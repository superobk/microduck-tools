"""One bounded serial owner, stable USB selection and Linux maintenance ownership."""
import os
from pathlib import Path
import stat
import subprocess
import time
from collections import deque
from .profiles import profile, Refused, NoAck
from .protocol import packet,Decoder


def list_ports():
    try:
        from serial.tools.list_ports import comports
    except ImportError as e:
        raise Refused("DEPENDENCY","真实串口需要 pyserial==3.5；模拟模式不需要") from e
    rows=[dict(device=p.device,vid=p.vid,pid=p.pid,serial_number=p.serial_number,
               location=p.location,description=p.description) for p in comports()]
    devices={p["device"] for p in rows}
    # macOS reports two names for the same UART; prefer the callout endpoint.
    return [p for p in rows if not (p["device"].startswith("/dev/tty.") and
                                  p["device"].replace("/dev/tty.","/dev/cu.",1) in devices)]


def select_port(saved,rows,reconnecting=False):
    if saved.get("serial_number"):
        matches=[p for p in rows if all(p.get(k)==saved.get(k) for k in ("vid","pid","serial_number"))]
        if len(matches)>1 and saved.get("location"):
            matches=[p for p in matches if p.get("location")==saved["location"]]
    else:
        if reconnecting:
            raise Refused("ADAPTER_AMBIGUOUS","适配器没有可靠序列号；请确认新的端口选择后恢复")
        matches=[p for p in rows if p.get("device")==saved.get("device")]
    if len(matches)>1:raise Refused("ADAPTER_AMBIGUOUS","多个USB候选；请重新选择，不能猜测")
    if not matches:raise ConnectionError("等待USB适配器重新出现")
    return matches[0]


def linux_guard(port,own_fd=None):
    """No stop/start calls. Root is needed to inspect every possible bus owner."""
    if os.name!="posix" or not Path("/proc").is_dir():
        raise Refused("BOARD_OS","板端维护只支持Linux")
    if os.geteuid()!=0:raise Refused("BOARD_ROOT","板端独占检查需要root；不授予常驻Web任意sudo")
    r=subprocess.run(["systemctl","show","robotd.service","-p","ActiveState","--value"],
                     text=True,capture_output=True,timeout=2,check=True)
    if r.stdout.strip() not in ("inactive","failed"):
        raise Refused("ROBOTD_ACTIVE","robotd运行或状态未知；需现场人员先进行维护交接")
    target=Path(port).stat()
    if not stat.S_ISCHR(target.st_mode):raise Refused("PORT","配置的串口不是字符设备")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():continue
        try:
            if (proc/"comm").read_text().strip()=="robotd":
                raise Refused("ROBOTD_PROCESS","发现手动启动的robotd")
            for fd in (proc/"fd").iterdir():
                if int(proc.name)==os.getpid() and own_fd is not None and fd.name==str(own_fd):continue
                try:info=fd.stat()
                except FileNotFoundError:continue
                if info.st_rdev==target.st_rdev and stat.S_ISCHR(info.st_mode):
                    raise Refused("PORT_BUSY","其他进程持有串口")
        except (FileNotFoundError,ProcessLookupError):continue


class SerialBus:
    simulated=False
    def __init__(self,kind,selection,board=False,ports_fn=list_ports,serial_factory=None,guard=None):
        self.kind=kind;self.selection=dict(selection);self.board=board
        self.ports_fn=ports_fn;self.serial_factory=serial_factory
        self.guard=guard or linux_guard
        self.serial=None;self.reconnecting=False;self.current_baud=None
        self.sent=deque(maxlen=500);self.bad_frames=0

    def ports(self):return self.ports_fn()

    def reconnect(self):
        if self.serial is not None:return
        row=({"device":self.selection["device"]} if self.board else
             select_port(self.selection,self.ports_fn(),self.reconnecting))
        if self.board:self.guard(row["device"])
        factory=self.serial_factory
        if factory is None:
            try:
                import serial
            except ImportError as e:raise Refused("DEPENDENCY","请安装固定pyserial依赖") from e
            factory=serial.Serial
        kwargs=dict(port=None,baudrate=1000000,timeout=.015,write_timeout=.1,
                    xonxoff=False,rtscts=False,dsrdtr=False)
        if os.name=="posix":kwargs["exclusive"]=True
        serial_port=factory(**kwargs)
        try:
            serial_port.dtr=False;serial_port.rts=False
            serial_port.port=row["device"];serial_port.open()
            if os.name=="posix" and self.serial_factory is None:
                import fcntl,termios
                fcntl.ioctl(serial_port.fileno(),termios.TIOCEXCL)
            if self.board:self.guard(row["device"],serial_port.fileno())
            serial_port.reset_input_buffer();serial_port.reset_output_buffer()
        except BaseException:
            serial_port.close();raise
        self.serial=serial_port;self.selection={**self.selection,**row}
        self.current_baud=1000000;self.reconnecting=False

    def close(self):
        if self.serial is not None:
            try:self.serial.reset_output_buffer()
            except OSError:pass
            try:self.serial.close()
            finally:self.serial=None
        self.reconnecting=True

    def _exchange(self,ident,baud,instruction,params,expected=None,write=False):
        self.reconnect()
        try:
            if self.board:self.guard(self.selection["device"],self.serial.fileno())
            if baud!=self.current_baud:
                self.serial.baudrate=baud;self.current_baud=baud
            self.serial.reset_input_buffer()
            codec=profile(self.kind)["protocol"]
            raw=packet(codec,ident,instruction,params)
            if self.serial.write(raw)!=len(raw):
                self.close();raise ConnectionError("串口仅发送了部分数据；结果未知，禁止重发")
            self.sent.append(dict(hex=raw.hex(),instruction=instruction,id=ident,write=write))
            decoder=Decoder(codec);deadline=time.monotonic()+.07
            while time.monotonic()<deadline:
                for answer in decoder.feed(self.serial.read(256)):
                    if answer["id"]!=ident:continue
                    error=answer["error"] & (0x7f if codec=="dxl2" else 0xff)
                    self.last_status=answer["error"]
                    if error and write:raise Refused("DEVICE_STATUS",f"ID{ident} 应答状态0x{answer['error']:02x}")
                    if expected is not None and len(answer["data"])!=expected:continue
                    self.bad_frames+=decoder.bad
                    return answer["data"]
            if write:
                raise NoAck("写应答缺失；只允许同连接读回核对，不重发")
            raise ConnectionError("等待舵机回应；不等同于确认断电")
        except OSError:
            self.close();raise

    def read(self,ident,baud,addr,size):
        p=profile(self.kind)
        if addr not in p["registers"] or size!=p["registers"][addr][0]:
            raise Refused("READ_ALLOWLIST","读取不在型号寄存器表")
        params=(addr.to_bytes(2,"little")+size.to_bytes(2,"little")
                if p["protocol"]=="dxl2" else bytes([addr,size]))
        return int.from_bytes(self._exchange(ident,baud,2,params,expected=size),"little")

    def write(self,ident,baud,addr,size,value,purpose="configuration"):
        p=profile(self.kind)
        configuration=({7,8,9,11,20,62,63} if self.kind=="xl330" else
                       {5,6,19,21,22,28,33,55})
        motion=({64,98,100,108,112,116} if self.kind=="xl330" else
                {40,41,42,44,46,48})
        permitted=(addr in configuration if purpose=="configuration" else
                   addr in motion if purpose=="motion" else
                   (addr==p["torque"] and value==0 or self.kind=="hd1910" and addr==55 and value==1)
                   if purpose=="cleanup" else False)
        if not permitted or addr not in p["registers"] or size!=p["registers"][addr][0]:
            raise Refused("WRITE_ALLOWLIST","拒绝未获类型化操作授权的寄存器写入")
        if type(value) is not int or not 0<=value<1<<(size*8):raise Refused("VALUE","原始寄存器值越界")
        address=addr.to_bytes(2,"little") if p["protocol"]=="dxl2" else bytes([addr])
        self._exchange(ident,baud,3,address+value.to_bytes(size,"little"),expected=0,write=True)

    def reset_mid(self,ident,baud):
        if self.kind!="hd1910":raise Refused("MIDPOINT","XL330不使用Feetech中位指令")
        self._exchange(ident,baud,0x0b,b"",expected=0,write=True)
