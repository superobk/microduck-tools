"""Deterministic power/USB/ACK fault model. No serial imports or native clients."""
from .profiles import profile, BAUDS, Refused
from .store import clone


class Offline(ConnectionError):
    pass


class SimBus:
    simulated=True
    def __init__(self,kind="hd1910"):
        self.kind=kind
        self.devices={}
        self.attached=[]
        self.usb=True
        self.busy=False
        self.robotd_active=False
        self.adapter=dict(vid=0x0403,pid=0x6014,serial_number="SIM-ONLY",location="sim-usb",
                          device="SIM0",synthetic=True)
        self.sent=[]
        self.fail_at=None
        self.failure="after"
        self.sends=0
        self.mid_noop=False
        self.closed=False
        self.connection_epoch=0;self.pinned_epoch=None;self.pre_write_guard=None
        self.create("A")

    def create(self,label,ident=1):
        p=profile(self.kind)
        if self.kind=="xl330":
            r={a:0 for a in p["registers"]}
            r.update({0:1200,6:52,7:ident,8:1,9:250,11:3,12:255,31:70,32:70,34:35,
                      36:885,38:1750,48:4095,52:0,62:140,63:53,68:2,84:400,
                      100:885,108:1,112:5,116:2048,132:2048,144:50,146:25})
        else:
            r={a:0 for a in p["registers"]}
            r.update({0:3,1:46,2:0,3:1910,5:ident,6:0,7:ident,8:1,9:0,11:4095,
                      13:80,14:100,15:40,16:1000,19:4,21:5,22:10,28:77,33:4,
                      41:1,42:2048,44:77,46:100,48:1000,50:5,51:10,55:1,56:2048,
                      62:74,63:25,67:2048})
        self.devices[label]=dict(raw=r.copy(),persistent=r.copy(),label=label)

    def attach(self,label=None):
        if label is not None and label not in self.devices:self.create(label)
        self.attached=[] if label is None else [label]

    def power_cycle(self):
        for label in self.attached:
            d=self.devices[label];p=profile(self.kind)
            # RAM reloads, persistent registers retain their state.
            r=d["raw"]
            for a in p["registers"]:
                if a < (64 if self.kind=="xl330" else 40):r[a]=d["persistent"][a]
            r[p["torque"]]=0
            if self.kind=="hd1910":r[55]=1;r[50]=r[21];r[51]=r[22]

    def _device(self,ident,baud):
        if not self.usb:raise Offline("USB适配器未连接")
        if self.busy or self.robotd_active:raise Refused("PORT_BUSY","端口或正式服务占用")
        found=[self.devices[x] for x in self.attached
               if self.devices[x]["raw"][profile(self.kind)["id_addr"]]==ident
               and BAUDS[self.kind].get(self.devices[x]["raw"][profile(self.kind)["baud_addr"]])==baud]
        if len(found)>1:raise Refused("DUPLICATE","多个模拟设备使用相同ID")
        if not found:raise Offline("等待舵机回应；不能据此判断已断电")
        return found[0]

    def read(self,ident,baud,addr,size):
        d=self._device(ident,baud)
        if self.closed:raise Offline("连接已关闭")
        return d["raw"].get(addr,0)

    def write(self,ident,baud,addr,size,value,purpose="configuration"):
        if self.closed:raise Offline('模拟连接已关闭')
        if self.pre_write_guard:self.pre_write_guard()
        self.sends+=1
        if self.fail_at==self.sends and self.failure=="before":
            raise Offline("模拟发包前断流")
        d=self._device(ident,baud);r=d["raw"];p=profile(self.kind)
        if addr < (64 if self.kind=="xl330" else 40) and r[p["torque"]]!=0:
            raise Refused("TORQUE","EEPROM写入时扭矩未关闭")
        self.sent.append(dict(id=ident,baud=baud,address=addr,value=value,purpose=purpose))
        r[addr]=value
        if addr < (64 if self.kind=="xl330" else 40):
            if self.kind=="xl330" or r[55]==0:d["persistent"][addr]=value
        if self.kind=="hd1910" and addr==5:
            r[7]=value
            if r[55]==0:d["persistent"][7]=value
        if purpose=="motion" and addr==(116 if self.kind=="xl330" else 42):
            r[p["position"]]=value
            if self.kind=="hd1910":r[67]=value
        if self.fail_at==self.sends and self.failure=="after":
            raise Offline("模拟已发包、应答前断流")

    def reset_mid(self,ident,baud):
        if self.closed:raise Offline('模拟连接已关闭')
        if self.pre_write_guard:self.pre_write_guard()
        d=self._device(ident,baud)
        self.sent.append(dict(id=ident,instruction=11,purpose="midpoint"))
        if not self.mid_noop:
            r=d["raw"];r[31]=(r[56]-2048)&0xffff;r[56]=2048
            d["persistent"][31]=r[31]

    def close(self):
        self.closed=True

    def reconnect(self):
        if self.closed and self.pinned_epoch is not None:raise Offline('事务内禁止重连')
        if self.closed:self.connection_epoch+=1
        self.closed=False

    def begin_transaction(self,epoch):
        if self.closed or self.pinned_epoch is not None or self.connection_epoch!=epoch:
            raise Offline('预览对应的模拟连接已变化')
        self.pinned_epoch=epoch

    def end_transaction(self):
        self.pinned_epoch=None;self.pre_write_guard=None

    def ports(self):
        return [clone(self.adapter)] if self.usb else []
