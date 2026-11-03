"""Loopback desktop UI. No serial port is opened by this module."""
import http.cookies
import json
from pathlib import Path
import secrets
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs
from .profiles import Refused

PREFIX = '/api/servo-workbench/v1/'
STATIC = Path(__file__).parent / 'static'
MUTATIONS = {'session-create', 'session-open', 'connect', 'claim', 'plan', 'action-plan',
             'apply', 'heartbeat', 'cancel', 'pause', 'resume', 'detect', 'scan',
             'reconcile', 'verify', 'simulation', 'report-import'}
READS = {'status', 'capabilities', 'ports', 'report', 'report-list', 'report-get',
         'events', 'job', 'checks'}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller):
        if address[0] != '127.0.0.1':
            raise ValueError('desktop server must bind IPv4 loopback')
        self.controller = controller
        self.sessions = {}
        self.sessions_lock = threading.Lock()
        self.streams = threading.BoundedSemaphore(2)
        super().__init__(address, Handler)

    def session(self, token):
        with self.sessions_lock:
            now = time.monotonic()
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
            return self.sessions.get(token)

    def new_session(self):
        with self.sessions_lock:
            if len(self.sessions) >= 64:
                raise Refused('SESSIONS', '打开的浏览器过多')
            token = secrets.token_urlsafe(32)
            self.sessions[token] = dict(csrf=secrets.token_urlsafe(32),
                                        owner=secrets.token_hex(16),
                                        expires=time.monotonic() + 8 * 3600)
            return token


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass  # Never record cookies or request bodies.

    def _host(self):
        host = self.headers.get('Host', '')
        if host not in {f'127.0.0.1:{self.server.server_port}',
                        f'localhost:{self.server.server_port}'}:
            raise Refused('HOST', 'Host不在本地白名单')
        return host

    def _identity(self, write=False):
        host = self._host()
        origin = self.headers.get('Origin')
        if (origin and origin != 'http://' + host) or (write and origin != 'http://' + host):
            raise Refused('ORIGIN', '拒绝跨站请求')
        cookies = http.cookies.SimpleCookie()
        cookies.load(self.headers.get('Cookie', ''))
        token = cookies.get('workbench_session')
        session = self.server.session(token.value if token else '')
        if not session:
            raise Refused('AUTH', '请重新打开工作台首页')
        if write and not secrets.compare_digest(self.headers.get('X-Workbench-CSRF', ''),
                                                 session['csrf']):
            raise Refused('CSRF', '请求令牌错误')
        return session

    def _send(self, status, value, content_type='application/json; charset=utf-8', cookie=None):
        raw = value if isinstance(value, bytes) else json.dumps(
            value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "object-src 'none'; frame-ancestors 'none'")
        if cookie:
            self.send_header('Set-Cookie', 'workbench_session=' + cookie +
                             '; Path=/; HttpOnly; SameSite=Strict')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, error):
        code = error.code if isinstance(error, Refused) else 'UNAVAILABLE'
        status = 401 if code == 'AUTH' else 403 if code in ('HOST', 'ORIGIN', 'CSRF') else (
            409 if isinstance(error, Refused) else 503)
        self._send(status, dict(error=str(error)[:1200], code=code))

    def do_GET(self):
        try:
            self._host()
            path = urlsplit(self.path).path
            if path == '/':
                cookies = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
                current = cookies.get('workbench_session')
                token = None if current and self.server.session(current.value) else self.server.new_session()
                return self._send(200, (STATIC / 'index.html').read_bytes(),
                                  'text/html; charset=utf-8', token)
            if path in ('/app.js', '/style.css'):
                return self._send(200, (STATIC / path[1:]).read_bytes(),
                                  'text/javascript; charset=utf-8' if path.endswith('.js')
                                  else 'text/css; charset=utf-8')
            session = self._identity()
            if path == PREFIX + 'session':
                return self._send(200, dict(csrf=session['csrf'], owner=session['owner']))
            if path == PREFIX + 'stream':
                return self._stream(session)
            if not path.startswith(PREFIX) or path[len(PREFIX):] not in READS:
                return self._send(404, dict(error='未找到'))
            args = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
            if 'after' in args:
                args['after'] = int(args['after'])
            self._send(200, self.server.controller.rpc(path[len(PREFIX):], args, session['owner']))
        except Exception as error:
            self._error(error)

    def _stream(self, session):
        if not self.server.streams.acquire(False):
            return self._send(429, dict(error='最多两个事件显示端；可使用状态轮询'))
        try:
            self.connection.settimeout(5)
            after = int(parse_qs(urlsplit(self.path).query).get('after', ['0'])[-1])
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            for _ in range(120):
                events = self.server.controller.rpc('events', {'after': after}, session['owner'])
                if events:
                    after = events[-1]['seq']
                    self.wfile.write(('id: ' + str(after) + '\ndata: ' +
                                      json.dumps(events, allow_nan=False) + '\n\n').encode())
                else:
                    self.wfile.write(b': waiting\n\n')
                self.wfile.flush()
                time.sleep(1)
        except (OSError, Refused):
            pass
        finally:
            self.close_connection = True
            self.server.streams.release()

    def do_POST(self):
        try:
            session = self._identity(True)
            path = urlsplit(self.path).path
            if not path.startswith(PREFIX) or path[len(PREFIX):] not in MUTATIONS:
                return self._send(404, dict(error='操作不在白名单'))
            length = self.headers.get('Content-Length', '')
            if not length.isdigit() or not 0 < int(length) <= 2 * 1024 * 1024:
                raise Refused('SIZE', '请求长度错误或超过2MiB')
            if self.headers.get('Transfer-Encoding'):
                raise Refused('SIZE', '不接受分块请求')
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise Refused('JSON', '请求不完整')
            body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError('非法JSON数值')))
            self._send(200, self.server.controller.rpc(path[len(PREFIX):], body, session['owner']))
        except Exception as error:
            self._error(error)
