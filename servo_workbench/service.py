"""Local controller shared by CLI, browser and fixed SSH stdio agent."""
import json
import os
from pathlib import Path
import threading
import time
from .engine import Engine
from .store import Store,AuthorityLock
from .profiles import Refused,public_profiles,profile
from .simulation import SimBus
from .transport import SerialBus,list_ports


def default_state():
    if os.name=="nt":
        return Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "Microduck" / "ServoWorkbench"
    if os.uname().sysname=="Darwin":
        return Path.home()/"Library/Application Support/Microduck/ServoWorkbench"
    return Path.home()/".local/share/microduck-servo-workbench"


class Controller:
    def __init__(self,directory,demo=True,board=False,board_port=None):
        self.directory=Path(directory);self.authority_lock=AuthorityLock(directory)
        try:self.store=Store(directory)
        except BaseException:self.authority_lock.close();raise
        self.demo=demo;self.board=board;self.board_port=board_port
        self.engine=None;self.stop_event=threading.Event();self.worker=None
        self.control_lock=threading.Lock()
        self.sim_lock=threading.Lock()
        self.report_dir=self.directory/"reports";self.report_dir.mkdir(exist_ok=True)
        self.selection=None
        sessions=self.store.list()
        if sessions:
            matching=[s for s in sessions if s.get('simulated') is bool(demo)]
            kind=matching[0]['kind'] if matching else sessions[0]["kind"]
            if demo:self._engine(kind)
            elif not board:
                saved=self.store.meta('usb_binding')
                if saved and saved['selection'].get('serial_number'):
                    self.selection=saved['selection'];self._engine(saved['kind'],self.selection)
        self.worker=threading.Thread(target=self._worker,daemon=True);self.worker.start()

    def _engine(self,kind,selection=None):
        profile(kind)
        if self.engine and self.engine.bus.kind==kind and selection is None:return self.engine
        if self.engine:
            if not self.engine.lock.acquire(False):raise Refused("BUSY","先完成或取消当前作业")
            try:self.engine.bus.close()
            finally:self.engine.lock.release()
        if self.demo:
            bus=SimBus(kind);bus.attach(None)
            path=self.directory/("simulation-"+kind+".json")
            if path.exists():
                value=json.loads(path.read_text())
                bus.devices={k:dict(raw={int(a):v for a,v in d["raw"].items()},
                                    persistent={int(a):v for a,v in d["persistent"].items()},label=k)
                             for k,d in value["devices"].items()}
                bus.attached=value["attached"];bus.usb=value["usb"]
                bus.adapter=value["adapter"]
        else:
            selection=selection or self.selection
            if self.board:
                selection={"device":self.board_port}
            if not isinstance(selection,dict) or not selection.get("device"):
                raise Refused("PORT_SELECT","请明确选择串口；不会自动访问设备")
            bus=SerialBus(kind,selection,board=self.board)
        self.engine=Engine(self.store,bus,authority="board" if self.board else "computer")
        return self.engine

    def _persist_sim(self):
        with self.sim_lock:
            self._persist_sim_locked()

    def _persist_sim_locked(self):
        if not self.demo or not self.engine:return
        bus=self.engine.bus
        path=self.directory/("simulation-"+bus.kind+".json")
        raw=json.dumps(dict(devices=bus.devices,attached=bus.attached,usb=bus.usb,adapter=bus.adapter))
        temporary=path.with_suffix(".tmp")
        with open(temporary,"w") as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)

    def _worker(self):
        while not self.stop_event.wait(.2):
            engine=self.engine
            if engine:
                try:
                    with self.control_lock:
                        if engine is self.engine:engine.tick()
                except Exception as e:
                    self.worker_error=str(e)  # visible status, never controls native services

    def rpc(self,method,body=None,owner="cli"):
        b=body or {}
        if not isinstance(b,dict):raise Refused("JSON","请求必须是对象")
        if method=="capabilities":return public_profiles()
        if method=="status":
            out=self.engine.status() if self.engine else dict(session=None,sessions=self.store.list())
            return {**out,"demo":self.demo,"agent_state":"online","writer_owned":bool(self.engine and self.engine.writer==owner and self.engine.clock()<self.engine.writer_until),"connected_read_only":bool(self.engine and not self.demo),"worker_error":getattr(self,"worker_error",None)}
        if method=="ports":
            if self.demo:return self.engine.bus.ports() if self.engine else []
            if self.board:return [dict(device=self.board_port,board_uart=True)]
            return list_ports()
        if method=="report-list":
            return [dict(id=p.stem) for p in sorted(self.report_dir.glob("*.json"))]
        if method=="report-import":
            return self.import_report(b.get("report"))
        if method=="report-get":
            ident=b.get("id","")
            if len(ident)!=64 or any(c not in "0123456789abcdef" for c in ident):raise Refused("REPORT","报告ID错误")
            return json.loads((self.report_dir/(ident+".json")).read_text())
        if method=="session-create":
            if self.engine and self.engine.writer and self.engine.writer!=owner and self.engine.clock()<self.engine.writer_until:
                raise Refused("VIEW_ONLY","另一操作端正在使用")
            with self.control_lock:
                e=self._engine(b.get("kind","hd1910"))
                result=e.create(b.get("kind","hd1910"),b.get("layout","microduck"),
                                b.get("version","0.18.3"),b.get("options"))
                e.claim(owner);self._persist_sim();return result
        if method=="session-open":
            with self.control_lock:
                if self.engine and self.engine.writer and self.engine.writer!=owner and self.engine.clock()<self.engine.writer_until:
                    raise Refused("VIEW_ONLY","另一操作端正在使用，不能替换其连接")
                s=self.store.get(b.get("id",""))
                if s.get('simulated') is not self.demo:raise Refused('MODE','模拟与真实批次不能相互执行')
                e=self._engine(s["kind"])
                if e.writer and e.writer!=owner and e.clock()<e.writer_until:
                    raise Refused("VIEW_ONLY","另一操作端正在使用")
                result=e.select(s["id"]);return result
        if method=="connect":
            if self.demo:raise Refused("MODE","模拟模式不连接真实串口")
            with self.control_lock:
                kind=b.get("kind","hd1910");profile(kind)
                if self.engine:
                    self.engine.require_writer(owner)
                selection=b.get("selection")
                if self.board:selection={"device":self.board_port}
                self.selection=selection
                e=self._engine(kind,selection);e.claim(owner)
                if not self.board:self.store.meta('usb_binding',dict(kind=kind,selection=selection))
                return dict(connected_read_only=True,ports_selection=selection)
        if not self.engine:raise Refused("SESSION","请先建立批次；真实模式需先选连接")
        e=self.engine
        if method=="claim":return e.claim(owner)
        if method=="events":
            if type(b.get("after",0)) is not int or b.get("after",0)<0:raise Refused("CURSOR","事件游标错误")
            return self.store.events(e.active,b.get("after",0)) if e.active else []
        if method=="report":return e.report()
        if method=="checks":return e.checks()
        if method=="job":
            job=self.store.job(b.get("id",""))
            if not job or job["session"]!=e.active:raise Refused("JOB","该批次无此作业")
            return job
        e.require_writer(owner)
        try:
            if method=="plan":
                return e.plan(index=b.get("index"),label=b.get("label",""),
                              model_confirmed=b.get("model_confirmed") is True,
                              same_servo=b.get("same_servo") is True)
            if method=="action-plan":
                return e.action_plan(b.get("kind"),b.get("model_confirmed") is True,
                                     b.get("standalone") is True,b.get("operator_ready") is True)
            if method=="apply":return e.apply(b)
            if method=="heartbeat":return e.heartbeat()
            if method=="cancel":return e.cancel_action()
            if method=="pause":return e.pause(True)
            if method=="resume":return e.pause(False)
            if method=="detect":return e.detect()
            if method=="scan":return e.scan(b.get('bauds'))
            if method=="reconcile":
                return e.reconcile(b.get("same_servo") is True,b.get("model_confirmed") is True)
            if method=="verify":
                return e.verify_all(b.get("power_cycle_confirmed") is True,b.get("model_confirmed") is True)
            if method=="simulation":
                if not self.demo:raise Refused("MODE","真实模式没有模拟控制")
                bus=e.bus;op=b.get("op")
                if op=="attach":
                    label=b.get("label","")
                    if not isinstance(label,str) or not 1<=len(label)<=80:raise Refused("LABEL","模拟标签错误")
                    bus.attach(label)
                elif op=="detach":bus.attach(None)
                elif op=="power-cycle":bus.power_cycle()
                elif op=="all":bus.attached=list(bus.devices)
                elif op=="usb-off":bus.usb=False;bus.close()
                elif op=="usb-on":bus.usb=True;bus.adapter["device"]="SIM-renamed";bus.reconnect()
                elif op=="fault":
                    n=b.get("at")
                    if n is not None and (type(n) is not int or n<1):raise Refused("FAULT","故障序号错误")
                    bus.fail_at=n
                else:raise Refused("SIMULATION","未知模拟操作")
                e.retry_at=0;return dict(simulated=True,attached=list(bus.attached))
            raise Refused("METHOD","操作不在工作台白名单")
        finally:
            if method in ('apply','reconcile','verify','simulation','pause','resume'):
                self._persist_sim()

    def import_report(self,value):
        from .reports import validate
        from .profiles import digest
        result=validate(value)
        ident=digest(result)
        path=self.report_dir/(ident+".json")
        if not path.exists():
            with open(path.with_suffix(".tmp"),"w") as f:
                json.dump(result,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
            os.replace(path.with_suffix(".tmp"),path)
        return dict(id=ident,historical=True,identity="imported_claim_not_verified")

    def close(self):
        self.stop_event.set()
        if self.engine:self.engine.cancel.set()
        self.worker.join(timeout=3)
        # The caller must first wait for an executing job; do not close its DB underneath it.
        if self.engine:
            with self.engine.lock:
                self._persist_sim();self.engine.bus.close()
        self.store.close()
        self.authority_lock.close()
