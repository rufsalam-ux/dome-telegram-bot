from __future__ import annotations
import json, logging, subprocess
from pathlib import Path
from app.core.config import settings

log=logging.getLogger('dome.cartoon_text')

def load_overlay_document(lesson_dir: Path) -> dict:
    p=lesson_dir / "cartoon_text_overlays.json"
    if not p.exists():return {}
    try:return dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:return {}

def load_overlay_config(lesson_dir: Path) -> list[dict]:
    return list(load_overlay_document(lesson_dir).get("overlays") or [])

def _overlap(a:dict,b:dict)->bool:
    windows=b.get('windows') or []
    if windows:
        item_start=float(a.get('start',0));item_end=float(a.get('end',item_start))
        if not any(item_start<float(window[1]) and item_end>float(window[0]) for window in windows if isinstance(window,list) and len(window)==2):
            return False
    return int(a['x'])<int(b['x'])+int(b['w']) and int(a['x'])+int(a['w'])>int(b['x']) and int(a['y'])<int(b['y'])+int(b['h']) and int(a['y'])+int(a['h'])>int(b['y'])

def overlay_safe_zone_violations(lesson_dir:Path)->list[tuple[str,int]]:
    document=load_overlay_document(lesson_dir);zones=list(document.get('protected_zones') or []);violations=[]
    for index,item in enumerate(document.get('overlays') or []):
        for zone in zones:
            if _overlap(item,zone):violations.append((str(zone.get('id') or 'protected'),index))
    return violations

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
    filters=[];document=load_overlay_document(lesson_dir);protected=list(document.get('protected_zones') or [])
    for index,item in enumerate(document.get('overlays') or []):
        conflicts=[str(zone.get('id') or 'protected') for zone in protected if _overlap(item,zone)]
        if conflicts:
            log.error('Skipping unsafe movie caption index=%s protected=%s',index,conflicts);continue
        text=str((item.get("text_by_language") or {}).get(target_language) or "").strip()
        if not text and not bool(item.get("cover_only")): continue
        st=float(item["start"]); en=float(item["end"]); x=int(item["x"]); y=int(item["y"]); w=int(item["w"]); h=int(item["h"]); fs=int(item.get("font_size",36))
        safe=text.replace("\\","\\\\").replace(":","\\:").replace("'","\\'")
        # Source-language glyphs must not remain ghosted below a translated
        # caption. Full opacity is deterministic and still respects every
        # protected zone checked above.
        enable=f"between(t,{st},{en})";background=str(item.get("background_color") or "white@1.0");foreground=str(item.get("font_color") or "black")
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
