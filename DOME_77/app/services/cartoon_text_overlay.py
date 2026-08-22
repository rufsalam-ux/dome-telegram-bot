from __future__ import annotations
import json, subprocess
from pathlib import Path
from app.core.config import settings

def load_overlay_config(lesson_dir: Path) -> list[dict]:
    p=lesson_dir / "cartoon_text_overlays.json"
    if not p.exists(): return []
    try:
        data=json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("overlays") or [])
    except Exception:
        return []

def apply_cartoon_text_overlays(video: Path, output: Path, lesson_dir: Path, target_language: str) -> Path:
    """Overlay pre-authored localized video text. Russian target is untouched.
    Each entry: start,end,x,y,w,h,text_by_language. Original Russian is covered first.
    Coordinates are pixels on 1280x720. Unknown/missing translations are skipped safely.
    """
    if target_language == "ru" or not video.exists(): return video
    entries=[]
    for item in load_overlay_config(lesson_dir):
        text=str((item.get("text_by_language") or {}).get(target_language) or "").strip()
        if text: entries.append((item,text))
    if not entries: return video
    filters=[]
    for item,text in entries:
        st=float(item["start"]); en=float(item["end"]); x=int(item["x"]); y=int(item["y"]); w=int(item["w"]); h=int(item["h"]); fs=int(item.get("font_size",36))
        safe=text.replace("\\","\\\\").replace(":","\\:").replace("'","\\'")
        enable=f"between(t,{st},{en})"
        filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=white@0.96:t=fill:enable='{enable}'")
        filters.append(f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{safe}':x={x+12}:y={y+12}:fontsize={fs}:fontcolor=black:enable='{enable}'")
    tmp=output.parent/(output.stem+"_localized.mp4")
    cmd=[settings.ffmpeg_bin,"-y","-i",str(video),"-vf",','.join(filters),"-c:v","libx264","-preset","ultrafast","-crf","23","-c:a","copy",str(tmp)]
    try:
        subprocess.run(cmd,check=True,capture_output=True,timeout=240); tmp.replace(output); return output
    except Exception:
        return video
