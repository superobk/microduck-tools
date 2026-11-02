"""Explicit optional actions; never called by discovery/reconnect."""
import math
import time
from .profiles import Refused,profile,integer,digest,identity,BAUDS,NoAck
from .store import clone,new_id


class Actions:
    def heartbeat(self):
        self.motion_until=self.clock()+.75
        return dict(motion_lease_s=.75)

    def cancel_action(self):
        self.cancel.set()
        return dict(cancelling=True,torque_state="await_readback")

    def action_plan(self,kind,model_confirmed=False,standalone=False,operator_ready=False):
        if kind not in ("midpoint","motion","rollback"):raise Refused("ACTION","未知操作")
        if not standalone or not operator_ready:
            raise Refused("READINESS","需要确认独立单舵机台架、固定与可立即断电条件")
        if not self.lock.acquire(False):raise Refused("BUSY","已有作业运行")
        try:
            s=self._s();p=profile(s["kind"])
            if s["running_job"]:raise Refused("RECOVERY","先核对中断任务")
            if s["connection_state"]!="ready" or not self.candidate:raise Refused("NOT_READY","尚未稳定读取")
            snap=self.snapshot(self.candidate["id"],self.candidate["baud"])
            self._guard_snapshot(s,snap,model_confirmed)
            idx=next((i for i,x in enumerate(s["slots"]) if x["id"]==snap["id"]),
                     min(s["cursor"],14))
            plan=dict(id=new_id(),kind=kind,index=idx,source_id=snap["id"],source_baud=snap["baud"],
                      target_id=snap["id"],identity=snap["identity"],snapshot=snap,
                      model_confirmed=model_confirmed,standalone=True,operator_ready=True,
                      connection_epoch=s["connection_epoch"],preset_hash=s["preset_hash"])
            plan['transport_epoch']=self.bus.connection_epoch
            if kind=="midpoint":
                if s["kind"]!="hd1910":raise Refused("MIDPOINT","此步骤只用于HD1910")
                if snap["raw"].get("33")!=4:raise Refused("MODE","中位只开放已评审HD1910模式4")
                plan["effect"]="0x0B把当前机械位置定义为2048；不发目标，不自动保持"
                plan["binding"]={a:snap["raw"].get(a) for a in ("31","56","33","40","55")}
            elif kind=="motion":
                if snap["raw"].get(str(p["mode_addr"]))!=p["mode"]:
                    raise Refused("MODE","先核对位置模式，不在测试中修改EEPROM")
                pos=snap["metrics"]["position_raw"]
                lo=snap["raw"].get("52" if s["kind"]=="xl330" else "9")
                hi=snap["raw"].get("48" if s["kind"]=="xl330" else "11")
                if pos is None or lo is None or hi is None or not lo<pos<hi or pos-35<lo or pos+35>hi:
                    raise Refused("TRAVEL","当前位置附近没有完整±3°的安全余量")
                if s["kind"]=="xl330":
                    r=snap["raw"];offset=r["20"]-(1<<32) if r["20"]&(1<<31) else r["20"]
                    normalized=((pos-offset)%4096)+offset
                    if normalized!=pos or r.get("10")!=0 or r.get("60")!=0 or r.get("12")!=255:
                        raise Refused("POSITION_RESET","扭矩切换/启动配置/副ID存在歧义，拒绝动作")
                    if r.get("98")!=0:raise Refused("WATCHDOG","现有看门狗状态需先人工核对")
                else:
                    if snap["raw"].get("7")!=snap["id"] or snap["raw"].get("18")!=0:
                        raise Refused("PHASE","HD1910副ID或相位与台架契约不同")
                plan["binding"]={str(a):snap["raw"].get(str(a)) for a in p["registers"]
                                 if a not in (p["current"],p["velocity"],p["voltage"],p["temperature"])}
                plan["test"]=dict(amplitude_deg=3,speed_deg_s=5,cycles=3,max_s=20,
                                  current_abort_ma=300,temp_abort_c=55,
                                  scope="unloaded_single_servo_not_rated_torque_or_life")
            else:
                slot=s["slots"][idx];parent=self.store.job(slot.get("last_job",""))
                if not parent or parent.get("kind")!="configuration":
                    raise Refused("BACKUP","没有本批次配置任务的写前备份")
                changes=clone(parent["plan"]["changes"])
                for c in changes:c["value"],c["old"]=c["old"],snap["raw"].get(str(c["address"]))
                changes=[c for c in changes if c['value']!=c['old']]
                for c in changes:
                    if c['address']==p['baud_addr'] and c['value'] not in BAUDS[s['kind']]:raise Refused('BAUD','备份波特率代码无效，拒绝回退写入')
                    if c['address']==p['id_addr'] and (type(c['value']) is not int or not 0<=c['value']<=(252 if s['kind']=='xl330' else 253)):raise Refused('ID','备份ID不可用于单播，拒绝回退')
                changes.sort(key=lambda c:2 if c["address"]==p["baud_addr"] else
                             1 if c["address"]==p["id_addr"] else 0)
                plan["changes"]=changes;plan["parent_job"]=parent["id"]
                plan["binding"]={str(c["address"]):snap["raw"].get(str(c["address"])) for c in changes}
                plan["effect"]="只恢复本配置任务修改项；中位、目标与扭矩不自动回退"
            plan["hash"]=digest(plan);s["plan"]=plan;s["phase"]="confirmation"
            self.store.save(s,"optional_plan",dict(kind=kind))
            return dict(plan=plan,revision=s["revision"])
        finally:self.lock.release()

    def apply_action(self,request):
        jid=request.get("job_id")
        if not isinstance(jid,str) or not 1<=len(jid)<=80:raise Refused("JOB_ID","需要任务ID")
        old=self.store.job(jid)
        if old:
            if old.get("request_hash")!=digest(request):raise Refused("JOB_REUSE","任务ID冲突")
            return old
        if not self.lock.acquire(False):raise Refused("BUSY","已有作业运行")
        job=None
        try:
            self.cancel.clear()
            s,plan=self._fresh(request);p=profile(s["kind"])
            snap=self.snapshot(plan["source_id"],plan["source_baud"])
            self._guard_snapshot(s,snap,plan["model_confirmed"])
            if snap["identity"]!=plan["identity"] or any(snap["raw"].get(a)!=v for a,v in plan["binding"].items()):
                raise Refused("CHANGED","读数变化，请重新预览可选步骤")
            job=dict(id=jid,session=s["id"],kind=plan["kind"],plan=clone(plan),
                     request_hash=digest(request),state="running",steps=[])
            self.store.save_job(job);s["running_job"]=jid;s["phase"]={"motion":"testing","rollback":"rolling_back"}.get(plan["kind"],plan["kind"])
            self.store.save(s,"optional_started")
            sid=plan["source_id"];baud=plan["source_baud"]
            if plan["kind"]=="midpoint":
                job["steps"].append(dict(instruction=11,state="intent"))
                self.store.save_job(job);self.fault_hook("after_intent")
                self.bus.pre_write_guard=lambda:self._before_write('configuration',job)
                self._before_write('configuration',job)
                if self.disconnected.is_set() or self.cancel.is_set():
                    raise Refused("DISCONNECTED","中位发包前连接已断开或取消")
                # Exactly one command. Never repeat on timeout or reconnect.
                try:
                    self.bus.reset_mid(sid,baud)
                except NoAck:
                    job["steps"][-1]["ack_missing"]=True
                self.fault_hook("after_packet")
                time.sleep(0 if self.bus.simulated else .15)
                after=self.snapshot(sid,baud);self._guard_snapshot(s,after,plan["model_confirmed"])
                if abs(after["metrics"]["position_raw"]-2048)>4:
                    raise Refused("MID_NO_EFFECT","中位应答不能证明生效；位置与2048不一致，禁止自动重发")
                if after['raw'].get('31') is None:raise Refused('MID_OFFSET','中位偏移读数缺失，不能验证')
                job["steps"][-1]["state"]="position_readback_with_offset_evidence"
                job['midpoint_effect']='already_centered_or_not_observable' if abs(snap['metrics']['position_raw']-2048)<=4 else 'position_changed_to_midpoint'
                job["before_offset"]=snap["raw"]["31"];job["after_offset"]=after["raw"]["31"]
                slot=s["slots"][plan["index"]];slot["midpoint"]=dict(job=jid,before=snap,after=after)
                slot["after"]=after  # The next power-cycle comparison must use the new offset.
                slot["test"]=None;slot["persistence"]="pending_operator_power_cycle"
                if slot["state"]=="verified":slot["state"]="readback"
                s["calibration_review_required"]=True
                s["coordinate_generation"]=s.get("coordinate_generation",0)+1
            elif plan["kind"]=="rollback":
                if s["kind"]=="hd1910" and plan["changes"]:
                    sid,baud=self._step(s,job,55,1,0,sid,baud)
                for c in plan["changes"]:
                    sid,baud=self._step(s,job,c["address"],c["size"],c["value"],sid,baud)
                if s["kind"]=="hd1910" and plan["changes"]:
                    sid,baud=self._step(s,job,55,1,1,sid,baud)
                after=self.snapshot(sid,baud);self._guard_snapshot(s,after,plan["model_confirmed"])
                s["slots"][plan["index"]]["state"]="rolled_back"
                s["slots"][plan["index"]]["persistence"]="rollback_readback_only"
                s["cursor"]=plan["index"]
            else:
                after=self._motion(s,plan,job)
                s["slots"][plan["index"]]["test"]=dict(job=jid,summary=job.get("summary"))
            job["state"]="verified";job["after"]=after
            s["running_job"]=None;s["plan"]=None;s["phase"]="waiting"
            self.store.finish(s,job,"optional_completed",dict(kind=plan["kind"],job=jid))
            self.candidate=after
            return job
        except Exception as e:
            if job:
                cleanup="not_attempted_after_link_failure"
                if job["kind"]=="motion" and not isinstance(e,(OSError,ConnectionError)):
                    try:
                        self._step(s,job,p["torque"],1,0,plan["source_id"],plan["source_baud"],"cleanup")
                        cleanup="torque_off_readback"
                    except Exception as closing:cleanup="unknown: "+str(closing)
                job["state"]="uncertain";job["error"]=str(e);job["cleanup"]=cleanup
                self.store.save_job(job)
                s=self._s();s["phase"]="interrupted";s["needs_identity"]=True
                self.store.save(s,"optional_interrupted",dict(job=jid,cleanup=cleanup))
                self.connected=True;self._lost(str(e))
            raise
        finally:
            self._end_transaction();self.lock.release()

    def _motion(self,s,plan,job):
        p=profile(s["kind"]);sid=plan["source_id"];baud=plan["source_baud"]
        r=plan["snapshot"]["raw"];base=plan["snapshot"]["metrics"]["position_raw"]
        self.motion_deadline=self.clock()+20
        if self.clock()>=getattr(self,"motion_until",0):
            raise Refused("MOTION_HEARTBEAT","需要独立的前台测试心跳；遥测不能续期")
        temporary=([ (100,2,min(r["100"],177)),(108,4,2),(112,4,4),(98,1,15) ]
                   if s["kind"]=="xl330" else
                   [(41,1,1),(44,2,min(r["44"],46)),(46,2,100),(48,2,min(r["48"],200))])
        if any(v<=0 for _,_,v in temporary):raise Refused("LIMIT","台架限值为0，先人工核对")
        self._motion_feedback(s,sid,baud,base,0)
        for a,size,v in temporary:self._step(s,job,a,size,v,sid,baud,"motion")
        self._motion_feedback(s,sid,baud,base,0)
        # Preload only the verified current position; never HOME/2048.
        goal=116 if s["kind"]=="xl330" else 42
        self._step(s,job,goal,p["registers"][goal][0],base,sid,baud,"motion")
        self._step(s,job,p["torque"],1,1,sid,baud,"motion")
        self._motion_feedback(s,sid,baud,base,1)
        samples=[];started=self.clock();previous=base
        for deg in [3,-3,0]*3:
            target=base+round(deg*4096/360)
            n=max(1,math.ceil(abs(target-previous)/(5*4096/360*.05)))
            for step in range(1,n+1):
                if self.cancel.is_set() or self.clock()>=self.motion_until or self.clock()-started>20:
                    raise Refused("TEST_STOP","取消、心跳超时或任务超时")
                value=round(previous+(target-previous)*step/n)
                self._step(s,job,goal,p["registers"][goal][0],value,sid,baud,"motion")
                snap=self._motion_feedback(s,sid,baud,base,1);m=snap['metrics']
                samples.append(dict(t=self.clock(),target_raw=value,actual_raw=m["position_raw"],
                                    current_ma=m["current_ma"],temperature_c=m["temperature_c"]))
                if not self.bus.simulated:time.sleep(.05)
            previous=target
        self._step(s,job,p["torque"],1,0,sid,baud,"cleanup")
        # Restore safe temporary settings with torque OFF; never restore an old goal or torque ON.
        for a,size,_ in reversed(temporary):
            self._step(s,job,a,size,r[str(a)],sid,baud,"motion")
        after=self.snapshot(sid,baud);self._guard_snapshot(s,after,plan["model_confirmed"])
        elapsed=self.clock()-started
        errors=[abs(x["target_raw"]-x["actual_raw"])*360/4096 for x in samples]
        job["samples"]=samples
        job["summary"]=dict(samples=len(samples),elapsed_s=elapsed,
                            observed_hz=None if elapsed<=0 else len(samples)/elapsed,
                            max_follow_error_deg=max(errors,default=None),
                            repeatability_raw=[x["actual_raw"] for x in samples if x["target_raw"]==base],
                            simulated=s["simulated"],
                            caveat="有限无负载轨迹；不能证明额定速度、扭矩、剩余寿命")
        return after

    def _motion_feedback(self,s,sid,baud,base,torque):
        p=profile(s['kind'])
        snap=self.snapshot(sid,baud,addresses={p[k] for k in ('position','current','velocity','voltage','temperature','fault','torque','mode_addr')})
        m=snap['metrics']
        if m['position_raw'] is None or m['speed_deg_s'] is None or m['current_ma'] is None or abs(m['current_ma'])>300 or m['temperature_c'] is None or m['temperature_c']>=55 or m['fault']!=0:
            raise Refused('TEST_PROTECTION','位置/速度/电流/温度/故障反馈缺失或触发停止条件')
        if abs(m['position_raw']-base)*360/4096>5:
            raise Refused('TEST_TRAVEL','实际位置超出台架±5°观测边界')
        if torque==0 and abs(m['position_raw']-base)>4:
            raise Refused('CHANGED','准备阶段位置已变化，禁止按旧位置上扭矩；请重新固定并预览')
        if m['torque']!=torque or snap['raw'].get(str(p['mode_addr']))!=p['mode']:
            raise Refused('TEST_STATE','实际扭矩或模式与本次台架状态不同')
        if m['voltage_v'] is None or not p['voltage_range'][0]<=m['voltage_v']<=p['voltage_range'][1]:
            raise Refused('VOLTAGE','测试中电压异常或缺失')
        return snap
