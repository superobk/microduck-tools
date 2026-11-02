"""Single-owner, durable workflow. Reconnection is always read-only."""
import contextlib
import json
import threading
import time

from .profiles import (Refused, profile, identity, metrics, layout, options, desired,
                       digest, integer, finite, VERSIONS, IDS, public_profiles, BAUDS, NoAck)
from .profiles import PERSISTENT
from .store import new_id, clone
from .actions import Actions


class Engine(Actions):
    def __init__(self,store,bus,clock=time.monotonic,authority="computer",fault_hook=None):
        self.store=store;self.bus=bus;self.clock=clock;self.authority=authority
        self.lock=threading.Lock();self.cancel=threading.Event()
        self.disconnected=threading.Event()
        self.writer_lock=threading.Lock();self.tx_epoch=None;self.seen_bus_epoch=None
        self.candidate=None;self.stable=None;self.connected=False;self.last_seen=None
        self.fault_hook=fault_hook or (lambda stage:None)
        self.active=None;self.retry_at=0.;self.wait_started=clock();self.attempt=0
        self.writer=None;self.writer_until=0.
        for s in store.list():
            if s["kind"]!=bus.kind or s.get('simulated') is not bool(bus.simulated):continue
            s["connection_epoch"]+=1;s["plan"]=None;s["connection_state"]="waiting"
            unfinished=store.unfinished(s["id"])
            if unfinished and not s.get("running_job"):s["running_job"]=unfinished[0]["id"]
            if unfinished or s.get("phase") in ("configuring","midpoint","testing","verifying_all","rolling_back"):
                s["phase"]="interrupted";s["needs_identity"]=True;s["plan"]=None
                s["connection_epoch"]+=1
                for slot in s["slots"]:
                    if slot["state"]=="configuring":slot["state"]="interrupted"
                store.save(s,"process_interrupted")
            store.save(s,"authority_reopened_read_only")
            if self.active is None:self.active=s["id"]

    def create(self,kind,robot_layout="microduck",version="0.18.3",opts=None):
        profile(kind)
        if version not in VERSIONS:raise Refused("VERSION","目标版本不受支持")
        if self.active and self.store.get(self.active).get("running_job"):
            raise Refused("BUSY","当前任务尚未核对完成")
        if self.bus.kind!=kind:raise Refused("PROFILE","先选择匹配的连接协议")
        s=dict(id=new_id(),kind=kind,layout=robot_layout,version=version,options=options(kind,opts),
               preset_hash=digest(dict(kind=kind,layout=robot_layout,version=version,
                                       options=options(kind,opts),facts=public_profiles())),
               authority=self.authority,simulated=bool(self.bus.simulated),
               hardware_acceptance=False,connection_epoch=1,revision=0,phase="waiting",
               connection_state="waiting",adapter_state="waiting",agent_state="online",
               cursor=0,paused=False,plan=None,needs_identity=False,running_job=None,
               slots=[{**row,"state":"pending","label":"","backup":None,"after":None,
                       "persistence":"not_checked","midpoint":None,"test":None} for row in layout(robot_layout)])
        self.store.save(s,"batch_created")
        self.active=s["id"];self.candidate=None;self.stable=None
        return self.status()

    def claim(self,owner):
        with self.writer_lock:
            now=self.clock()
            if self.writer and self.writer!=owner and (now<self.writer_until or self.lock.locked()):
                raise Refused("VIEW_ONLY","另一操作端拥有批次或仍有作业；当前仅可查看")
            self.writer=owner;self.writer_until=now+30
        return dict(owner=True,lease_s=30)

    def require_writer(self,owner):
        with self.writer_lock:
            if self.writer!=owner or self.clock()>=self.writer_until:
                raise Refused("WRITER","请先接管操作端")
            self.writer_until=self.clock()+30

    def _s(self):
        if not self.active:raise Refused("SESSION","请先创建或打开批次")
        return self.store.get(self.active)

    def select(self,ident):
        saved=self.store.get(ident)
        if saved['kind']!=self.bus.kind or saved.get('simulated') is not bool(self.bus.simulated):
            raise Refused('MODE','批次协议或模拟/真实模式与当前执行端不同')
        if not self.lock.acquire(False):raise Refused("BUSY","已有任务运行")
        try:
            self.active=ident;self.candidate=None;self.stable=None
            s=self._s();s["connection_epoch"]+=1;s["plan"]=None
            s["connection_state"]="waiting";self.store.save(s,"batch_opened")
        finally:self.lock.release()
        return self.status()

    def status(self):
        if not self.active:return dict(session=None,sessions=self.store.list(),component_version="0.1.1")
        s=self._s()
        out=clone(s)
        out["observed"]=clone(self.candidate)
        out["last_seen_age_s"]=None if self.last_seen is None else max(0,self.clock()-self.last_seen)
        out["completed"]=sum(x["state"]=="verified" for x in s["slots"])
        out["configured"]=sum(x["state"] in ("readback","verified") for x in s["slots"])
        out["sessions"]=[dict(id=x["id"],kind=x["kind"],layout=x["layout"],phase=x["phase"])
                         for x in self.store.list()]
        return out

    def _save_state(self,s,**values):
        if any(s.get(k)!=v for k,v in values.items()):
            s.update(values);self.store.save(s,"connection",values)

    def _lost(self,message,adapter="present"):
        s=self._s()
        if self.connected or self.candidate is not None or s.get("connection_state") in ("ready","stabilizing"):
            s["connection_epoch"]+=1;s["plan"]=None
        self.connected=False;self.candidate=None;self.stable=None
        self._save_state(s,connection_state="waiting",adapter_state=adapter,last_error=str(message),
                         agent_state="online")
        self.attempt+=1
        delay=(.5,1,2)[min(self.attempt-1,2)]
        if self.clock()-self.wait_started>60:delay=5
        self.retry_at=self.clock()+delay

    def snapshot(self,ident,baud,full=True,addresses=None):
        self.bus.reconnect()
        epoch=self.bus.connection_epoch
        p=profile(self.bus.kind);raw={};errors=[]
        identity_addrs=[0,6] if self.bus.kind=="xl330" else [0,1,2,3]
        for a in identity_addrs:
            raw[str(a)]=self.bus.read(ident,baud,a,p["registers"][a][0])
        ident_info=identity(self.bus.kind,raw)
        if full and ident_info["compatible"]:
            for a,(size,_) in p["registers"].items():
                if str(a) in raw:continue
                if addresses is not None and a not in addresses:continue
                try:raw[str(a)]=self.bus.read(ident,baud,a,size)
                except Refused as e:errors.append(f"{a}: {e}")
        if self.bus.connection_epoch!=epoch:raise ConnectionError('读取途中连接发生变化；快照作废')
        return dict(id=ident,baud=baud,raw=raw,identity=ident_info,read_errors=errors,
                    metrics=metrics(self.bus.kind,raw),sampled_at=time.time())

    def tick(self,force=False):
        if not self.active or not self.lock.acquire(False):return
        try:
            s=self._s()
            if s["paused"] or (not force and self.clock()<self.retry_at):return
            try:
                if hasattr(self.bus,"reconnect"):self.bus.reconnect()
                if self.seen_bus_epoch!=self.bus.connection_epoch:
                    if self.seen_bus_epoch is not None:self._lost('适配器连接已更新，重新核对')
                    self.seen_bus_epoch=self.bus.connection_epoch;s=self._s()
                p=profile(s["kind"])
                plans=s.get("plan") or (self.store.job(s["running_job"]) or {}).get("plan",{}) if s.get("running_job") else s.get("plan") or {}
                changes=plans.get('changes',[]);hint=s.get('discovery_hint') or {}
                ids=list(dict.fromkeys([plans.get("source_id",1),plans.get("target_id",1),hint.get('id',1),1,
                      s["slots"][min(s["cursor"],14)]["id"]]+
                      [c['value'] for c in changes if c['address']==p['id_addr']]+
                      [x["id"] for x in s["slots"] if x["state"]!="pending"]))
                speeds=list(dict.fromkeys([plans.get("source_baud",1000000),hint.get('baud',1000000),1000000]+
                        [BAUDS[s['kind']][c['value']] for c in changes if c['address']==p['baud_addr'] and c['value'] in BAUDS[s['kind']]]+
                        ([57600] if s["kind"]=="xl330" else [])))
                found=[];unsupported=[]
                for baud in speeds:
                    for ident in ids:
                        try:
                            snap=self.snapshot(ident,baud,False)
                            if snap["identity"]["compatible"]:found.append(snap)
                            else:unsupported.append(snap)
                        except (ConnectionError,OSError):continue
                if len(found)+len(unsupported)>1:raise Refused("MULTIPLE","检测到多个候选；初始化要求物理上只连接一个舵机")
                if not found and unsupported:raise Refused('IDENTITY','设备有回应，但型号/固件不在契约中：'+json.dumps(unsupported[0]['identity'],ensure_ascii=False))
                if not found:
                    self._lost("等待舵机回应；请核对供电和连接，工具不能判断电源是否关闭")
                    return
                snap=found[0];self.last_seen=self.clock();self.connected=True
                key=(snap["id"],snap["baud"],digest(snap["identity"]))
                changed=self.stable is None or self.stable["key"]!=key
                if changed:
                    if self.stable is not None:
                        s["connection_epoch"]+=1;s["plan"]=None
                    self.candidate=None
                    self.stable=dict(key=key,count=1,start=self.clock())
                else:self.stable["count"]+=1
                if changed or s["connection_state"]!="ready":
                    self._save_state(s,connection_state="stabilizing",adapter_state="present",last_error=None)
                if self.stable["count"]>=3 and self.clock()-self.stable["start"]>=.5:
                    self.candidate=self.snapshot(snap["id"],snap["baud"])
                    s=self._s()
                    self._save_state(s,connection_state="ready",adapter_state="present",last_error=None)
                    self.attempt=0;self.wait_started=self.clock()
                self.retry_at=self.clock()+.25
            except Refused as e:
                adapter='ambiguous' if e.code=='ADAPTER_AMBIGUOUS' else 'blocked' if e.code in ('PORT_BUSY','ROBOTD_ACTIVE','ROBOTD_PROCESS','BOARD_ROOT','BOARD_OS') else 'present'
                self._lost(str(e),adapter)
                s=self._s();self._save_state(s,connection_state="blocked",error_code=e.code)
            except (OSError,ConnectionError) as e:
                self._lost(str(e),"missing")
        finally:self.lock.release()

    def pause(self,value=True):
        self.cancel.set()
        if not self.lock.acquire(False):return dict(cancelling=True)
        try:
            s=self._s();s["paused"]=bool(value);s["plan"]=None
            s["connection_epoch"]+=1;self.candidate=None;self.stable=None
            s["connection_state"]="paused" if value else "waiting"
            self.store.save(s,"paused" if value else "resumed_read_only")
        finally:self.lock.release()
        return self.status()

    def detect(self):
        self.retry_at=0
        self.tick(True)
        return self.status()

    def scan(self,bauds=None):
        if not self.lock.acquire(False):raise Refused("BUSY","当前总线正在工作")
        try:
            if self._s()["paused"]:raise Refused("PAUSED","先恢复只读检测")
            if bauds is None:bauds=list(dict.fromkeys(BAUDS[self.bus.kind].values()))
            if not isinstance(bauds,list) or not bauds or len(bauds)>len(BAUDS[self.bus.kind]) or any(type(b) is not int or b not in BAUDS[self.bus.kind].values() for b in bauds):
                raise Refused('BAUD','扫描速率必须来自所选型号的速率表')
            self.cancel.clear();found=[];complete=True;deadline=self.clock()+180
            self._lost('正在完整只读扫描；旧预览已失效')
            for baud in dict.fromkeys(bauds):
                for ident in range(254 if self.bus.kind=="hd1910" else 253):
                    if self.cancel.is_set() or self.disconnected.is_set() or self.clock()>=deadline:
                        complete=False;break
                    try:
                        x=self.snapshot(ident,baud,False)
                        found.append(x)
                    except (ConnectionError,OSError):continue
                if not complete:break
            s=self._s();s['discovery_hint']=dict(id=found[0]['id'],baud=found[0]['baud']) if complete and len(found)==1 and found[0]['identity']['compatible'] else None
            self.store.save(s,'scan_completed',dict(complete=complete,found=len(found)))
            self.retry_at=0
            return dict(found=found,read_only=True,complete=complete,auto_selected=bool(s['discovery_hint']))
        finally:self.lock.release()

    def _guard_snapshot(self,s,snap,model_confirmed=False,allow_unlocked=False):
        p=profile(s["kind"]);r=snap["raw"]
        if not snap["identity"]["compatible"]:raise Refused("IDENTITY","型号或固件不在已评审契约中")
        if s["kind"]=="hd1910" and not model_confirmed:
            raise Refused("MODEL_CLAIM","请按实物标签确认HD1910-C001；协议版本不能证明唯一型号")
        m=metrics(s["kind"],r)
        if m["torque"]!=0:raise Refused("TORQUE","请在机器人支撑条件下单独卸力；初始化不会自动改变扭矩")
        if m["fault"]!=0:raise Refused("HARDWARE_FAULT",f"故障寄存器非0或未知：{m['fault']}")
        if m["voltage_v"] is None or not p["voltage_range"][0]<=m["voltage_v"]<=p["voltage_range"][1]:
            raise Refused("VOLTAGE","供电不在型号规格内或读数缺失")
        if m["temperature_c"] is None or m["temperature_c"]>=55:
            raise Refused("TEMPERATURE","温度未知或达到55°C；先检查并冷却")
        if s["kind"]=="hd1910" and r.get("55") not in ((0,1) if allow_unlocked else (1,)):
            raise Refused("LOCK","EPROM锁未确认关闭写入窗口；请先核对中断任务")

    def plan(self,index=None,label="",model_confirmed=False,same_servo=False):
        if not self.lock.acquire(False):raise Refused("BUSY","已有任务运行")
        try:
            s=self._s()
            if s["paused"] or s["connection_state"]!="ready" or not self.candidate:
                raise Refused("NOT_READY","等待稳定读取后再预览")
            if s["running_job"]:
                raise Refused("RECOVERY","先核对中断作业")
            if s["needs_identity"] and not same_servo:
                raise Refused("IDENTITY_RECONFIRM","断流后请确认是刚才那只舵机")
            idx=s["cursor"] if index is None else integer(index,0,14)
            slot=s["slots"][idx]
            if not isinstance(label,str) or not 1<=len(label.strip())<=80:
                raise Refused("LABEL","需要实物标签（1..80字符）")
            snap=self.snapshot(self.candidate["id"],self.candidate["baud"])
            self._guard_snapshot(s,snap,model_confirmed)
            known=next((i for i,x in enumerate(s["slots"]) if x["id"]==snap["id"] and
                        x["state"] in ("readback","verified")),None)
            if known is not None and known!=idx:
                raise Refused("RETURNED_SERVO",f"这是已登记的ID{snap['id']}；请核对对应槽位")
            wanted=desired(s["kind"],slot["id"],snap["raw"],s["options"])
            for addr in wanted:
                if str(addr) not in snap["raw"]:raise Refused("MISSING_REGISTER",f"寄存器{addr}缺失，不能生成写入")
            changes=[dict(address=a,size=profile(s["kind"])["registers"][a][0],
                          old=snap["raw"][str(a)],value=v)
                     for a,v in wanted.items() if snap["raw"].get(str(a))!=v]
            # Communication-changing fields are last; verify each at the new address/speed.
            p=profile(s["kind"])
            changes.sort(key=lambda c: 2 if c["address"]==p["baud_addr"] else
                         1 if c["address"]==p["id_addr"] else 0)
            binding={str(a):snap["raw"].get(str(a)) for a in wanted}
            plan=dict(id=new_id(),source_id=snap["id"],source_baud=snap["baud"],
                      target_id=slot["id"],index=idx,label=label.strip(),changes=changes,
                      desired={str(a):v for a,v in wanted.items()},binding=binding,
                      model_confirmed=model_confirmed,identity=snap["identity"],
                      connection_epoch=s["connection_epoch"],preset_hash=s["preset_hash"])
            plan['transport_epoch']=self.bus.connection_epoch
            plan["hash"]=digest(plan)
            slot["backup"]=slot["backup"] or snap
            slot["label"]=label.strip()
            s["plan"]=plan;s["needs_identity"]=False;s["phase"]="confirmation"
            self.store.save(s,"plan_created",dict(index=idx,plan_hash=plan["hash"]))
            return dict(plan=clone(plan),revision=s["revision"])
        finally:self.lock.release()

    def _fresh(self,request):
        s=self._s();plan=s.get("plan")
        if not plan or request.get("revision")!=s["revision"] or request.get("connection_epoch")!=s["connection_epoch"] or request.get("plan_hash")!=plan["hash"]:
            raise Refused("STALE_PLAN","计划已过期；请重新读取和预览")
        if s["paused"] or s["connection_state"]!="ready":raise Refused("NOT_READY","连接尚未就绪")
        if plan.get('transport_epoch')!=self.bus.connection_epoch:raise Refused('STALE_PLAN','原始串口连接已变化')
        self.bus.begin_transaction(plan['transport_epoch']);self.tx_epoch=plan['transport_epoch']
        return s,plan

    def _end_transaction(self):
        self.tx_epoch=None;self.bus.end_transaction()

    def _before_write(self,purpose,job):
        if self.tx_epoch is None or self.bus.connection_epoch!=self.tx_epoch:
            raise ConnectionError('原始事务连接已变化；禁止写入及重连收尾')
        if purpose!='cleanup':
            if self.disconnected.is_set() or self.cancel.is_set():raise Refused('CANCELLED','发包前已取消或断开')
            if job.get('kind')=='motion' and (self.clock()>=getattr(self,'motion_until',0) or self.clock()>=getattr(self,'motion_deadline',0)):
                raise Refused('TEST_STOP','台架心跳或总时限已到，禁止继续发包')

    def _step(self,s,job,addr,size,value,ident,baud,purpose="configuration"):
        self._before_write(purpose,job)
        if self.disconnected.is_set() and purpose!="cleanup":raise Refused("DISCONNECTED","代理连接已断开；冻结未发送写入")
        if self.cancel.is_set() and purpose!="cleanup":raise Refused("CANCELLED","已取消，停止发送后续写入")
        p=profile(s["kind"])
        if purpose not in ("motion","cleanup") and self.bus.read(ident,baud,p["torque"],1)!=0:
            raise Refused("TORQUE","写前扭矩核对失败")
        job["steps"].append(dict(address=addr,value=value,id=ident,baud=baud,state="intent"))
        self.store.save_job(job)  # FULL synchronous commit before TX.
        self.fault_hook("after_intent")
        self.bus.pre_write_guard=lambda:self._before_write(purpose,job)
        self._before_write(purpose,job)
        if (self.disconnected.is_set() or self.cancel.is_set()) and purpose!="cleanup":
            raise Refused("CANCELLED","发包前已取消或断开")
        try:
            self.bus.write(ident,baud,addr,size,value,purpose=purpose)
        except NoAck:
            job["steps"][-1]["ack_missing"]=True
            self.store.save_job(job)
        self.fault_hook("after_packet")
        step=job["steps"][-1];step["state"]="sent";self.store.save_job(job)
        new_id=value if addr==p["id_addr"] else ident
        new_baud=BAUDS[s["kind"]][value] if addr==p["baud_addr"] else baud
        time.sleep(0 if self.bus.simulated else .025)
        got=self.bus.read(new_id,new_baud,addr,size)
        if got!=value:raise Refused("READBACK",f"寄存器{addr}读回{got} != {value}")
        self.fault_hook("after_readback")
        step["state"]="verified";self.store.save_job(job)
        return new_id,new_baud

    def apply(self,request):
        if (self._s().get("plan") or {}).get("kind") in ("midpoint","motion","rollback"):
            return self.apply_action(request)
        ident=request.get("job_id")
        if not isinstance(ident,str) or not 1<=len(ident)<=80:raise Refused("JOB_ID","需要幂等任务ID")
        previous=self.store.job(ident)
        if previous:
            if previous.get("request_hash")!=digest(request):raise Refused("JOB_REUSE","任务ID对应另一请求")
            return previous
        if not self.lock.acquire(False):raise Refused("BUSY","已有配置或检查正在进行")
        job=None
        try:
            self.cancel.clear()
            s,plan=self._fresh(request)
            snap=self.snapshot(plan["source_id"],plan["source_baud"])
            self._guard_snapshot(s,snap,plan["model_confirmed"])
            if snap["identity"]!=plan["identity"] or any(snap["raw"].get(a)!=v for a,v in plan["binding"].items()):
                raise Refused("CHANGED","设备读数已变化，需重新预览")
            job=dict(id=ident,session=s["id"],request_hash=digest(request),kind="configuration",
                     state="running",steps=[],plan=clone(plan))
            self.store.save_job(job)
            s["phase"]="configuring";s["running_job"]=ident
            s["slots"][plan["index"]]["state"]="configuring";self.store.save(s,"apply_started")
            p=profile(s["kind"]);sid=plan["source_id"];baud=plan["source_baud"]
            if s["kind"]=="hd1910" and plan["changes"]:
                sid,baud=self._step(s,job,55,1,0,sid,baud)
            for c in plan["changes"]:
                sid,baud=self._step(s,job,c["address"],c["size"],c["value"],sid,baud)
            if s["kind"]=="hd1910" and plan["changes"]:
                sid,baud=self._step(s,job,55,1,1,sid,baud)
            after=self.snapshot(sid,baud)
            self._guard_snapshot(s,after,plan["model_confirmed"])
            if any(after["raw"].get(a)!=v for a,v in plan["desired"].items()):
                raise Refused("READBACK","最终配置不一致")
            self.fault_hook("before_commit")
            slot=s["slots"][plan["index"]];slot["state"]="readback";slot["after"]=after
            slot["persistence"]="pending_operator_power_cycle";slot["last_job"]=ident
            s["phase"]="waiting_next";s["running_job"]=None;s["plan"]=None
            s["cursor"]=next((i for i,x in enumerate(s["slots"]) if x["state"] not in ("readback","verified")),15)
            s["last_message"]=f"已保存第 {sum(x['state'] in ('readback','verified') for x in s['slots'])}/15 只配置。请关闭舵机电源，更换下一只并上电；检测到后会自动显示配置预览。"
            job["state"]="readback";job["after"]=after
            self.store.finish(s,job,"apply_readback",dict(slot=plan["index"],safe_to_swap=True))
            self.candidate=after
            return job
        except Exception as e:
            if job:
                job["state"]="uncertain";job["error"]=str(e);self.store.save_job(job)
                s=self._s();s["phase"]="interrupted";s["needs_identity"]=True
                s["slots"][job["plan"]["index"]]["state"]="interrupted"
                # Preserve old/new addresses in the plan for read-only reconciliation.
                self.store.save(s,"apply_interrupted",dict(error=str(e)))
                self.connected=True;self._lost(str(e))
            raise
        finally:
            self._end_transaction();self.lock.release()

    def reconcile(self,same_servo=False,model_confirmed=False):
        if not same_servo:raise Refused("IDENTITY_RECONFIRM","请核对实物标签，确认仍是中断的那只")
        if not self.lock.acquire(False):raise Refused("BUSY","作业仍在运行")
        try:
            s=self._s()
            if not self.candidate or s["connection_state"]!="ready":raise Refused("NOT_READY","先等待读取稳定")
            old=s.get("running_job");job=self.store.job(old) if old else None
            snap=self.snapshot(self.candidate["id"],self.candidate["baud"])
            p=profile(s["kind"])
            self._guard_snapshot(s,snap,model_confirmed,allow_unlocked=True)
            self.bus.begin_transaction(self.bus.connection_epoch);self.tx_epoch=self.bus.connection_epoch
            # A relock is an explicit recovery action, never a reconnect side effect.
            if snap["raw"].get(str(p["torque"]))!=0:raise Refused("TORQUE","仍有扭矩或未知；禁止恢复")
            if s["kind"]=="hd1910" and snap["raw"].get("55")==0:
                recovery=dict(id=new_id(),session=s["id"],kind="relock",state="running",steps=[],plan=dict(source_id=snap['id'],source_baud=snap['baud']))
                self.cancel.clear()
                try:self._step(s,recovery,55,1,1,snap["id"],snap["baud"],purpose="cleanup")
                except Exception as error:
                    recovery.update(state='uncertain',error=str(error));self.store.save_job(recovery);raise
                recovery["state"]="verified";self.store.save_job(recovery)
                snap=self.snapshot(snap["id"],snap["baud"])
            self._guard_snapshot(s,snap,model_confirmed)
            if job:
                job["state"]="reconciled_read_only";job["observed_after_reconnect"]=snap;self.store.save_job(job)
                if 'index' in job.get('plan',{}):s['cursor']=job['plan']['index']
            s["running_job"]=None;s["needs_identity"]=False;s["plan"]=None;s["phase"]="confirmation"
            self.store.save(s,"reconciled",dict(parent_job=old))
            self.candidate=snap
            return dict(observed=snap,remaining_requires_new_plan=True)
        finally:
            self._end_transaction();self.lock.release()

    def verify_all(self,power_cycle_confirmed=False,model_confirmed=False):
        if not power_cycle_confirmed:raise Refused("POWER_CYCLE","请人工确认整组已断电重上电")
        if not self.lock.acquire(False):raise Refused("BUSY","已有作业运行")
        try:
            s=self._s()
            if s["running_job"]:raise Refused("RECOVERY","先核对中断任务")
            if any(x["state"] not in ("readback","verified") for x in s["slots"]):
                raise Refused("INCOMPLETE","15个槽位尚未全部完成单只读回")
            self.bus.reconnect();self.bus.begin_transaction(self.bus.connection_epoch)
            self.tx_epoch=self.bus.connection_epoch
            self.cancel.clear();s["phase"]="verifying_all";self.store.save(s,"power_cycle_claim",dict(basis="operator_claim_not_electrical_measurement"))
            rows=[]
            for slot in s["slots"]:
                try:
                    if self.cancel.is_set():raise Refused("CANCELLED","已取消检查")
                    snap=self.snapshot(slot["id"],1000000)
                    self._guard_snapshot(s,snap,model_confirmed)
                    want=desired(s["kind"],slot["id"],slot["backup"]["raw"],s["options"])
                    ok=all(snap["raw"].get(str(a))==v for a,v in want.items())
                    expected=(slot.get('after') or {}).get('raw',{})
                    persistent_ok=all(str(a) in expected and str(a) in snap['raw'] and
                                      expected[str(a)]==snap['raw'][str(a)] for a in PERSISTENT[s['kind']])
                    ok=ok and persistent_ok
                    if not ok:raise Refused("PERSISTENCE","重连读数与预设不同")
                    slot["state"]="verified";slot["persistence"]="operator_cycle_and_readback"
                    slot["after"]=snap;rows.append(dict(id=slot["id"],ok=True))
                except (OSError,ConnectionError,Refused) as e:
                    slot["state"]="repair";slot["persistence"]="failed";rows.append(dict(id=slot["id"],ok=False,error=str(e)))
            s["phase"]="complete" if all(x["ok"] for x in rows) else "repair"
            s["power_cycle_evidence"]=dict(operator_confirmed=True,at=time.time(),readbacks=rows)
            self.store.save(s,"group_verified",dict(ok=s["phase"]=="complete"))
            return dict(rows=rows,hardware_acceptance=False,simulated=s["simulated"])
        finally:
            self._end_transaction();self.lock.release()

    def report(self):
        s=self._s()
        report=dict(schema="microduck-servo-workbench/v1",component_version="0.1.1",
                    exported_at=time.time(),session=s,events=self.store.events(s["id"]),
                    jobs=[],event_window="first_200; full history in authority SQLite",
                    historical=True,live=False,identity="operator_labels_and_protocol_evidence",
                    simulated=s["simulated"],hardware_acceptance=False,
                    caveat="配置读回不证明机械装配、额定扭矩、寿命或运动性能")
        size=len(json.dumps(report,ensure_ascii=False).encode())
        for job in self.store.jobs(s['id']):
            counts={key:len(job.get(key,[])) for key in ('steps','samples')}
            job={**job,'steps':job.get('steps',[])[-500:], 'samples':job.get('samples',[])[-300:]}
            job['export_window']=dict(original_counts=counts,full_history='authority SQLite')
            amount=len(json.dumps(job,ensure_ascii=False).encode())
            if size+amount>1_800_000:break
            report['jobs'].append(job);size+=amount
        report['job_window']=dict(order='newest_first',included=len(report['jobs']),
                                  total=self.store.job_count(s['id']),full_history='authority SQLite')
        return report

    def checks(self):
        if not self.lock.acquire(False):raise Refused("BUSY","只读检查等待当前作业结束")
        try:return self._checks_locked()
        finally:self.lock.release()

    def _checks_locked(self):
        if not self.candidate:raise Refused("NOT_READY","没有新鲜设备读数")
        s=self._s();snap=self.snapshot(self.candidate["id"],self.candidate["baud"])
        p=profile(s["kind"]);m=snap["metrics"]
        findings=[]
        def add(code,raw,advice):
            findings.append(dict(code=code,raw=raw,source="direct_register_read",
                                 advice=advice,verification="重新只读采集并核对新读数"))
        if snap["read_errors"]:add("READ_ERRORS",snap["read_errors"],"检查电源、接线、端口独占和正确型号；不要反复写参数")
        if m["fault"]!=0:add("FAULT",m["fault"],"支撑机构，记录错误位，按型号核对供电、温度、电流；不自动清故障")
        if m["voltage_v"] is None or not p["voltage_range"][0]<=m["voltage_v"]<=p["voltage_range"][1]:
            add("VOLTAGE",m["voltage_v"],"用电表确认舵机电源与型号规格；保护未触发不代表供电合规")
        if m["temperature_c"] is None or m["temperature_c"]>=55:
            add("TEMPERATURE",m["temperature_c"],"停止测试并冷却，检查负载和机构卡滞")
        if snap["raw"].get(str(p["mode_addr"]))!=p["mode"]:
            add("MODE",snap["raw"].get(str(p["mode_addr"])),"核对所选预设；模式改变需要重新预览并验证相关RAM参数")
        return dict(snapshot=snap,findings=findings,read_only=True,
                    conclusion="未发现已配置检查项异常" if not findings else "需要按证据排查")
