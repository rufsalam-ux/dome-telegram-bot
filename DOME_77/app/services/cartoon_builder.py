from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import time
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


SAFE_MOVIE_ERROR = "Мультфильм пока не удалось собрать. Все записи сохранены — попробуйте ещё раз."


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


def _tail_text(path: Path, limit: int = 8000) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read(limit).decode("utf-8", errors="ignore")


def _run_ffmpeg_step(cmd: list[str], *, step: str, work: Path, timeout: float) -> None:
    """Run one bounded FFmpeg stage without buffering media or unbounded stderr in RAM."""

    command_file = work / f"{step}.command.txt"
    error_file = work / f"{step}.stderr.log"
    command_file.write_text(" ".join(cmd), encoding="utf-8")
    started = time.monotonic()
    log.info("MOBILE_MOVIE_FFMPEG_START step=%s timeout=%.1f", step, timeout)
    try:
        with error_file.open("wb") as error_stream:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=error_stream,
                timeout=max(5.0, timeout),
            )
    except FileNotFoundError as exc:
        log.exception("FFmpeg executable not found step=%s executable=%s", step, settings.ffmpeg_bin)
        raise CartoonBuildError(SAFE_MOVIE_ERROR) from exc
    except subprocess.TimeoutExpired as exc:
        log.error("FFmpeg timed out step=%s command=%s stderr=%s", step, command_file, _tail_text(error_file, 4000))
        raise CartoonBuildError(SAFE_MOVIE_ERROR) from exc
    except subprocess.CalledProcessError as exc:
        log.error(
            "FFmpeg failed step=%s code=%s command=%s stderr=%s",
            step,
            exc.returncode,
            command_file,
            _tail_text(error_file),
        )
        raise CartoonBuildError(SAFE_MOVIE_ERROR) from exc
    log.info("MOBILE_MOVIE_FFMPEG_DONE step=%s elapsed=%.2f", step, time.monotonic() - started)


_BETWEEN_RE = re.compile(r"between\(t,\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\)")


def _shift_timed_filters(filters: list[str] | None, offset: float) -> list[str]:
    """Translate authored absolute timeline filters into one local segment timeline."""

    def replace(match: re.Match[str]) -> str:
        return f"between(t,{float(match.group(1)) - offset:.3f},{float(match.group(2)) - offset:.3f})"

    return [_BETWEEN_RE.sub(replace, value) for value in (filters or [])]


def _render_windows(timeline: list[dict], duration: float) -> list[tuple[float, float]]:
    boundaries = {0.0, round(duration, 6)}
    for segment in timeline:
        boundaries.add(max(0.0, min(duration, float(segment["visible_start"]))))
        boundaries.add(max(0.0, min(duration, float(segment["end"]))))
    ordered = sorted(boundaries)
    return [(start, end) for start, end in zip(ordered, ordered[1:]) if end - start >= 0.01]


def _local_segment(segment: dict, window_start: float, window_end: float) -> dict:
    local = dict(segment)
    local["visible_start"] = max(0.0, float(segment["visible_start"]) - window_start)
    local["talk_start"] = max(0.0, float(segment.get("talk_start", segment["visible_start"])) - window_start)
    local["end"] = min(window_end - window_start, float(segment["end"]) - window_start)
    return local


def _concat_line(path: Path) -> str:
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'"


def _has_audio_stream(path: Path) -> bool:
    ffprobe = settings.ffmpeg_bin.replace("ffmpeg", "ffprobe")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return bool(result.stdout.strip())
    except Exception:
        try:
            result = subprocess.run(
                [settings.ffmpeg_bin, "-hide_banner", "-i", str(path), "-t", "0", "-f", "null", "-"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return "Audio:" in (result.stderr or "")
        except Exception:
            return False


def _build_voice_track(
    audio_segments: list[dict],
    duration: float,
    output: Path,
    work: Path,
    render_threads: int,
    timeout: float,
) -> Path:
    """Stream voices in authored order; never allocate ten full delayed tracks."""

    ordered = sorted(audio_segments, key=lambda row: float(row.get("talk_start", row["visible_start"])))
    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
        "-threads", str(render_threads), "-filter_threads", "1", "-filter_complex_threads", "1",
    ]
    for segment in ordered:
        cmd += ["-i", str(segment["audio_path"])]

    filters: list[str] = []
    pieces: list[str] = []
    cursor = 0.0
    for index, segment in enumerate(ordered):
        start = max(cursor, min(duration, float(segment.get("talk_start", segment["visible_start"]))))
        if start - cursor >= 0.001:
            label = f"[silence{index}]"
            filters.append(f"anullsrc=r=48000:cl=stereo:d={start - cursor:.3f},asetpts=PTS-STARTPTS{label}")
            pieces.append(label)
        end = max(start + 0.05, min(duration, float(segment.get("end", start + 5.0))))
        slot_duration = min(duration - start, end - start)
        label = f"[voice{index}]"
        filters.append(
            f"[{index}:a]atrim=0:{slot_duration:.3f},asetpts=PTS-STARTPTS,"
            "highpass=f=80,acompressor=threshold=-20dB:ratio=3:makeup=3,alimiter=limit=0.891,"
            f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=pad_dur={slot_duration:.3f},"
            f"atrim=0:{slot_duration:.3f}{label}"
        )
        pieces.append(label)
        cursor = start + slot_duration
    if duration - cursor >= 0.001:
        label = "[silence_final]"
        filters.append(f"anullsrc=r=48000:cl=stereo:d={duration - cursor:.3f},asetpts=PTS-STARTPTS{label}")
        pieces.append(label)
    filters.append(f"{''.join(pieces)}concat=n={len(pieces)}:v=0:a=1[aout]")
    cmd += [
        "-filter_complex", ";".join(filters), "-map", "[aout]",
        "-c:a", "aac", "-b:a", "128k", "-t", f"{duration:.3f}", str(output),
    ]
    _run_ffmpeg_step(cmd, step="voice_track", work=work, timeout=timeout)
    return output


def build_timeline_cartoon(base_video: Path, character_png: Path, audio_by_phrase: dict[str, Path], timeline: list[dict], output_mp4: Path, base_video_filters: list[str] | None = None) -> Path:
    """Render the authored movie with disk-backed sequential FFmpeg stages.

    The former single graph split a 1080p hero stream ten times and kept ten
    delayed audio streams alive for the full film. On Railway that graph hit
    the 1 GB cgroup hard limit. Here each video window is rendered separately,
    at most the currently visible hero is decoded, voices are concatenated as
    a streaming audio track, and the final video concat/mux uses stream copy.
    """
    if not base_video.exists():
        log.error("Movie base is missing: %s", base_video)
        raise CartoonBuildError(SAFE_MOVIE_ERROR)
    if not character_png.exists():
        log.error("Movie hero is missing: %s", character_png)
        raise CartoonBuildError(SAFE_MOVIE_ERROR)
    if not timeline:
        log.error("Movie timeline is empty")
        raise CartoonBuildError(SAFE_MOVIE_ERROR)

    cfg = _cartoon_config()
    frame_width, frame_height, base_duration = _probe_video(base_video)
    timeline = _resolve_normalized_timeline([dict(item) for item in timeline], frame_width, frame_height)
    minimum = float(cfg.get("first_child_scene_seconds", 8))
    timeline[0]["end"] = max(float(timeline[0].get("end", 0)), float(timeline[0].get("visible_start", 0)) + minimum)
    timeline_end = max(float(item["end"]) for item in timeline)
    render_duration = max(base_duration, timeline_end)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ensure_animation_library(settings.storage_root / "animation-library")
    _rig = load_character_rig(character_png, settings.storage_root / "character-rigs")
    _motion_plans = [normalize_motion_plan(item) for item in timeline]

    allow_generate = bool(cfg.get("generate_missing_animation_during_render", False))
    render_threads = max(1, min(2, int(cfg.get("render_threads", 1))))
    total_timeout = max(60, int(cfg.get("total_render_timeout_seconds", 600)))
    step_timeout = max(30, int(cfg.get("ffmpeg_timeout_seconds", 300)))
    render_started = time.monotonic()

    def remaining_timeout() -> float:
        remaining = total_timeout - (time.monotonic() - render_started)
        if remaining <= 5:
            raise CartoonBuildError(SAFE_MOVIE_ERROR)
        return min(float(step_timeout), remaining)

    with tempfile.TemporaryDirectory(prefix=f"{output_mp4.stem}_render_", dir=output_mp4.parent) as work_value:
        work = Path(work_value)
        ai_clips: list[Path | None] = []
        for index, segment in enumerate(timeline):
            try:
                phrase_audio = Path(audio_by_phrase[segment["phrase_id"]]) if audio_by_phrase.get(segment["phrase_id"]) else None
                ai_clips.append(prepare_character_animation(character_png, segment, work / "ai-animation", phrase_audio, allow_generate=allow_generate))
            except Exception as exc:
                log.warning("AI animation fallback for scene %s: %s", index, exc)
                ai_clips.append(None)

        segment_files: list[Path] = []
        windows = _render_windows(timeline, render_duration)
        log.info(
            "MOBILE_MOVIE_RENDER_PLAN duration=%.3f windows=%s voices=%s threads=%s",
            render_duration,
            len(windows),
            sum(1 for item in timeline if audio_by_phrase.get(item["phrase_id"])),
            render_threads,
        )
        for window_index, (window_start, window_end) in enumerate(windows):
            window_duration = window_end - window_start
            active = [
                index for index, item in enumerate(timeline)
                if float(item["visible_start"]) < window_end - 0.001 and float(item["end"]) > window_start + 0.001
            ]
            cmd = [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
                "-threads", str(render_threads), "-filter_threads", "1", "-filter_complex_threads", "1",
                "-ss", f"{min(window_start, max(0.0, base_duration - 0.05)):.3f}", "-i", str(base_video),
            ]
            input_by_scene: dict[int, int] = {}
            for scene_index in active:
                clip = ai_clips[scene_index]
                if clip:
                    cmd += ["-stream_loop", "-1", "-i", str(clip)]
                else:
                    cmd += ["-loop", "1", "-framerate", "15", "-i", str(character_png)]
                input_by_scene[scene_index] = len(input_by_scene) + 1

            base_chain = ["setpts=PTS-STARTPTS", *_shift_timed_filters(base_video_filters, window_start)]
            pad = max(0.0, window_end - base_duration)
            if pad > 0.001:
                base_chain.append(f"tpad=stop_mode=clone:stop_duration={pad + 0.05:.3f}")
            filters = [f"[0:v]{','.join(base_chain)}[basev]"]
            previous = "[basev]"
            for local_index, scene_index in enumerate(active):
                segment = _local_segment(timeline[scene_index], window_start, window_end)
                height = int(segment.get("height", 225))
                start = float(segment["visible_start"])
                end = float(segment["end"])
                hero_label = f"[hero{local_index}]"
                source = input_by_scene[scene_index]
                if ai_clips[scene_index]:
                    filters.append(
                        f"[{source}:v]setpts=PTS-STARTPTS,chromakey=0x00FF00:0.28:0.08,format=rgba,"
                        f"scale=-1:{height},fade=t=in:st={start:.3f}:d=0.12:alpha=1,"
                        f"fade=t=out:st={max(start, end - 0.18):.3f}:d=0.18:alpha=1{hero_label}"
                    )
                else:
                    profile = animation_profile(segment.get("animation", "stand_front_talk"), settings.storage_root / "animation-library")
                    pre = "hflip," if bool(profile.get("mirror", False)) else ""
                    rotation = float(profile.get("rotation", 0.012))
                    filters.append(
                        f"[{source}:v]setpts=PTS-STARTPTS,{pre}scale=-1:{height},"
                        f"rotate='{rotation}*sin(3.2*(t-{start:.3f}))':ow=rotw(iw):oh=roth(ih):c=none,"
                        f"fade=t=in:st={start:.3f}:d=0.15:alpha=1,"
                        f"fade=t=out:st={max(start, end - 0.18):.3f}:d=0.18:alpha=1{hero_label}"
                    )
                out = f"[overlay{local_index}]"
                filters.append(
                    f"{previous}{hero_label}overlay=x='{_x_expression(segment)}':y='{_y_expression(segment)}':"
                    f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass{out}"
                )
                previous = out
            filters.append(f"{previous}fps=30,format=yuv420p[vout]")
            segment_path = work / f"video_{window_index:03d}.mp4"
            cmd += [
                "-filter_complex", ";".join(filters), "-map", "[vout]", "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-maxrate", "2600k", "-bufsize", "5200k", "-r", "30",
                "-t", f"{window_duration:.3f}", str(segment_path),
            ]
            _run_ffmpeg_step(cmd, step=f"video_{window_index:03d}", work=work, timeout=remaining_timeout())
            segment_files.append(segment_path)

        concat_list = work / "video_segments.txt"
        concat_list.write_text("\n".join(_concat_line(path) for path in segment_files) + "\n", encoding="utf-8")
        video_only = work / "video_only.mp4"
        _run_ffmpeg_step(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
                "-threads", str(render_threads), "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-map", "0:v:0", "-c:v", "copy", "-an", str(video_only),
            ],
            step="video_concat",
            work=work,
            timeout=remaining_timeout(),
        )

        audio_segments = []
        for segment in timeline:
            path = audio_by_phrase.get(segment["phrase_id"])
            if path and Path(path).exists() and Path(path).stat().st_size > 0:
                audio_segments.append({**segment, "audio_path": Path(path)})
        voice_track: Path | None = None
        if audio_segments:
            voice_track = _build_voice_track(
                audio_segments,
                render_duration,
                work / "voice_track.m4a",
                work,
                render_threads,
                remaining_timeout(),
            )

        final_tmp = work / "final.mp4"
        base_has_audio = _has_audio_stream(base_video)
        final_cmd = [
            settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
            "-threads", str(render_threads), "-filter_threads", "1", "-filter_complex_threads", "1",
            "-i", str(video_only), "-i", str(base_video),
        ]
        if voice_track:
            final_cmd += ["-i", str(voice_track)]
        if base_has_audio and voice_track:
            audio_filter = (
                f"[1:a]apad=pad_dur={max(0.0, render_duration - base_duration) + 0.05:.3f},"
                f"atrim=0:{render_duration:.3f},asetpts=PTS-STARTPTS[basea];"
                f"[2:a]atrim=0:{render_duration:.3f},asetpts=PTS-STARTPTS[voice];"
                "[basea][voice]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.891[aout]"
            )
            final_cmd += ["-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
        elif base_has_audio:
            audio_filter = f"[1:a]apad=pad_dur={max(0.0, render_duration - base_duration) + 0.05:.3f},atrim=0:{render_duration:.3f}[aout]"
            final_cmd += ["-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
        elif voice_track:
            final_cmd += ["-map", "0:v:0", "-map", "2:a:0", "-c:a", "copy"]
        else:
            final_cmd += ["-map", "0:v:0", "-an"]
        final_cmd += ["-c:v", "copy", "-movflags", "+faststart", "-t", f"{render_duration:.3f}", str(final_tmp)]
        _run_ffmpeg_step(final_cmd, step="final_mux", work=work, timeout=remaining_timeout())

        if not final_tmp.exists() or final_tmp.stat().st_size < 10_000:
            log.error("Final movie artifact is missing or empty: %s", final_tmp)
            raise CartoonBuildError(SAFE_MOVIE_ERROR)
        final_tmp.replace(output_mp4)

    log.info("Cartoon ready: %s bytes=%s", output_mp4, output_mp4.stat().st_size)
    return ensure_telegram_safe_mp4(output_mp4)


def build_simple_cartoon(character_png: Path, audio_files: list[Path], output_mp4: Path) -> Path:
    lesson_path = settings.content_root / "lessons" / "demo_001" / "lesson.json"
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    base = settings.content_root / "lessons" / "demo_001" / lesson["cartoon_base"]
    ids = [item["phrase_id"] for item in lesson["required_phrases"]]
    return build_timeline_cartoon(base, character_png, dict(zip(ids, audio_files)), lesson["timeline"], output_mp4)
