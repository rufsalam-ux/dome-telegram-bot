from __future__ import annotations
import hashlib, json
from pathlib import Path
import httpx
from app.core.config import settings
from app.core.i18n import language_name

class VisualLocalizationError(RuntimeError): pass

def _cache_key(source:Path,target_language:str)->str:
    return hashlib.sha256(f"{source.resolve()}:{source.stat().st_mtime_ns}:{target_language}:{settings.openai_image_model}".encode()).hexdigest()[:28]

async def localize_embedded_russian_image(source:Path, output_root:Path, target_language:str)->Path:
    """One-time AI edit for PNG/JPG slides with Russian text baked into pixels.
    Russian target returns the original untouched. Other languages use an image edit and cache the result.
    If the provider/key is unavailable, returns the source so the lesson never crashes.
    """
    if not source.exists() or not target_language or target_language=='ru' or not settings.openai_api_key:
        return source
    # v61: pre-cleaned animal/Mila assets contain no baked Russian labels.
    # Never send them to image AI, which previously invented the name Lyosha.
    if source.stem.endswith("-clean") or source.stem.startswith("animal-pair-"):
        return source
    out=output_root/'localized-visuals'/target_language/f"{source.stem}_{_cache_key(source,target_language)}.png"
    if out.exists() and out.stat().st_size>1000: return out
    out.parent.mkdir(parents=True,exist_ok=True)
    prompt=(
        f"Edit this educational children's slide. Remove EVERY visible Russian/Cyrillic text element completely, reconstruct the original background cleanly underneath, "
        f"then replace each removed text with a natural child-friendly translation in {language_name(target_language)}. Preserve all illustrations, objects, characters, layout, colors, sizes and composition. "
        "Do not leave any Russian letters visible or ghosted. Do not add new objects, logos or extra captions. Keep translated text inside the original text areas and do not cover illustrations. "
        "Preserve character identity exactly. If the source says Лёша, use Lyosha (never Alex). If the source says Мила, use Mila (never Lyosha). "
        "Never assign a human name to an animal and never invent a name that is not present in the source."
    )
    headers={'Authorization':f'Bearer {settings.openai_api_key}'}
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            with source.open('rb') as f:
                files={'image':(source.name,f,'image/png' if source.suffix.lower()=='.png' else 'image/jpeg')}
                data={'model':settings.openai_image_model,'prompt':prompt,'size':'auto','quality':'medium','output_format':'png'}
                r=await client.post('https://api.openai.com/v1/images/edits',headers=headers,data=data,files=files)
        if r.status_code>=400: return source
        payload=r.json(); item=(payload.get('data') or [{}])[0]
        import base64
        if item.get('b64_json'):
            out.write_bytes(base64.b64decode(item['b64_json'])); return out
        if item.get('url'):
            async with httpx.AsyncClient(timeout=120) as c: rr=await c.get(item['url'])
            if rr.status_code<400: out.write_bytes(rr.content); return out
    except Exception:
        return source
    return source
