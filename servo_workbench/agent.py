"""Linux maintenance agent: fixed configuration and reviewed operations only."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
from .profiles import Refused
from .service import Controller
from .remote import PROTOCOL
from .server import MUTATIONS, READS


def trusted_config(path):
    config = Path(path)
    for node in (config, config.parent):
        info = node.lstat()
        if node.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('AGENT_CONFIG', '配置必须root拥有，且不可被其他用户写入')
    value = json.loads(config.read_text())
    if not isinstance(value, dict) or set(value) != {'schema', 'port', 'kind', 'state_dir'} or value['schema'] != 1:
        raise Refused('AGENT_CONFIG', '配置schema错误')
    if value['kind'] not in ('xl330', 'hd1910') or not isinstance(value['port'], str) or not value['port'].startswith('/dev/'):
        raise Refused('AGENT_CONFIG', '型号或固定串口错误')
    state = Path(value['state_dir'])
    if state != Path('/var/lib/microduck-servo-workbench'):
        raise Refused('AGENT_CONFIG', '状态目录不在固定路径')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.lstat()
    if state.is_symlink() or info.st_uid != 0 or info.st_mode & 0o077:
        raise Refused('AGENT_STATE', '状态目录必须root私有')
    root = Path(__file__).resolve().parents[1]
    for node in [root, *root.joinpath('servo_workbench').rglob('*.py')]:
        info = node.lstat()
        if node.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('AGENT_CODE', '代理源码必须root拥有且不可被其他用户修改')
    return value


def serve_stdio(controller, kind, source=sys.stdin, sink=sys.stdout):
    output_lock = threading.Lock()
    closed = threading.Event()

    def respond(request):
        ident = request.get('id')
        try:
            method = request.get('method')
            body = request.get('params', {})
            owner = request.get('owner', '')
            if not isinstance(ident, str) or len(ident) > 80 or not isinstance(owner, str) or len(owner) > 80 or not isinstance(body, dict):
                raise Refused('FRAME', '消息身份或参数错误')
            if method == 'hello':
                result = dict(protocol=PROTOCOL, component_version='0.1.0',
                              authority='board', kind=kind, hardware_acceptance=False)
            else:
                if method not in MUTATIONS | READS or method in ('simulation', 'report-import'):
                    raise Refused('METHOD', '操作不在白名单')
                if method in ('connect', 'session-create') and body.get('kind', kind) != kind:
                    raise Refused('PROFILE', '只允许本机配置的型号')
                if method == 'connect':
                    body = {**body, 'kind': kind, 'selection': None}
                if closed.is_set():
                    raise Refused('DISCONNECTED', 'SSH已断开；排队操作已冻结')
                result = controller.rpc(method, body, owner)
            reply = dict(id=ident, ok=True, result=result)
        except Exception as error:
            reply = dict(id=ident, ok=False,
                         code=error.code if isinstance(error, Refused) else 'AGENT',
                         error=str(error)[:1200])
        with output_lock:
            sink.write(json.dumps(reply, allow_nan=False) + '\n')
            sink.flush()

    # Heartbeat and cancellation remain available during a bounded action.
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = set()
            try:
                while True:
                    line = source.readline(2 * 1024 * 1024 + 1)
                    if not line:
                        break
                    if len(line) > 2 * 1024 * 1024 or not line.endswith('\n'):
                        raise Refused('FRAME', '消息过长或不完整')
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise Refused('FRAME', '消息必须是对象')
                    pending = {f for f in pending if not f.done()}
                    if len(pending) >= 8:
                        raise Refused('QUEUE', '在途请求过多')
                    pending.add(pool.submit(respond, request))
            finally:
                closed.set()
                if controller.engine:
                    controller.engine.disconnected.set()
                    controller.engine.cancel.set()
    finally:
        controller.close()


def main(config):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise Refused('AGENT_OS', '代理需要Linux root维护入口')
    cfg = trusted_config(config)
    controller = Controller(cfg['state_dir'], demo=False, board=True, board_port=cfg['port'])
    controller._engine(cfg['kind'], {'device': cfg['port']})
    serve_stdio(controller, cfg['kind'])
