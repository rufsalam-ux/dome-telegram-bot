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

def cartoon_text_filters(lesson_dir: Path, target_language: str) -> list[str]:
    """Return FFmpeg video filters for one-pass movie localization."""
    if target_language == "ru": return []
    linux_font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    windows_font=Path('C:/Windows/Fonts/arial.ttf')
    font_path=linux_font if linux_font.exists() else windows_font
    # drawtext parses ':' as an option separator even when subprocess is used
    # without a shell. Quote the whole path and escape only the drive colon.
    font_value=font_path.as_posix().replace(':','\\:')
    font_arg=f"fontfile='{font_value}'" if font_path.exists() else "font=Sans"
    filters=[]
    for item in load_overlay_config(lesson_dir):
        text=str((item.get("text_by_language") or {}).get(target_language) or "").strip()
        if not text and not bool(item.get("cover_only")): continue
        st=float(item["start"]); en=float(item["end"]); x=int(item["x"]); y=int(item["y"]); w=int(item["w"]); h=int(item["h"]); fs=int(item.get("font_size",36))
        safe=text.replace("\\","\\\\").replace(":","\\:").replace("'","\\'")
        enable=f"between(t,{st},{en})";background=str(item.get("background_color") or "white@0.96");foreground=str(item.get("font_color") or "black")
        filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={background}:t=fill:enable='{enable}'")
        if text:filters.append(f"drawtext={font_arg}:text='{safe}':x={x+12}:y={y+12}:fontsize={fs}:fontcolor={foreground}:enable='{enable}'")
    return filters

def apply_cartoon_text_overlays(video: Path, output: Path, lesson_dir: Path, target_language: str) -> Path:
    """Overlay pre-authored localized video text. Russian target is untouched.
    Each entry: start,end,x,y,w,h,text_by_language. Original Russian is covered first.
    Coordinates are pixels on the authored base-video canvas. Entries may be
    cover_only for credits/instructions that should simply be removed.
    """
    if target_language == "ru" or not video.exists(): return video
    filters=cartoon_text_filters(lesson_dir,target_language)
    if not filters:return video
    tmp=output.parent/(output.stem+"_localized.mp4")
    cmd=[settings.ffmpeg_bin,"-y","-i",str(video),"-vf",','.join(filters),"-c:v","libx264","-preset","ultrafast","-crf","23","-c:a","copy",str(tmp)]
    try:
        subprocess.run(cmd,check=True,capture_output=True,timeout=240); tmp.replace(output); return output
    except Exception:
        return video
