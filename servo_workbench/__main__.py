"""Desktop entry point. Default is a simulated workflow, never native hardware."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import sys
import urllib.request
import webbrowser
from .profiles import Refused
from .service import Controller, default_state


def client(port, method, body=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    origin = 'http://127.0.0.1:' + str(port)
    opener.open(origin + '/', timeout=3).close()
    with opener.open(origin + '/api/servo-workbench/v1/session', timeout=3) as response:
        auth = json.load(response)
    headers = {'Content-Type': 'application/json', 'Origin': origin,
               'X-Workbench-CSRF': auth['csrf']}
    url = origin + '/api/servo-workbench/v1/'
    if body is not None:
        req = urllib.request.Request(url + 'claim', data=b'{}', headers=headers)
        opener.open(req, timeout=3).close()
    req = urllib.request.Request(url + method,
                                data=None if body is None else json.dumps(body).encode(),
                                headers=headers)
    with opener.open(req, timeout=240 if method=='scan' else 90) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='start',
                        choices=['start', 'serve', 'status', 'export', 'request', 'agent'])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--demo', action='store_true')
    mode.add_argument('--real', action='store_true')
    mode.add_argument('--ssh', help='已登记的SSH Host别名或user@host')
    parser.add_argument('--state-dir', type=Path, default=default_state())
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--open', action='store_true', help='打开本地浏览器')
    parser.add_argument('--method', default='status')
    parser.add_argument('--body', help='JSON对象；只用于明确的API操作')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--stdio', action='store_true')
    parser.add_argument('--config', type=Path, default=Path('/etc/microduck-servo-workbench/agent.json'))
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('端口需要1024..65535')
    if args.command == 'agent':
        if not args.stdio:
            parser.error('板端代理仅支持--stdio')
        from .agent import main as agent_main
        return agent_main(args.config)
    if args.command in ('status', 'export', 'request'):
        method = 'report' if args.command == 'export' else args.method
        body = json.loads(args.body) if args.body else None
        value = client(args.port, method, body)
        if args.command == 'export':
            if not args.out:
                parser.error('export需要--out文件路径')
            if args.out.exists():
                parser.error('输出已存在，避免覆盖证据')
            args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2))
            print(json.dumps(dict(output=str(args.out))))
        else:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    if args.command == 'start' and not (args.demo or args.real or args.ssh):
        if sys.stdin.isatty():
            print('Duck Servo Workbench\n1 模拟演练（无硬件）\n2 电脑USB直连\n3 已登记SSH板端')
            choice = input('选择 [1]: ').strip() or '1'
            if choice == '2':
                args.real = True
            elif choice == '3':
                args.ssh = input('SSH Host别名或user@host: ').strip()
                if not args.ssh:
                    parser.error('SSH目标不能为空')
            elif choice != '1':
                parser.error('未知选择')
        else:
            print('非交互启动：默认模拟模式；真实串口需显式--real。')
    from .server import Server
    if args.ssh:
        from .remote import Remote
        controller = Remote(args.ssh)
    else:
        controller = Controller(args.state_dir, demo=not args.real)
    try:
        server = Server(('127.0.0.1', args.port), controller)
    except Exception:
        controller.close()
        raise
    address = 'http://127.0.0.1:' + str(server.server_port)
    print(('LIVE（只读连接需在界面明确选择）' if args.real or args.ssh else 'SIMULATION / NO HARDWARE') +
          ' · ' + address, flush=True)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()


if __name__ == '__main__':
    try:
        main()
    except (Refused, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
