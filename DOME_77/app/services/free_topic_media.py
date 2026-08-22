from __future__ import annotations
import base64, hashlib, io, textwrap
from pathlib import Path
import httpx
from PIL import Image, ImageDraw, ImageFont
from app.core.config import settings

def _fallback_png(path: Path, topic: str, title: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im=Image.new('RGB',(1024,1024),(246,249,255)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((70,70,954,954),radius=54,fill=(255,255,255),outline=(205,216,235),width=5)
    try: f=ImageFont.truetype('DejaVuSans.ttf',54); f2=ImageFont.truetype('DejaVuSans.ttf',38)
    except Exception: f=f2=None
    lines=textwrap.wrap(topic,24); y=300
    for line in lines: d.text((120,y),line,fill=(34,58,96),font=f); y+=70
    for line in textwrap.wrap(title,36): d.text((120,y+40),line,fill=(74,91,120),font=f2); y+=50
    d.text((120,760),'DOME',fill=(90,110,150),font=f2); im.save(path,'PNG'); return path

async def ensure_free_topic_image(child_id:int, lesson_key:str, slide:dict, topic:str) -> Path:
    prompt=str(slide.get('image_prompt') or f'Bright educational illustration about {topic}, no text')
    digest=hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:18]
    path=settings.storage_root/'children'/str(child_id)/'free-topic-media'/lesson_key/f'{digest}.png'
    if path.exists() and path.stat().st_size>5000: return path
    path.parent.mkdir(parents=True,exist_ok=True)
    if settings.openai_api_key:
        headers={'Authorization':f'Bearer {settings.openai_api_key}','Content-Type':'application/json'}
        payload={'model':settings.openai_image_model,'prompt':prompt+', visually clear for a child language lesson, no written words, no logos','size':'1024x1024','quality':'low','output_format':'png'}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r=await client.post('https://api.openai.com/v1/images/generations',headers=headers,json=payload)
            r.raise_for_status(); data=r.json().get('data') or []
            if data and data[0].get('b64_json'):
                path.write_bytes(base64.b64decode(data[0]['b64_json'])); return path
        except Exception:
            pass
    return _fallback_png(path,topic,str(slide.get('title') or ''))


def ensure_free_topic_clip(image_path: Path, output_path: Path, seconds: int = 6) -> Path | None:
    """Create a short real MP4 visual stage from the current lesson illustration.

    This gives the learner an actual video stage even when no approved external clip
    is attached yet. It is deterministic and cheap; later the builder may attach a
    licensed/approved video instead.
    """
    import subprocess
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 10000:
        return output_path
    try:
        subprocess.run([
            settings.ffmpeg_bin,'-y','-loop','1','-framerate','30','-i',str(image_path),
            '-t',str(max(4,min(12,int(seconds)))),
            '-vf',"scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,zoompan=z='min(zoom+0.0012,1.08)':d=180:s=1280x720:fps=30",
            '-an','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p','-movflags','+faststart',str(output_path)
        ],check=True,capture_output=True,timeout=180)
        return output_path if output_path.exists() else None
    except Exception:
        return None


async def ensure_free_topic_item_image(child_id:int, lesson_key:str, item_label:str, topic:str, index:int) -> Path:
    """Generate/cache one clean isolated object picture for visual drag tasks."""
    safe_label=(item_label or f"object {index}").strip()
    prompt=(f"Single isolated child-friendly object for a language-learning drag-and-drop game about {topic}: {safe_label}. "
            "Centered object, plain light background, no text, no letters, no logos, no extra objects.")
    digest=hashlib.sha256((prompt+str(index)).encode('utf-8')).hexdigest()[:18]
    path=settings.storage_root/'children'/str(child_id)/'free-topic-media'/lesson_key/f'item_{index}_{digest}.png'
    if path.exists() and path.stat().st_size>5000: return path
    path.parent.mkdir(parents=True,exist_ok=True)
    if settings.openai_api_key:
        headers={'Authorization':f'Bearer {settings.openai_api_key}','Content-Type':'application/json'}
        payload={'model':settings.openai_image_model,'prompt':prompt,'size':'1024x1024','quality':'low','output_format':'png'}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r=await client.post('https://api.openai.com/v1/images/generations',headers=headers,json=payload)
            r.raise_for_status(); data=r.json().get('data') or []
            if data and data[0].get('b64_json'):
                path.write_bytes(base64.b64decode(data[0]['b64_json'])); return path
        except Exception:
            pass
    return _fallback_png(path,topic,safe_label)
