"""Historical report validation; cannot create live/physical acceptance."""
import json
from .profiles import Refused,IDS,profile


def validate(value):
    if not isinstance(value,dict) or value.get("schema")!="microduck-servo-workbench/v1":
        raise Refused("REPORT_SCHEMA","需要舵机工作台v1报告")
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>2*1024*1024:
        raise Refused("REPORT_SIZE","报告超过2MiB")
    s=value.get("session")
    if not isinstance(s,dict):raise Refused("REPORT","缺少批次")
    profile(s.get("kind"))
    rows=s.get("slots")
    if not isinstance(rows,list) or len(rows)!=15 or sorted(x.get("id") for x in rows if isinstance(x,dict))!=IDS:
        raise Refused("REPORT","报告需要15个唯一预期槽位")
    if not isinstance(value.get('events',[]),list) or not isinstance(value.get('jobs',[]),list):
        raise Refused('REPORT','事件和任务必须是列表')
    for row in rows:
        if not isinstance(row.get("state"),str):raise Refused("REPORT","槽位状态错误")
    # Do not trust exported identity/live/hardware-acceptance claims.
    s={**s,"hardware_acceptance":False,"live":False,"identity":"imported_claim_not_verified"}
    return dict(schema=value["schema"],session=s,simulated=value.get("simulated") is True,
                historical=True,live=False,hardware_acceptance=False,
                identity="imported_claim_not_verified",events=value.get("events",[])[:200],
                jobs=value.get("jobs",[]),event_window=value.get("event_window","unknown"),
                job_window=value.get('job_window',{'coverage':'unknown'}),
                component_version=str(value.get("component_version","unknown"))[:30])
