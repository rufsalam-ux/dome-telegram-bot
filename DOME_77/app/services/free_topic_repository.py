from __future__ import annotations
import json, re, hashlib
from pathlib import Path
from app.core.config import settings

def _slug(value:str)->str:
    value=re.sub(r"\s+"," ",(value or "").strip().lower())
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

def _age_band(age:int|None)->str:
    if age is None: return "unknown"
    if age<=5: return "3-5"
    if age<=8: return "6-8"
    if age<=12: return "9-12"
    if age<=17: return "13-17"
    return "18+"

def library_key(topic:str,target_language:str,native_language:str,age:int|None,level:str)->str:
    raw=f"{topic.strip().lower()}|{target_language}|{native_language}|{_age_band(age)}|{level}"
    return _slug(raw)

def _root()->Path:
    p=settings.storage_root/"free-topic-library"; p.mkdir(parents=True,exist_ok=True); return p

def choose_unused_variant(child_id:int, topic:str, target_language:str, native_language:str, age:int|None, level:str):
    key=library_key(topic,target_language,native_language,age,level); d=_root()/key; d.mkdir(parents=True,exist_ok=True)
    meta=d/"index.json"
    try: data=json.loads(meta.read_text("utf-8"))
    except Exception: data={"variants":[]}
    for item in data.get("variants",[]):
        if int(child_id) not in [int(x) for x in item.get("used_by",[])]:
            path=d/item["file"]
            if path.exists():
                lesson=json.loads(path.read_text("utf-8")); item.setdefault("used_by",[]).append(int(child_id)); meta.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8"); return lesson, str(item.get("id"))
    return None, None

def save_variant(child_id:int, lesson:dict, topic:str,target_language:str,native_language:str,age:int|None,level:str):
    key=library_key(topic,target_language,native_language,age,level); d=_root()/key; d.mkdir(parents=True,exist_ok=True); meta=d/"index.json"
    try: data=json.loads(meta.read_text("utf-8"))
    except Exception: data={"topic":topic,"variants":[]}
    n=len(data.get("variants",[]))+1; vid=f"v{n:03d}"; fn=f"{vid}.json"
    obj=dict(lesson); obj["library_variant_id"]=vid; (d/fn).write_text(json.dumps(obj,ensure_ascii=False,indent=2),"utf-8")
    data.setdefault("variants",[]).append({"id":vid,"file":fn,"used_by":[int(child_id)]}); meta.write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8")
    return obj, vid
