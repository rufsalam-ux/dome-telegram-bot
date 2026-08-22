from __future__ import annotations
import json
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from app.core.config import settings

DAYS={'mon':0,'tue':1,'wed':2,'thu':3,'fri':4,'sat':5,'sun':6}

def _path(child_id:int)->Path:return settings.storage_root/'lesson-schedules'/f'{child_id}.json'
def save_schedule(child_id:int,timezone:str,days:list[str],local_time:str,remind_before_minutes:int=15)->dict:
    ZoneInfo(timezone); hh,mm=map(int,local_time.split(':')); assert 0<=hh<24 and 0<=mm<60
    data={'timezone':timezone,'days':[d.lower()[:3] for d in days if d.lower()[:3] in DAYS],'local_time':f'{hh:02d}:{mm:02d}','remind_before_minutes':max(5,min(60,int(remind_before_minutes))),'enabled':True,'last_sent_key':None}
    p=_path(child_id);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');return data
def load_schedule(child_id:int)->dict|None:
    p=_path(child_id)
    try:return json.loads(p.read_text('utf-8'))
    except Exception:return None
def due_now(child_id:int,now_utc:datetime|None=None)->tuple[bool,dict|None]:
    s=load_schedule(child_id)
    if not s or not s.get('enabled'):return False,s
    now_utc=now_utc or datetime.now(tz=ZoneInfo('UTC')); local=now_utc.astimezone(ZoneInfo(s['timezone']))
    if local.hour<7 or local.hour>=21:return False,s  # never send at night
    hh,mm=map(int,s['local_time'].split(':')); lesson=local.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if local.weekday() not in [DAYS[d] for d in s.get('days',[])]:return False,s
    remind=lesson-timedelta(minutes=int(s.get('remind_before_minutes',15))); key=lesson.strftime('%Y-%m-%dT%H:%M')
    return (remind<=local<lesson+timedelta(minutes=2) and s.get('last_sent_key')!=key),s
def mark_sent(child_id:int,key:str)->None:
    s=load_schedule(child_id) or {};s['last_sent_key']=key;p=_path(child_id);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
