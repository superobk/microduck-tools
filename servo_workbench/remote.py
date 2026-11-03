"""Fixed SSH stdio transport. Uncertain writes are never retried."""
import json
import queue
import re
import subprocess
import threading
import time
import uuid
from .profiles import Refused

COMMAND = 'sudo -n -- /opt/microduck-servo-workbench/current/agent.sh --stdio'
PROTOCOL = 'microduck-servo-agent/v1'


class Remote:
    demo = False

    def __init__(self, host, popen=subprocess.Popen):
        if not isinstance(host, str) or not re.fullmatch(
                r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*', host):
            raise Refused('SSH_HOST', '请使用SSH Host别名或user@host，不接受额外参数')
        self.host = host
        self.popen = popen
        self.proc = None
        self.pending = {}
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.remote_info = None
        self.retry_at = 0.
        self.failures = 0

    def _reader(self, proc):
        try:
            while True:
                line = proc.stdout.readline(3 * 1024 * 1024 + 1)
                if not line or len(line) > 3 * 1024 * 1024 or not line.endswith('\n'):
                    raise ConnectionError('SSH代理断开或消息不完整；写入结果需核对')
                reply = json.loads(line)
                with self.lock:
                    if self.proc is not proc:
                        return  # A delayed old reader cannot consume the new connection's replies.
                    waiter = self.pending.pop(reply.get('id'), None)
                if waiter:
                    waiter.put(reply)
        except Exception as error:
            with self.lock:
                if self.proc is not proc:
                    return
                self.proc = None
                waiters = list(self.pending.values())
                self.pending.clear()
            for waiter in waiters:
                waiter.put(error)

    def _connect(self):
        with self.connect_lock:
            with self.lock:
                if self.proc and self.proc.poll() is None and self.remote_info:
                    return
                if time.monotonic() < self.retry_at:
                    raise Refused('SSH_WAIT', '等待已登记板端重新上线')
                self.remote_info = None
                self.proc = self.popen(
                    ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                     '-o', 'ConnectTimeout=3', '-o', 'ServerAliveInterval=2',
                     '-o', 'ServerAliveCountMax=2', '--', self.host, COMMAND],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1)
                threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            info = self._call('hello', {}, 'connection', timeout=8, connect=False)
            if info.get('protocol') != PROTOCOL or info.get('component_version') != '0.1.2' or info.get('authority') != 'board':
                self.close()
                raise Refused('AGENT_VERSION', '板端代理版本不匹配；不会自动部署或升级')
            self.remote_info = info
            self.failures = 0

    def _call(self, method, body, owner, timeout=12, connect=True):
        try:
            if connect:
                self._connect()
            ident = uuid.uuid4().hex
            waiter = queue.Queue(maxsize=1)
            with self.lock:
                proc = self.proc
                if not proc:
                    raise ConnectionError('SSH尚未连接')
                self.pending[ident] = waiter
            with self.write_lock:
                proc.stdin.write(json.dumps(dict(id=ident, method=method, params=body,
                                                owner=owner), allow_nan=False) + '\n')
                proc.stdin.flush()
            reply = waiter.get(timeout=timeout)
            if isinstance(reply, Exception):
                raise reply
            if not reply.get('ok'):
                raise Refused(reply.get('code', 'AGENT'), reply.get('error', '代理拒绝'))
            return reply['result']
        except (OSError, ConnectionError, queue.Empty) as error:
            self.failures += 1
            self.retry_at = time.monotonic() + min(5, .5 * 2 ** min(self.failures, 4))
            self.close()
            raise Refused('SSH_UNCERTAIN', '回复中断或超时；只读重连后核对，不重发写入') from error
        finally:
            if 'ident' in locals():
                with self.lock:
                    self.pending.pop(ident, None)

    def rpc(self, method, body=None, owner='cli'):
        if method == 'status':
            try:
                value = self._call(method, body or {}, owner)
                return {**value, 'agent_state': 'online', 'remote': self.host}
            except Refused as error:
                return dict(session=None, agent_state='reconnecting', remote=self.host,
                            error=str(error), code=error.code, remote_authority=True)
        return self._call(method, body or {}, owner,
                          timeout=240 if method=='scan' else 90 if method=='verify' else 45 if method=='apply' else 12)

    def close(self):
        with self.lock:
            proc = self.proc
            self.proc = None
            self.remote_info = None
            waiters = list(self.pending.values())
            self.pending.clear()
        for waiter in waiters:
            try:
                waiter.put_nowait(ConnectionError('连接已关闭'))
            except queue.Full:
                pass
        if proc:
            try:
                if proc.poll() is None:
                    proc.terminate()
                proc.stdin.close()
            except OSError:
                pass
