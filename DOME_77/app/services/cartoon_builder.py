from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from app.core.config import settings
from app.services.animation_library import animation_profile, ensure_animation_library
from app.services.animation_engine.rig_loader import load_character_rig
from app.services.animation_engine.motion_planner import normalize_motion_plan, primary_motion_action
from app.services.animation_engine.local_motion_cache import analyze_hero_for_animation, ensure_local_motion_cache
from app.services.animation_engine.runtime_provider import prepare_character_animation

log = logging.getLogger("dome.cartoon")
MOVIE_AVATAR_PERCEPTUAL_SCALE = 1.22
# Backwards-compatible export for existing tools; mobile lesson scale remains
# independently defined in DOME_MOBILE_77 and is not enlarged by movie policy.
AVATAR_PERCEPTUAL_SCALE = MOVIE_AVATAR_PERCEPTUAL_SCALE


def _cartoon_config() -> dict:
    p=Path("config/cartoon.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"first_child_scene_seconds":8}


SAFE_MOVIE_ERROR = "Мультфильм пока не удалось собрать. Все записи сохранены — попробуйте ещё раз."


class CartoonBuildError(RuntimeError):
    """A classified render failure whose technical detail stays server-side."""

    def __init__(self, message: str = SAFE_MOVIE_ERROR, *, code: str = "MOVIE_RENDER_FAILED", stage: str = "FFMPEG_RENDER", technical_message: str = ""):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.technical_message = technical_message or message


MovieProgressCallback = Callable[[str, int, str], None]


def _publish_progress(callback: MovieProgressCallback | None, stage: str, progress: int, strategy: str) -> None:
    if callback:
        callback(stage, max(0, min(100, int(progress))), strategy)


def cleanup_stale_render_dirs(output_mp4: Path, work_root: Path | None = None) -> tuple[int, int]:
    """Delete only incomplete work directories belonging to this exact output.

    Child recordings, final MP4 files, avatar metadata and persistent animation
    caches live outside these directories and are deliberately never touched.
    """

    output_mp4 = Path(output_mp4)
    removed = 0
    released = 0
    roots = {output_mp4.parent.resolve()}
    if work_root is not None:
        roots.add(Path(work_root).resolve())
    for parent in roots:
        if not parent.exists():
            continue
        for candidate in parent.glob(f"{output_mp4.stem}_render_*"):
            try:
                resolved = candidate.resolve()
                if not candidate.is_dir() or resolved.parent != parent:
                    continue
                released += sum(item.stat().st_size for item in candidate.rglob("*") if item.is_file())
                shutil.rmtree(candidate)
                removed += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                log.warning("Unable to remove stale movie work directory path=%s error=%s", candidate, exc)
    if removed:
        log.warning("MOBILE_MOVIE_STALE_WORK_CLEANUP output=%s dirs=%s bytes=%s", output_mp4, removed, released)
    return removed, released


def movie_storage_free_bytes(path: Path) -> int:
    path = Path(path)
    root = path if path.exists() and path.is_dir() else path.parent
    root.mkdir(parents=True, exist_ok=True)
    return int(shutil.disk_usage(root).free)


def movie_render_work_root(configured: Path | None = None) -> Path:
    """Return the bounded, ephemeral root used by FFmpeg intermediates."""

    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "dome-movie-work"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _persistent_publish_capacity(output_mp4: Path, reserve_bytes: int) -> tuple[int, int, int]:
    """Return writable bytes, current free bytes and safely reclaimable bytes."""

    free = movie_storage_free_bytes(output_mp4)
    reclaimable = output_mp4.stat().st_size if output_mp4.exists() else 0
    return max(0, free + reclaimable - reserve_bytes), free, reclaimable


def reclaim_regenerable_movie_storage(output_mp4: Path, target_free_bytes: int) -> dict[str, int]:
    """Reclaim only disposable duplicates/caches before publishing a movie.

    Final movies, child WAV takes, avatar sources, localized lesson images and
    authored content are never candidates. Successful voice uploads leave a WAV
    as the durable DB-linked take, so a same-stem recorder source is redundant.
    TTS files are a cache and can be synthesized again when needed.
    """

    output_mp4 = Path(output_mp4)
    storage_root = Path(settings.storage_root)
    target = max(0, int(target_free_bytes))
    before = movie_storage_free_bytes(output_mp4)
    stats = {"before": before, "after": before, "files": 0, "bytes": 0, "voice_sources": 0, "tts_cache": 0, "uploading": 0}
    if before >= target or not storage_root.exists():
        return stats

    candidates: list[tuple[str, Path]] = []
    for candidate in storage_root.glob("children/*/cartoons/*.uploading"):
        if candidate.is_file():
            candidates.append(("uploading", candidate))
    for candidate in storage_root.glob("children/*/mobile-voice/**/*.m4a"):
        if candidate.is_file() and candidate.with_suffix(".wav").is_file():
            candidates.append(("voice_sources", candidate))
    tts_root = storage_root / "tts-cache-mobile"
    if tts_root.exists():
        tts_files = [item for item in tts_root.rglob("*") if item.is_file() and ".tmp." not in item.name]
        tts_files.sort(key=lambda item: (item.stat().st_mtime, str(item)))
        candidates.extend(("tts_cache", item) for item in tts_files)

    for category, candidate in candidates:
        if movie_storage_free_bytes(output_mp4) >= target:
            break
        try:
            size = candidate.stat().st_size
            candidate.unlink()
            stats["files"] += 1
            stats["bytes"] += size
            stats[category] += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("MOVIE_STORAGE_RECLAIM_FILE_FAILED category=%s path=%s error=%s", category, candidate, exc)
    stats["after"] = movie_storage_free_bytes(output_mp4)
    log.warning(
        "MOVIE_STORAGE_RECLAIM before=%s after=%s target=%s files=%s bytes=%s voice_sources=%s tts_cache=%s uploading=%s",
        stats["before"], stats["after"], target, stats["files"], stats["bytes"],
        stats["voice_sources"], stats["tts_cache"], stats["uploading"],
    )
    return stats


def _publish_final_movie(final_tmp: Path, output_mp4: Path, *, reserve_bytes: int = 4_000_000) -> None:
    """Publish one verified MP4 to persistent storage without moving work files.

    Railway's volume is intentionally small.  Copying only the final artifact
    avoids filling it with render windows, voice tracks and concat files.  The
    `.uploading` file is atomically renamed only after a complete fsynced copy.
    """

    size = final_tmp.stat().st_size
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    uploading = output_mp4.with_suffix(output_mp4.suffix + ".uploading")
    uploading.unlink(missing_ok=True)
    capacity, free, reclaimable = _persistent_publish_capacity(output_mp4, reserve_bytes)
    if capacity < size:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED",
            stage="UPLOADING",
            technical_message=(
                "insufficient persistent storage for final movie: "
                f"free={free} reclaimable={reclaimable} reserve={reserve_bytes} size={size}"
            ),
        )
    # Replacing the same session's older movie is safe after the new artifact
    # has already been rendered and validated in ephemeral storage.
    if free < size + reserve_bytes and output_mp4.exists():
        output_mp4.unlink()
    try:
        with final_tmp.open("rb") as source, uploading.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if uploading.stat().st_size != size:
            raise OSError(f"incomplete movie publish: expected={size} actual={uploading.stat().st_size}")
        uploading.replace(output_mp4)
    except Exception:
        uploading.unlink(missing_ok=True)
        raise


def _fit_final_movie_for_storage(
    final_tmp: Path,
    output_mp4: Path,
    work: Path,
    render_threads: int,
    timeout: float,
    *,
    reserve_bytes: int = 4_000_000,
) -> Path:
    """Create a bounded delivery rendition when the volume is nearly full.

    All authored scenes, timings, child audio and avatar animation are already
    present in ``final_tmp``. This step adjusts only the delivery bitrate and
    stays on ephemeral storage until the MP4 has been verified.
    """

    source_size = final_tmp.stat().st_size
    cfg = _cartoon_config()
    reclaim_target = max(
        source_size + reserve_bytes,
        int(cfg.get("storage_reclaim_target_free_bytes", 64_000_000)),
    )
    reclaim_regenerable_movie_storage(output_mp4, reclaim_target)
    capacity, free, reclaimable = _persistent_publish_capacity(output_mp4, reserve_bytes)
    if source_size <= capacity:
        return final_tmp

    minimum_capacity = max(1_000_000, int(cfg.get("storage_fit_minimum_bytes", 9_000_000)))
    if capacity < minimum_capacity:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED",
            stage="UPLOADING",
            technical_message=(
                "persistent storage cannot hold the minimum playable movie rendition: "
                f"free={free} reclaimable={reclaimable} reserve={reserve_bytes} capacity={capacity}"
            ),
        )

    _width, _height, duration = _probe_video(final_tmp)
    duration = max(0.1, duration)
    has_audio = _has_audio_stream(final_tmp)
    audio_kbps = max(64, min(160, int(cfg.get("storage_fit_audio_kbps", 96)))) if has_audio else 0
    safety_ratio = max(0.65, min(0.90, float(cfg.get("storage_fit_safety_ratio", 0.82))))
    total_kbps = int((capacity * 8 / duration / 1000) * safety_ratio)
    video_kbps = total_kbps - audio_kbps
    minimum_video_kbps = max(320, int(cfg.get("storage_fit_min_video_kbps", 450)))
    if video_kbps < minimum_video_kbps:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED",
            stage="UPLOADING",
            technical_message=(
                "persistent storage is below the minimum safe movie bitrate: "
                f"capacity={capacity} duration={duration:.3f} video_kbps={video_kbps}"
            ),
        )

    fitted = work / "final-storage-fit.mp4"
    fitted.unlink(missing_ok=True)
    log.warning(
        "MOVIE_STORAGE_FIT_STARTED source_bytes=%s capacity=%s free=%s duration=%.3f video_kbps=%s audio_kbps=%s",
        source_size,
        capacity,
        free,
        duration,
        video_kbps,
        audio_kbps,
    )
    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
        "-threads", str(render_threads), "-filter_threads", "1", "-filter_complex_threads", "1",
        "-i", str(final_tmp), "-map", "0:v:0", "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", f"{audio_kbps}k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", "-t", f"{duration:.3f}", str(fitted)]
    _run_ffmpeg_step(cmd, step="storage_fit", work=work, timeout=timeout)

    fitted_size = fitted.stat().st_size if fitted.exists() else 0
    if fitted_size < 10_000 or fitted_size > capacity:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED",
            stage="UPLOADING",
            technical_message=(
                "storage-fit rendition did not meet the publish bound: "
                f"capacity={capacity} source={source_size} fitted={fitted_size}"
            ),
        )
    log.info(
        "MOVIE_STORAGE_FIT_SUCCESS source_bytes=%s fitted_bytes=%s capacity=%s",
        source_size,
        fitted_size,
        capacity,
    )
    return fitted


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


def _resolve_normalized_timeline(timeline: list[dict], frame_width: int, frame_height: int, character_aspect: float = 0.6, ground_ratio: float = 1.0) -> list[dict]:
    """Convert authored placement using visible-body size and the saved feet anchor."""

    resolved: list[dict] = []
    frame_ratio=frame_height/max(1,frame_width)
    normalized_visual_aspect=max(.01,character_aspect*frame_ratio)
    for source in timeline:
        segment = dict(source)
        if "height_norm" in segment:
            authored_height=float(segment["height_norm"])*AVATAR_PERCEPTUAL_SCALE
            # A horizontal dinosaur should not be made tiny merely because its
            # silhouette is wide. Preserve comparable perceptual body area,
            # then apply the authored safe-zone width as a final bound.
            height_norm=authored_height/(max(1.0,character_aspect)**0.32)
            max_width_norm=float(segment.get("max_width_norm") or 0.56)
            if height_norm*normalized_visual_aspect>max_width_norm:
                height_norm=max_width_norm/normalized_visual_aspect
            segment["height"] = max(1, round(height_norm * frame_height))
        height = int(segment.get("height", 225))
        if "floor_y_norm" in segment:
            segment["y"] = round(float(segment["floor_y_norm"]) * frame_height - height*max(.65,min(1.15,ground_ratio)))
        if "x_norm" in segment:
            segment["x"] = round(float(segment["x_norm"]) * frame_width)
        if "x_start_norm" in segment:
            segment["x_start"] = round(float(segment["x_start_norm"]) * frame_width)
        if "x_end_norm" in segment:
            segment["x_end"] = round(float(segment["x_end_norm"]) * frame_width)
        hero_width=max(1,round(height*max(character_aspect,0.05)))
        for key in ("x","x_end"):
            if key not in segment:continue
            x=float(segment[key]);hero=[x/frame_width,float(segment.get("y",0))/frame_height,hero_width/frame_width,height/frame_height]
            for box in segment.get("protected_boxes_norm") or []:
                if not isinstance(box,list) or len(box)!=4:continue
                left,top,width,box_height=(float(value) for value in box)
                overlaps=hero[0]<left+width+.012 and hero[0]+hero[2]+.012>left and hero[1]<top+box_height+.012 and hero[1]+hero[3]+.012>top
                if overlaps:
                    side=str(segment.get("placement_side") or "left").lower()
                    x=(left-hero[2]-.018)*frame_width if side=="left" else (left+width+.018)*frame_width
                    hero[0]=x/frame_width
            segment[key]=round(x)
        resolved.append(segment)
    return resolved


def _visible_character_asset(source: Path, metadata: dict | None, output: Path) -> tuple[Path,float]:
    """Crop transparent canvas once so scale and baseline use visible pixels."""

    with Image.open(source) as image:
        rgba=image.convert("RGBA");width,height=rgba.size
        raw=(metadata or {}).get("characterBoundingBox") or []
        if isinstance(raw,list) and len(raw)==4:
            left=max(0,min(width-1,round(float(raw[0])*width)));top=max(0,min(height-1,round(float(raw[1])*height)))
            right=max(left+1,min(width,round((float(raw[0])+float(raw[2]))*width)));bottom=max(top+1,min(height,round((float(raw[1])+float(raw[3]))*height)))
            crop=(left,top,right,bottom)
        else:
            crop=rgba.getbbox() or (0,0,width,height)
        visible=rgba.crop(crop);visible.save(output)
    return output,visible.width/max(visible.height,1)


def _desired_facing(segment: dict) -> str:
    explicit=str(segment.get("hero_facing") or "").upper()
    if explicit in {"LEFT","RIGHT","FRONT"}:return explicit
    view=str((segment.get("character_animation") or {}).get("view") or "").lower()
    if view.endswith("_left"):return "LEFT"
    if view.endswith("_right"):return "RIGHT"
    partner=str(segment.get("partner_side") or segment.get("face_partner") or "").upper()
    if partner in {"LEFT","RIGHT"}:return partner
    action=primary_motion_action(segment)
    if action in {"walk_left","turn_left","exit_left","enter_right"}:return "LEFT"
    if action in {"walk_right","turn_right","exit_right","enter_left"}:return "RIGHT"
    motion=str(segment.get("animation") or segment.get("motion") or "")
    if "right_to_left" in motion or "from_right" in motion:return "LEFT"
    if "left_to_right" in motion or "from_left" in motion:return "RIGHT"
    return "FRONT"


def _resolved_facing(segment: dict, source_facing: str) -> str:
    """Resolve an authored view without inventing a view absent from the drawing."""

    requested=_desired_facing(segment);source=str(source_facing or "UNKNOWN").upper()
    if requested=="FRONT" and source in {"LEFT","RIGHT"}:
        return source
    if requested in {"LEFT","RIGHT","FRONT"}:
        return requested
    return source


def _should_hflip(segment: dict, source_facing: str, legacy_mirror: bool) -> bool:
    desired=_resolved_facing(segment,source_facing);source=str(source_facing or "UNKNOWN").upper()
    if desired=="FRONT" or source=="FRONT":return False
    if source in {"LEFT","RIGHT"} and desired in {"LEFT","RIGHT"}:return source!=desired
    return legacy_mirror


def _source_facing(metadata: dict | None) -> str:
    payload=metadata or {}
    canonical=str(payload.get("canonicalFacing") or "UNKNOWN").upper();saved=str(payload.get("facingDirection") or "UNKNOWN").upper()
    value=canonical if canonical in {"LEFT","RIGHT","FRONT"} else saved
    return value if value in {"LEFT","RIGHT","FRONT"} else "UNKNOWN"


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
    action=primary_motion_action(segment)
    if action in {"enter_left","enter_right"} or motion in {"walk_from_left", "walk_left_then_talk"}:
        return f"if(lt(t,{talk}),{x0}+({x1}-{x0})*(t-{start})/max({talk-start},0.01),{x1})"
    if action in {"walk_left","walk_right","exit_left","exit_right"} or motion in {"walk_from_right", "walk_right_to_left_talk", "walk_right_to_left", "walk_left_to_right_talk"}:
        return f"{x0}+({x1}-{x0})*(t-{start})/max({end-start},0.01)"
    return str(float(segment.get("x", x1)))


def _y_expression(segment: dict) -> str:
    y = float(segment.get("y", 245))
    start = float(segment["visible_start"])
    motion = segment.get("animation", segment.get("motion", "stand_front_talk"))
    action=primary_motion_action(segment)
    profile = animation_profile(action, settings.storage_root / "animation-library")
    if action in {"walk_left","walk_right","enter_left","enter_right","exit_left","exit_right"} or "walk" in motion:
        return f"{y}+{float(profile.get('walk_bob', 4.0))}*abs(sin(8*(t-{start})))"
    if action == "small_jump" or motion == "happy_jump":
        return f"{y}-{float(profile.get('jump_height',18.0))}*abs(sin(5*(t-{start})))"
    return f"{y}+{float(profile.get('body_bob',1.5))}*sin(2.2*(t-{start}))"


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
    try:
        command_file.write_text(" ".join(cmd), encoding="utf-8")
    except OSError as exc:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED" if getattr(exc, "errno", None) == 28 else "MOVIE_RENDER_IO_FAILED",
            stage="FFMPEG_RENDER",
            technical_message=f"unable to persist ffmpeg command step={step}: {exc}",
        ) from exc
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
        raise CartoonBuildError(code="MOVIE_FFMPEG_UNAVAILABLE", stage="FFMPEG_RENDER", technical_message=f"ffmpeg executable not found: {settings.ffmpeg_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        detail = _tail_text(error_file, 4000)
        log.error("FFmpeg timed out step=%s command=%s stderr=%s", step, command_file, detail)
        raise CartoonBuildError(code="MOVIE_RENDER_TIMED_OUT", stage="FFMPEG_RENDER", technical_message=f"ffmpeg timeout step={step}: {detail[-1200:]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = _tail_text(error_file)
        log.error(
            "FFmpeg failed step=%s code=%s command=%s stderr=%s",
            step,
            exc.returncode,
            command_file,
            detail,
        )
        disk_full = "No space left on device" in detail or "error code: -28" in detail
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED" if disk_full else "MOVIE_FFMPEG_FAILED",
            stage="FFMPEG_RENDER",
            technical_message=f"ffmpeg step={step} exit={exc.returncode}: {detail[-1600:]}",
        ) from exc
    except OSError as exc:
        raise CartoonBuildError(
            code="MOVIE_STORAGE_EXHAUSTED" if getattr(exc, "errno", None) == 28 else "MOVIE_RENDER_IO_FAILED",
            stage="FFMPEG_RENDER",
            technical_message=f"ffmpeg I/O failure step={step}: {exc}",
        ) from exc
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


def _audio_duration(path: Path) -> float:
    ffprobe=settings.ffmpeg_bin.replace("ffmpeg","ffprobe")
    try:
        result=subprocess.run([ffprobe,"-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)],check=True,capture_output=True,text=True,timeout=30)
        return max(0.0,float(result.stdout.strip()))
    except Exception:
        try:
            result=subprocess.run([settings.ffmpeg_bin,"-hide_banner","-i",str(path),"-t","0","-f","null","-"],check=False,capture_output=True,text=True,timeout=30)
            match=re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",result.stderr or "")
            return int(match.group(1))*3600+int(match.group(2))*60+float(match.group(3)) if match else 0.0
        except Exception:return 0.0


def _scheduled_voice_duration(audio_segments: list[dict], authored_duration: float) -> float:
    cursor=0.0
    for segment in sorted(audio_segments,key=lambda row:float(row.get("talk_start",row["visible_start"]))):
        start=max(cursor,float(segment.get("talk_start",segment["visible_start"])))
        slot=max(0.05,float(segment.get("end",start+5.0))-start)
        source_duration=max(0.05,_audio_duration(Path(segment["audio_path"])))
        # A small, pitch-safe acceleration keeps ordinary long child answers in
        # the authored slot. Longer answers remain complete and extend the
        # schedule instead of being cut at five seconds.
        output_duration=slot if source_duration>slot and source_duration/slot<=1.35 else source_duration
        cursor=start+output_duration
    return max(authored_duration,cursor)


def _build_voice_track(
    audio_segments: list[dict],
    duration: float,
    output: Path,
    work: Path,
    render_threads: int,
    timeout: float,
    audio_bitrate: str,
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
        end = max(start + 0.05, float(segment.get("end", start + 5.0)))
        slot_duration = max(0.05, end - start)
        source_duration=max(0.05,_audio_duration(Path(segment["audio_path"])))
        speed=source_duration/slot_duration if slot_duration else 1.0
        fit=source_duration>slot_duration and speed<=1.35
        output_duration=slot_duration if fit else min(source_duration,max(0.05,duration-start))
        label = f"[voice{index}]"
        tempo=f"atempo={speed:.5f}," if fit else ""
        filters.append(
            f"[{index}:a]atrim=0:{source_duration:.3f},asetpts=PTS-STARTPTS,{tempo}"
            "highpass=f=80,acompressor=threshold=-20dB:ratio=3:makeup=3,alimiter=limit=0.891,"
            f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{output_duration:.3f}{label}"
        )
        pieces.append(label)
        cursor = start + output_duration
    if duration - cursor >= 0.001:
        label = "[silence_final]"
        filters.append(f"anullsrc=r=48000:cl=stereo:d={duration - cursor:.3f},asetpts=PTS-STARTPTS{label}")
        pieces.append(label)
    filters.append(f"{''.join(pieces)}concat=n={len(pieces)}:v=0:a=1[aout]")
    cmd += [
        "-filter_complex", ";".join(filters), "-map", "[aout]",
        "-c:a", "aac", "-b:a", audio_bitrate, "-t", f"{duration:.3f}", str(output),
    ]
    _run_ffmpeg_step(cmd, step="voice_track", work=work, timeout=timeout)
    return output


def build_timeline_cartoon(
    base_video: Path,
    character_png: Path,
    audio_by_phrase: dict[str, Path],
    timeline: list[dict],
    output_mp4: Path,
    base_video_filters: list[str] | None = None,
    *,
    character_metadata: dict | None = None,
    render_strategy: str = "rich",
    progress_callback: MovieProgressCallback | None = None,
    total_timeout_override: float | None = None,
    work_root: Path | None = None,
) -> Path:
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

    if render_strategy not in {"rich", "safe", "static"}:
        raise ValueError(f"Unsupported movie render strategy: {render_strategy}")
    cfg = _cartoon_config()
    _publish_progress(progress_callback, "LOADING_AVATAR", 12, render_strategy)
    frame_width, frame_height, base_duration = _probe_video(base_video)
    with Image.open(character_png) as source_image:
        source_width,source_height=source_image.size
    visible_box=(character_metadata or {}).get("characterBoundingBox") or [0,0,1,1]
    try:character_aspect=(source_width*float(visible_box[2]))/max(1.0,source_height*float(visible_box[3]))
    except (TypeError,ValueError,IndexError):character_aspect=source_width/max(source_height,1)
    ground_anchor=(character_metadata or {}).get("feetAnchor") or (character_metadata or {}).get("groundAnchor") or []
    try:ground_ratio=(float(ground_anchor[1])-float(visible_box[1]))/max(.01,float(visible_box[3]))
    except (TypeError,ValueError,IndexError):ground_ratio=1.0
    timeline = _resolve_normalized_timeline([dict(item) for item in timeline], frame_width, frame_height, character_aspect,ground_ratio)
    minimum = float(cfg.get("first_child_scene_seconds", 8))
    timeline[0]["end"] = max(float(timeline[0].get("end", 0)), float(timeline[0].get("visible_start", 0)) + minimum)
    timeline_end = max(float(item["end"]) for item in timeline)
    render_duration = max(base_duration, timeline_end)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    ensure_animation_library(settings.storage_root / "animation-library")
    _rig = load_character_rig(character_png, settings.storage_root / "character-rigs", character_metadata if settings.avatar_animation_engine_enabled else None)
    _animation_analysis=analyze_hero_for_animation(character_metadata,built_in=_rig.provider not in {"fallback_png","metadata_cutout"})
    log.info("MOVIE_AVATAR_ANIMATION_MODE mode=%s provider=%s capabilities=%s static_fallback=%s",_animation_analysis["mode"],_rig.provider,_animation_analysis["capabilities"],_animation_analysis["static_fallback"])
    _motion_plans = [normalize_motion_plan(item) for item in timeline]
    _publish_progress(progress_callback, "LOADING_ANIMATION_CACHE", 20, render_strategy)
    if render_strategy == "rich" and settings.avatar_animation_engine_enabled:
        try:
            _library,cache_hits,cache_created=ensure_local_motion_cache(character_png,settings.storage_root,character_metadata)
            log.info("MOVIE_AVATAR_LOCAL_CACHE avatar=%s hits=%s created=%s provider=%s",_library.avatar_id,cache_hits,cache_created,_rig.provider)
        except Exception as exc:
            # The current PNG compositor is the mandatory production fallback.
            log.warning("Local avatar motion cache unavailable; using static-safe renderer: %s",exc)

    allow_generate = bool(cfg.get("generate_missing_animation_during_render", False))
    render_threads = max(1, min(2, int(cfg.get("render_threads", 1))))
    configured_timeout = max(60, int(cfg.get("total_render_timeout_seconds", 600)))
    total_timeout = min(configured_timeout, max(10.0, float(total_timeout_override))) if total_timeout_override is not None else configured_timeout
    step_timeout = max(30, int(cfg.get("ffmpeg_timeout_seconds", 300)))
    render_started = time.monotonic()

    def remaining_timeout() -> float:
        remaining = total_timeout - (time.monotonic() - render_started)
        if remaining <= 5:
            raise CartoonBuildError(SAFE_MOVIE_ERROR)
        return min(float(step_timeout), remaining)

    render_root = movie_render_work_root(work_root)
    with tempfile.TemporaryDirectory(prefix=f"{output_mp4.stem}_render_", dir=render_root) as work_value:
        work = Path(work_value)
        _publish_progress(progress_callback, "PREPARING_SCENES", 28, render_strategy)
        render_character,_visible_aspect=_visible_character_asset(character_png,character_metadata,work/"character-visible.png")
        source_facing=_source_facing(character_metadata)
        log.info("MOVIE_AVATAR_METADATA source_facing=%s confirmed=%s version=%s",source_facing,(character_metadata or {}).get("userConfirmed") is True,(character_metadata or {}).get("analysisVersion") or "legacy")
        for segment in timeline:
            action=primary_motion_action(segment);desired=_desired_facing(segment);resolved_facing=_resolved_facing(segment,source_facing);applied_flip=_should_hflip(segment,source_facing,bool(animation_profile(action, settings.storage_root / "animation-library").get("mirror",False)))
            displayed=("RIGHT" if source_facing=="LEFT" else "LEFT") if applied_flip and source_facing in {"LEFT","RIGHT"} else source_facing
            orientation_source="parent_confirmed" if (character_metadata or {}).get("userConfirmed") is True else "saved_analysis"
            log.info("MOVIE_AVATAR_SCENE scene_id=%s asset_id=%s metadata_version=%s requested_facing=%s resolved_facing=%s source_facing=%s orientation_source=%s scale=%.5f x=%s y=%s ground_anchor=%s fallback=%s flip=%s displayed=%s",segment.get("phrase_id"),character_png.name,(character_metadata or {}).get("metadataVersion") or (character_metadata or {}).get("analysisVersion") or "legacy",desired,resolved_facing,source_facing,orientation_source,float(segment.get("height",0))/max(1,frame_height),segment.get("x",segment.get("x_start")),segment.get("y"),(character_metadata or {}).get("groundAnchor") or (character_metadata or {}).get("feetAnchor"),desired!=resolved_facing,applied_flip,displayed)
        _publish_progress(progress_callback, "RENDERING_AVATAR_MOTION", 42, render_strategy)
        ai_clips: list[Path | None] = []
        for index, segment in enumerate(timeline):
            if render_strategy != "rich":
                ai_clips.append(None)
                continue
            try:
                phrase_audio = Path(audio_by_phrase[segment["phrase_id"]]) if audio_by_phrase.get(segment["phrase_id"]) else None
                animation_segment = {**segment, "resolved_facing": _resolved_facing(segment,source_facing).lower()}
                ai_clips.append(prepare_character_animation(
                    character_png, animation_segment, work / "ai-animation", phrase_audio,
                    metadata=character_metadata, allow_generate=allow_generate,
                ))
            except Exception as exc:
                log.warning("AI animation fallback for scene %s: %s", index, exc)
                ai_clips.append(None)

        _publish_progress(progress_callback, "PREPARING_AUDIO", 50, render_strategy)
        audio_segments=[]
        for segment in timeline:
            path=audio_by_phrase.get(segment["phrase_id"])
            if path and Path(path).exists() and Path(path).stat().st_size>0:
                audio_segments.append({**segment,"audio_path":Path(path)})
        render_duration=_scheduled_voice_duration(audio_segments,render_duration)
        windows = _render_windows(timeline, render_duration)
        log.info(
            "MOBILE_MOVIE_RENDER_PLAN duration=%.3f windows=%s voices=%s threads=%s",
            render_duration,
            len(windows),
            sum(1 for item in timeline if audio_by_phrase.get(item["phrase_id"])),
            render_threads,
        )
        _publish_progress(progress_callback, "COMPOSITING", 56, render_strategy)
        video_only = work / "video_only.mp4"
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
                    cmd += ["-loop", "1", "-framerate", "15", "-i", str(render_character)]
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
                    alpha_input = Path(ai_clips[scene_index]).suffix.lower() in {".mov", ".webm"}
                    transparency = "format=rgba" if alpha_input else "chromakey=0x00FF00:0.28:0.08,format=rgba"
                    filters.append(
                        f"[{source}:v]setpts=PTS-STARTPTS,{transparency},"
                        f"scale=-1:{height},fade=t=in:st={start:.3f}:d=0.12:alpha=1,"
                        f"fade=t=out:st={max(start, end - 0.18):.3f}:d=0.18:alpha=1{hero_label}"
                    )
                else:
                    profile = animation_profile(primary_motion_action(segment), settings.storage_root / "animation-library")
                    pre = "hflip," if _should_hflip(segment,source_facing,bool(profile.get("mirror",False))) else ""
                    rotation = 0.0 if render_strategy == "static" else float(profile.get("rotation", 0.012))
                    filters.append(
                        f"[{source}:v]setpts=PTS-STARTPTS,{pre}scale=-1:{height},"
                        f"rotate='{rotation}*sin(3.2*(t-{start:.3f}))':ow=rotw(iw):oh=roth(ih):c=none,"
                        f"fade=t=in:st={start:.3f}:d=0.15:alpha=1,"
                        f"fade=t=out:st={max(start, end - 0.18):.3f}:d=0.18:alpha=1{hero_label}"
                    )
                out = f"[overlay{local_index}]"
                y_expression = str(float(segment.get("y", 245))) if render_strategy == "static" else _y_expression(segment)
                filters.append(
                    f"{previous}{hero_label}overlay=x='{_x_expression(segment)}':y='{y_expression}':"
                    f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass{out}"
                )
                previous = out
            max_width = max(640, min(1920, int(cfg.get("output_max_width", 1280))))
            filters.append(f"{previous}scale={max_width}:-2:force_original_aspect_ratio=decrease:flags=lanczos,fps=30,format=yuv420p[vout]")
            segment_path = work / f"video_{window_index:03d}.mp4"
            cmd += [
                "-filter_complex", ";".join(filters), "-map", "[vout]", "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(int(cfg.get("video_crf", 25))),
                "-maxrate", str(cfg.get("video_maxrate", "1600k")), "-bufsize", str(cfg.get("video_bufsize", "3200k")), "-r", "30",
                "-t", f"{window_duration:.3f}", str(segment_path),
            ]
            _run_ffmpeg_step(cmd, step=f"video_{window_index:03d}", work=work, timeout=remaining_timeout())
            if not video_only.exists():
                segment_path.replace(video_only)
            else:
                concat_list = work / f"video_join_{window_index:03d}.txt"
                concat_list.write_text("\n".join((_concat_line(video_only), _concat_line(segment_path))) + "\n", encoding="utf-8")
                joined = work / f"video_join_{window_index:03d}.mp4"
                _run_ffmpeg_step(
                    [
                        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "warning",
                        "-threads", str(render_threads), "-f", "concat", "-safe", "0", "-i", str(concat_list),
                        "-map", "0:v:0", "-c:v", "copy", "-an", str(joined),
                    ],
                    step=f"video_join_{window_index:03d}",
                    work=work,
                    timeout=remaining_timeout(),
                )
                video_only.unlink(missing_ok=True)
                segment_path.unlink(missing_ok=True)
                joined.replace(video_only)
            progress = 56 + round(22 * ((window_index + 1) / max(1, len(windows))))
            _publish_progress(progress_callback, "COMPOSITING", progress, render_strategy)

        voice_track: Path | None = None
        if audio_segments:
            audio_bitrate = str(cfg.get("audio_bitrate", "96k"))
            voice_track = _build_voice_track(
                audio_segments,
                render_duration,
                work / "voice_track.m4a",
                work,
                render_threads,
                remaining_timeout(),
                audio_bitrate,
            )

        _publish_progress(progress_callback, "FFMPEG_RENDER", 82, render_strategy)
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
            final_cmd += ["-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[aout]", "-c:a", "aac", "-b:a", str(cfg.get("audio_bitrate", "96k"))]
        elif base_has_audio:
            audio_filter = f"[1:a]apad=pad_dur={max(0.0, render_duration - base_duration) + 0.05:.3f},atrim=0:{render_duration:.3f}[aout]"
            final_cmd += ["-filter_complex", audio_filter, "-map", "0:v:0", "-map", "[aout]", "-c:a", "aac", "-b:a", str(cfg.get("audio_bitrate", "96k"))]
        elif voice_track:
            final_cmd += ["-map", "0:v:0", "-map", "2:a:0", "-c:a", "copy"]
        else:
            final_cmd += ["-map", "0:v:0", "-an"]
        final_cmd += ["-c:v", "copy", "-movflags", "+faststart", "-t", f"{render_duration:.3f}", str(final_tmp)]
        _publish_progress(progress_callback, "ENCODING", 90, render_strategy)
        _run_ffmpeg_step(final_cmd, step="final_mux", work=work, timeout=remaining_timeout())

        if not final_tmp.exists() or final_tmp.stat().st_size < 10_000:
            log.error("Final movie artifact is missing or empty: %s", final_tmp)
            raise CartoonBuildError(SAFE_MOVIE_ERROR)
        _publish_progress(progress_callback, "UPLOADING", 96, render_strategy)
        publish_source = _fit_final_movie_for_storage(
            final_tmp,
            output_mp4,
            work,
            render_threads,
            remaining_timeout(),
        )
        _publish_final_movie(publish_source, output_mp4)

    _publish_progress(progress_callback, "FINALIZING", 98, render_strategy)
    log.info("Cartoon ready: %s bytes=%s", output_mp4, output_mp4.stat().st_size)
    result = ensure_telegram_safe_mp4(output_mp4)
    _publish_progress(progress_callback, "READY", 100, render_strategy)
    return result


def build_simple_cartoon(character_png: Path, audio_files: list[Path], output_mp4: Path) -> Path:
    lesson_path = settings.content_root / "lessons" / "demo_001" / "lesson.json"
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    base = settings.content_root / "lessons" / "demo_001" / lesson["cartoon_base"]
    ids = [item["phrase_id"] for item in lesson["required_phrases"]]
    return build_timeline_cartoon(base, character_png, dict(zip(ids, audio_files)), lesson["timeline"], output_mp4)
