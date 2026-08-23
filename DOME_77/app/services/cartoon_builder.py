from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from app.core.config import settings
from app.services.animation_library import animation_profile, ensure_animation_library
from app.services.animation_engine.rig_loader import load_character_rig
from app.services.animation_engine.motion_planner import normalize_motion_plan
from app.services.animation_engine.runtime_provider import prepare_character_animation

log = logging.getLogger("dome.cartoon")


def _cartoon_config() -> dict:
    p=Path("config/cartoon.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"first_child_scene_seconds":8}


class CartoonBuildError(RuntimeError):
    pass


def _probe_video(path: Path) -> tuple[int, int, float]:
    ffprobe = settings.ffmpeg_bin.replace("ffmpeg", "ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        return int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"])
    except Exception as probe_exc:
        # Some self-contained desktop ffmpeg runtimes do not ship a separate
        # ffprobe executable. The production image still uses ffprobe, while
        # this metadata-only fallback keeps the renderer portable and testable.
        try:
            result = subprocess.run([settings.ffmpeg_bin,"-hide_banner","-i",str(path),"-t","0","-f","null","-"],check=False,capture_output=True,text=True,timeout=30)
            detail=(result.stderr or '')+(result.stdout or '')
            size=re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b",detail)
            duration=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",detail)
            if not size or not duration:raise ValueError('metadata not present in ffmpeg output')
            seconds=int(duration.group(1))*3600+int(duration.group(2))*60+float(duration.group(3))
            return int(size.group(1)),int(size.group(2)),seconds
        except Exception as exc:
            raise CartoonBuildError(f"Не удалось прочитать параметры базового MP4: {path.name}") from (exc or probe_exc)


def _resolve_normalized_timeline(timeline: list[dict], frame_width: int, frame_height: int) -> list[dict]:
    """Convert authored 0..1 placement into FFmpeg pixels at render time."""

    resolved: list[dict] = []
    for source in timeline:
        segment = dict(source)
        if "height_norm" in segment:
            segment["height"] = max(1, round(float(segment["height_norm"]) * frame_height))
        height = int(segment.get("height", 225))
        if "floor_y_norm" in segment:
            segment["y"] = round(float(segment["floor_y_norm"]) * frame_height - height)
        if "x_norm" in segment:
            segment["x"] = round(float(segment["x_norm"]) * frame_width)
        if "x_start_norm" in segment:
            segment["x_start"] = round(float(segment["x_start_norm"]) * frame_width)
        if "x_end_norm" in segment:
            segment["x_end"] = round(float(segment["x_end_norm"]) * frame_width)
        resolved.append(segment)
    return resolved


def ensure_telegram_safe_mp4(source_mp4: Path, output_mp4: Path | None = None) -> Path:
    """Return an MP4 small enough for reliable Telegram Bot API delivery.

    The lesson base video in v62 is ~62 MB, which can exceed Telegram bot upload
    limits and previously left the session stuck after "Собираю мультфильм…".
    Re-encode only when needed; otherwise copy/return the original file.
    """
    source_mp4 = Path(source_mp4)
    output_mp4 = Path(output_mp4 or source_mp4)
    if not source_mp4.exists() or source_mp4.stat().st_size < 10_000:
        raise CartoonBuildError(f"Нет полноценного MP4 для отправки: {source_mp4}")

    cfg = _cartoon_config()
    max_bytes = int(cfg.get("telegram_video_max_bytes", 45_000_000))
    if source_mp4.stat().st_size <= max_bytes:
        if output_mp4 != source_mp4:
            import shutil
            output_mp4.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_mp4, output_mp4)
        return output_mp4

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_mp4.with_suffix('.telegram.tmp.mp4')
    tmp.unlink(missing_ok=True)
    log.warning(
        "Telegram-safe transcode required: source=%s bytes=%s limit=%s",
        source_mp4, source_mp4.stat().st_size, max_bytes,
    )
    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(source_mp4),
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "2400k", "-maxrate", "2600k", "-bufsize", "5200k",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(tmp),
    ]
    try:
        timeout = int(cfg.get("telegram_transcode_timeout_seconds", 90))
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        tmp.unlink(missing_ok=True)
        raise CartoonBuildError("Сжатие мультфильма для Telegram превысило лимит времени.") from exc
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        detail = (exc.stderr or b"").decode("utf-8", errors="ignore")
        log.error("Telegram-safe transcode failed: %s", detail[-4000:])
        raise CartoonBuildError("Не удалось подготовить мультфильм для отправки в Telegram.") from exc

    if not tmp.exists() or tmp.stat().st_size < 10_000:
        raise CartoonBuildError("После сжатия не получен полноценный MP4.")
    if tmp.stat().st_size > max_bytes:
        size = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise CartoonBuildError(f"MP4 после сжатия всё ещё слишком большой: {size} bytes")
    tmp.replace(output_mp4)
    log.info("Telegram-safe MP4 ready: %s bytes=%s", output_mp4, output_mp4.stat().st_size)
    return output_mp4


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


def build_timeline_cartoon(base_video: Path, character_png: Path, audio_by_phrase: dict[str, Path], timeline: list[dict], output_mp4: Path, base_video_filters: list[str] | None = None) -> Path:
    """Render one cartoon with bounded CPU/RAM and actionable Railway logs."""
    if not base_video.exists():
        raise CartoonBuildError(f"Не найдена основа мультфильма: {base_video}")
    if not character_png.exists():
        raise CartoonBuildError(f"Не найден герой: {character_png}")
    if not timeline:
        raise CartoonBuildError("Timeline пуст.")
    frame_width, frame_height, base_duration = _probe_video(base_video)
    timeline = _resolve_normalized_timeline([dict(x) for x in timeline], frame_width, frame_height)
    minimum=float(_cartoon_config().get("first_child_scene_seconds",8))
    first=timeline[0]
    first["end"]=max(float(first.get("end",0)),float(first.get("visible_start",0))+minimum)

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ensure_animation_library(settings.storage_root / "animation-library")
    # v49: load a reusable character rig if one exists. The current FFmpeg path remains
    # the stable fallback until a full-body AnimationProvider is configured.
    _rig = load_character_rig(character_png, settings.storage_root / "character-rigs")
    _motion_plans = [normalize_motion_plan(item) for item in timeline]
    work = output_mp4.parent / f"{output_mp4.stem}_work"
    work.mkdir(parents=True, exist_ok=True)
    error_file = work / "ffmpeg_error.txt"
    command_file = work / "ffmpeg_command.txt"

    # v57: final delivery must be fast. Reuse already-generated full-body clips,
    # but never start slow external animation generation while the child waits.
    # Missing cached motion falls back immediately to the stable PNG animation.
    allow_generate_during_render=bool(_cartoon_config().get("generate_missing_animation_during_render",False))
    render_threads=max(1,min(4,int(_cartoon_config().get("render_threads",2))))
    ai_clips: list[Path | None] = []
    for idx, segment in enumerate(timeline):
        try:
            ai_clips.append(prepare_character_animation(character_png, segment, work / "ai-animation", Path(audio_by_phrase[segment["phrase_id"]]) if audio_by_phrase.get(segment["phrase_id"]) else None, allow_generate=allow_generate_during_render))
        except Exception as exc:
            log.warning("AI animation fallback for scene %s: %s", idx, exc)
            ai_clips.append(None)

    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
        "-threads", str(render_threads), "-filter_threads", str(render_threads), "-filter_complex_threads", str(render_threads),
        "-i", str(base_video), "-loop", "1", "-framerate", "30", "-i", str(character_png),
    ]
    clip_inputs: dict[int, int] = {}
    next_input = 2
    for idx, clip in enumerate(ai_clips):
        if clip:
            cmd += ["-stream_loop", "-1", "-i", str(clip)]
            clip_inputs[idx] = next_input
            next_input += 1

    audio_segments = []
    for segment in timeline:
        path = audio_by_phrase.get(segment["phrase_id"])
        if path and Path(path).exists() and Path(path).stat().st_size > 0:
            cmd += ["-i", str(path)]
            audio_segments.append({**segment, "input_index": next_input})
            next_input += 1

    filters: list[str] = []
    timeline_end = max(float(item["end"]) for item in timeline)
    pad_seconds = max(0.0, timeline_end - base_duration)
    # Split the PNG only for fallback scenes. AI clips are green-screen keyed individually.
    fallback_indices=[i for i,c in enumerate(ai_clips) if not c]
    if fallback_indices:
        split_labels = "".join(f"[hero{i}]" for i in fallback_indices)
        filters.append(f"[1:v]format=rgba,split={len(fallback_indices)}{split_labels}")
    base_source="[0:v]"
    if base_video_filters:
        filters.append(f"[0:v]{','.join(base_video_filters)}[localizedv]")
        base_source="[localizedv]"
    if pad_seconds > 0.001:
        filters.append(f"{base_source}tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}[basev]")
        previous = "[basev]"
    else:
        previous = base_source
    for idx, segment in enumerate(timeline):
        height = int(segment.get("height", 225))
        start = float(segment["visible_start"])
        end = float(segment["end"])
        fade_out_start = max(start, end - 0.18)
        if ai_clips[idx]:
            src=clip_inputs[idx]
            # Remove the provider's solid green background. Similarity/blend are deliberately
            # conservative to preserve green details on the character if present.
            filters.append(
                f"[{src}:v]setpts=PTS-STARTPTS,chromakey=0x00FF00:0.28:0.08,format=rgba,"
                f"scale=-1:{height},fade=t=in:st=0:d=0.12:alpha=1,fade=t=out:st={max(.01,end-start-.18)}:d=.18:alpha=1[hs{idx}]"
            )
        else:
            profile = animation_profile(segment.get("animation", "stand_front_talk"), settings.storage_root / "animation-library")
            pre = "hflip," if bool(profile.get("mirror", False)) else ""
            rotation = float(profile.get("rotation", 0.012))
            filters.append(
                f"[hero{idx}]{pre}scale=-1:{height},"
                f"rotate='{rotation}*sin(3.2*(t-{start}))':ow=rotw(iw):oh=roth(ih):c=none,"
                f"fade=t=in:st={start}:d=0.15:alpha=1,fade=t=out:st={fade_out_start}:d=0.18:alpha=1[hs{idx}]"
            )
        out = f"[v{idx}]"
        xexpr=_x_expression(segment)
        yexpr=_y_expression(segment)
        if ai_clips[idx]:
            # AI clip timestamps start at zero, but overlay placement follows lesson timeline.
            filters.append(f"{previous}[hs{idx}]overlay=x='{xexpr}':y='{yexpr}':enable='between(t,{start},{end})':eof_action=pass{out}")
        else:
            filters.append(f"{previous}[hs{idx}]overlay=x='{xexpr}':y='{yexpr}':enable='between(t,{start},{end})':eof_action=pass{out}")
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
        if pad_seconds > 0.001:
            filters.append(f"[0:a]apad=pad_dur={pad_seconds:.3f}[basea]")
            filters.append("[basea][voice]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.891[aout]")
        else:
            filters.append("[0:a][voice]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.891[aout]")

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    cmd += ["-map", "[aout]"] if audio_segments else ["-map", "0:a?"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-maxrate", "2600k", "-bufsize", "5200k",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        "-t", f"{max(base_duration, timeline_end):.3f}", str(output_mp4),
    ]
    command_file.write_text(" ".join(cmd), encoding="utf-8")
    log.info("Rendering cartoon: base=%s character=%s voices=%s output=%s", base_video, character_png, len(audio_segments), output_mp4)
    try:
        ffmpeg_timeout=int(_cartoon_config().get("ffmpeg_timeout_seconds",180))
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=ffmpeg_timeout)
        if result.stderr:
            log.info("FFmpeg warnings: %s", result.stderr.decode("utf-8", errors="ignore")[-2000:])
    except FileNotFoundError as exc:
        log.exception("FFmpeg executable not found: %s", settings.ffmpeg_bin)
        raise CartoonBuildError("FFmpeg не установлен в контейнере.") from exc
    except subprocess.TimeoutExpired as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="ignore")
        error_file.write_text(detail or "FFmpeg timed out", encoding="utf-8")
        log.error("FFmpeg timed out. stderr=%s", detail[-4000:])
        raise CartoonBuildError("Сборка мультфильма превысила допустимое время. Ошибка записана в Railway Logs.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="ignore")
        error_file.write_text(detail, encoding="utf-8")
        log.error("FFmpeg failed code=%s command=%s stderr=%s", exc.returncode, command_file, detail[-8000:])
        raise CartoonBuildError(f"FFmpeg завершился с кодом {exc.returncode}. Подробности выведены в Railway Logs.") from exc

    if not output_mp4.exists() or output_mp4.stat().st_size < 10_000:
        raise CartoonBuildError("FFmpeg не создал полноценный MP4-файл.")
    log.info("Cartoon ready: %s bytes=%s", output_mp4, output_mp4.stat().st_size)
    return ensure_telegram_safe_mp4(output_mp4)


def build_simple_cartoon(character_png: Path, audio_files: list[Path], output_mp4: Path) -> Path:
    lesson_path = settings.content_root / "lessons" / "demo_001" / "lesson.json"
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    base = settings.content_root / "lessons" / "demo_001" / lesson["cartoon_base"]
    ids = [item["phrase_id"] for item in lesson["required_phrases"]]
    return build_timeline_cartoon(base, character_png, dict(zip(ids, audio_files)), lesson["timeline"], output_mp4)
