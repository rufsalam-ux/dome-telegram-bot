from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from app.core.config import settings
from app.services.animation_library import animation_profile, ensure_animation_library

log = logging.getLogger("dome.cartoon")


def _cartoon_config() -> dict:
    p=Path("config/cartoon.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"first_child_scene_seconds":8}


class CartoonBuildError(RuntimeError):
    pass


def _x_expression(segment: dict) -> str:
    motion = segment.get("animation", segment.get("motion", "stand_front_talk"))
    start = float(segment["visible_start"])
    end = float(segment["end"])
    talk = float(segment.get("talk_start", start))
    x0 = float(segment.get("x_start", segment.get("x", 180)))
    x1 = float(segment.get("x_end", segment.get("x", x0)))
    if motion in {"walk_from_left", "walk_left_then_talk"}:
        return f"if(lt(t,{talk}),{x0}+({x1}-{x0})*(t-{start})/max({talk-start},0.01),{x1})"
    if motion in {"walk_from_right", "walk_right_to_left_talk", "walk_right_to_left", "walk_left_to_right_talk"}:
        return f"{x0}+({x1}-{x0})*(t-{start})/max({end-start},0.01)"
    return str(float(segment.get("x", x1)))


def _y_expression(segment: dict) -> str:
    y = float(segment.get("y", 245))
    start = float(segment["visible_start"])
    motion = segment.get("animation", segment.get("motion", "stand_front_talk"))
    profile = animation_profile(motion, settings.storage_root / "animation-library")
    if "walk" in motion:
        return f"{y}+{float(profile.get('walk_bob', 4.0))}*abs(sin(8*(t-{start})))"
    if motion == "happy_jump":
        return f"{y}-18*abs(sin(5*(t-{start})))"
    return f"{y}+1.5*sin(2.2*(t-{start}))"


def build_timeline_cartoon(base_video: Path, character_png: Path, audio_by_phrase: dict[str, Path], timeline: list[dict], output_mp4: Path) -> Path:
    """Render one cartoon with bounded CPU/RAM and actionable Railway logs."""
    if not base_video.exists():
        raise CartoonBuildError(f"Не найдена основа мультфильма: {base_video}")
    if not character_png.exists():
        raise CartoonBuildError(f"Не найден герой: {character_png}")
    if not timeline:
        raise CartoonBuildError("Timeline пуст.")
    timeline=[dict(x) for x in timeline]
    minimum=float(_cartoon_config().get("first_child_scene_seconds",8))
    first=timeline[0]
    first["end"]=max(float(first.get("end",0)),float(first.get("visible_start",0))+minimum)

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ensure_animation_library(settings.storage_root / "animation-library")
    work = output_mp4.parent / f"{output_mp4.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    error_file = work / "ffmpeg_error.txt"
    command_file = work / "ffmpeg_command.txt"

    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
        "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1",
        "-i", str(base_video), "-loop", "1", "-framerate", "30", "-i", str(character_png),
    ]
    audio_segments = []
    for segment in timeline:
        path = audio_by_phrase.get(segment["phrase_id"])
        if path and Path(path).exists() and Path(path).stat().st_size > 0:
            cmd += ["-i", str(path)]
            audio_segments.append({**segment, "input_index": len(audio_segments) + 2})

    filters: list[str] = []
    split_labels = "".join(f"[hero{i}]" for i in range(len(timeline)))
    filters.append(f"[1:v]format=rgba,split={len(timeline)}{split_labels}")
    previous = "[0:v]"
    for idx, segment in enumerate(timeline):
        height = int(segment.get("height", 225))
        start = float(segment["visible_start"])
        end = float(segment["end"])
        profile = animation_profile(segment.get("animation", "stand_front_talk"), settings.storage_root / "animation-library")
        pre = "hflip," if bool(profile.get("mirror", False)) else ""
        rotation = float(profile.get("rotation", 0.012))
        fade_out_start = max(start, end - 0.18)
        filters.append(
            f"[hero{idx}]{pre}scale=-1:{height},"
            f"rotate='{rotation}*sin(3.2*(t-{start}))':ow=rotw(iw):oh=roth(ih):c=none,"
            f"fade=t=in:st={start}:d=0.15:alpha=1,fade=t=out:st={fade_out_start}:d=0.18:alpha=1[hs{idx}]"
        )
        out = f"[v{idx}]"
        filters.append(
            f"{previous}[hs{idx}]overlay=x='{_x_expression(segment)}':y='{_y_expression(segment)}':"
            f"enable='between(t,{start},{end})':eof_action=pass{out}"
        )
        previous = out
    filters.append(f"{previous}format=yuv420p[vout]")

    if audio_segments:
        labels = []
        for idx, segment in enumerate(audio_segments):
            delay = int(round(float(segment.get("talk_start", segment["visible_start"])) * 1000))
            label = f"[a{idx}]"
            filters.append(
                f"[{segment['input_index']}:a]atrim=0:5,asetpts=PTS-STARTPTS,"
                f"highpass=f=80,acompressor=threshold=-20dB:ratio=3:makeup=3,"
                f"alimiter=limit=0.891,adelay={delay}|{delay}{label}"
            )
            labels.append(label)
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[voice]")
        # Mix voices with the original soundtrack. Avoid sidechaincompress here: it was fragile
        # across FFmpeg builds and could abort the entire render.
        filters.append("[0:a][voice]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.891[aout]")

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    cmd += ["-map", "[aout]"] if audio_segments else ["-map", "0:a?"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        "-shortest", str(output_mp4),
    ]
    command_file.write_text(" ".join(cmd), encoding="utf-8")
    log.info("Rendering cartoon: base=%s character=%s voices=%s output=%s", base_video, character_png, len(audio_segments), output_mp4)
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=420)
        if result.stderr:
            log.info("FFmpeg warnings: %s", result.stderr.decode("utf-8", errors="ignore")[-2000:])
    except FileNotFoundError as exc:
        log.exception("FFmpeg executable not found: %s", settings.ffmpeg_bin)
        raise CartoonBuildError("FFmpeg не установлен в контейнере.") from exc
    except subprocess.TimeoutExpired as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="ignore")
        error_file.write_text(detail or "FFmpeg timed out after 420 seconds", encoding="utf-8")
        log.error("FFmpeg timed out. stderr=%s", detail[-4000:])
        raise CartoonBuildError("Сборка мультфильма превысила 7 минут. Ошибка записана в Railway Logs.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="ignore")
        error_file.write_text(detail, encoding="utf-8")
        log.error("FFmpeg failed code=%s command=%s stderr=%s", exc.returncode, command_file, detail[-8000:])
        raise CartoonBuildError(f"FFmpeg завершился с кодом {exc.returncode}. Подробности выведены в Railway Logs.") from exc

    if not output_mp4.exists() or output_mp4.stat().st_size < 10_000:
        raise CartoonBuildError("FFmpeg не создал полноценный MP4-файл.")
    log.info("Cartoon ready: %s bytes=%s", output_mp4, output_mp4.stat().st_size)
    return output_mp4


def build_simple_cartoon(character_png: Path, audio_files: list[Path], output_mp4: Path) -> Path:
    lesson_path = settings.content_root / "lessons" / "demo_001" / "lesson.json"
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    base = settings.content_root / "lessons" / "demo_001" / lesson["cartoon_base"]
    ids = [item["phrase_id"] for item in lesson["required_phrases"]]
    return build_timeline_cartoon(base, character_png, dict(zip(ids, audio_files)), lesson["timeline"], output_mp4)
