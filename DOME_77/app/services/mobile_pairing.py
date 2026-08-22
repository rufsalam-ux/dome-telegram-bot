from __future__ import annotations
import base64, hashlib, hmac, json, secrets, time
from pathlib import Path
from app.core.config import settings

_FILE = settings.storage_root / 'mobile_pairing.json'

def _secret() -> bytes:
    raw=(settings.consent_hash_secret or settings.bot_token or 'DOME-MOBILE-CHANGE-ME').encode('utf-8')
    return hashlib.sha256(raw+b'|mobile-v1').digest()

def _load() -> dict:
    try:return json.loads(_FILE.read_text(encoding='utf-8'))
    except Exception:return {}

def _save(data:dict):
    _FILE.parent.mkdir(parents=True,exist_ok=True)
    _FILE.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')

def issue_pair_code(parent_id:int, ttl:int=600) -> str:
    data=_load(); now=int(time.time())
    data={k:v for k,v in data.items() if int(v.get('exp',0))>now}
    code=f'{secrets.randbelow(1000000):06d}'
    data[code]={'parent_id':int(parent_id),'exp':now+ttl}
    _save(data); return code

def consume_pair_code(code:str) -> int | None:
    data=_load(); row=data.pop(str(code),None); _save(data)
    if not row or int(row.get('exp',0))<int(time.time()): return None
    return int(row['parent_id'])

def issue_token(parent_id:int, ttl:int=60*60*24*90) -> str:
    payload={'parent_id':int(parent_id),'exp':int(time.time())+ttl,'v':1}
    raw=json.dumps(payload,separators=(',',':')).encode(); body=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    sig=hmac.new(_secret(),body.encode(),hashlib.sha256).hexdigest()
    return body+'.'+sig

def verify_token(token:str) -> int | None:
    try:
        body,sig=token.split('.',1)
        expected=hmac.new(_secret(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig,expected): return None
        raw=base64.urlsafe_b64decode(body+'='*((4-len(body)%4)%4)); p=json.loads(raw)
        if int(p.get('exp',0))<int(time.time()): return None
        return int(p['parent_id'])
    except Exception:return None

def signed_media_token(value:str, ttl:int=86400*7) -> str:
    exp=int(time.time())+ttl; body=f'{value}|{exp}'; sig=hmac.new(_secret(),body.encode(),hashlib.sha256).hexdigest()[:24]
    return f'{exp}.{sig}'

def verify_media_token(value:str, token:str) -> bool:
    try:
        exp_s,sig=token.split('.',1); exp=int(exp_s)
        if exp<int(time.time()): return False
        body=f'{value}|{exp}'; expected=hmac.new(_secret(),body.encode(),hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(sig,expected)
    except Exception:return False
