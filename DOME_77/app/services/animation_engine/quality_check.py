from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.core.config import settings


def _duration(path: Path) -> float:
    cmd=[settings.ffmpeg_bin, "-i", str(path)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
    import re
    m=re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    return int(m.group(1))*3600+int(m.group(2))*60+float(m.group(3)) if m else 0.0


def technical_ok(path: Path, expected_seconds: float) -> bool:
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    d=_duration(path)
    return d >= min(2.0, max(1.0, expected_seconds * .45))


def _sample_frames(video: Path, work: Path) -> list[Path]:
    work.mkdir(parents=True,exist_ok=True)
    pattern=work/'qc_%02d.jpg'
    subprocess.run([settings.ffmpeg_bin,'-y','-i',str(video),'-vf','fps=1/2,scale=512:-2','-frames:v','3',str(pattern)],check=False,capture_output=True,timeout=40)
    return sorted(work.glob('qc_*.jpg'))[:3]


def vision_ok(video: Path, reference: Path, description_ru: str) -> bool:
    if not settings.character_animation_qc or not settings.openai_api_key.strip():
        return True
    try:
        with tempfile.TemporaryDirectory() as td:
            frames=_sample_frames(video,Path(td))
            if not frames:
                return False
            def data_url(p:Path):
                mime='image/png' if p.suffix.lower()=='.png' else 'image/jpeg'
                return f"data:{mime};base64,"+base64.b64encode(p.read_bytes()).decode()
            content=[{"type":"text","text":(
                "Проверь AI-анимацию детского рисованного персонажа. Исходный герой — первое изображение, затем кадры видео. "
                f"Требуемое действие: {description_ru}. Ответь ТОЛЬКО JSON вида {{\"pass\":true/false}}. "
                "pass=false если герой заметно изменился, появились лишние/сломанные конечности, искажено лицо/одежда, несколько героев, либо движение явно не соответствует описанию."
            )},{"type":"image_url","image_url":{"url":data_url(reference)}}]
            for f in frames:
                content.append({"type":"image_url","image_url":{"url":data_url(f)}})
            payload={"model":settings.openai_text_model,"messages":[{"role":"user","content":content}],"temperature":0,"max_tokens":40}
            r=httpx.post('https://api.openai.com/v1/chat/completions',headers={'Authorization':'Bearer '+settings.openai_api_key,'Content-Type':'application/json'},json=payload,timeout=45)
            r.raise_for_status()
            text=(r.json()['choices'][0]['message']['content'] or '').strip()
            import re
            m=re.search(r'\{.*\}',text,re.S)
            if not m: return True
            return bool(json.loads(m.group(0)).get('pass',True))
    except Exception:
        # QC must never break the lesson; technical validation still applies.
        return True


def animation_ok(video: Path, reference: Path, expected_seconds: float, description_ru: str) -> bool:
    return technical_ok(video, expected_seconds) and vision_ok(video, reference, description_ru)
