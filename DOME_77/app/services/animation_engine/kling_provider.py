from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings

log = logging.getLogger("dome.kling")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(access_key: str, secret_key: str) -> str:
    """Create Kling HS256 bearer token without an extra PyJWT dependency."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": now + 1800, "nbf": now - 5}
    head = _b64url(json.dumps(header, separators=(",", ":")).encode())
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret_key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"


def enabled() -> bool:
    mode = (settings.character_ai_animation or "auto").lower().strip()
    if mode in {"off", "false", "0", "disabled"}:
        return False
    return bool(settings.kling_api_key.strip() and settings.kling_api_secret.strip())


def _image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _technical_prompt(description_ru: str, speaking: bool, view: str) -> str:
    # Kling accepts natural-language prompts. Keep the user's Russian description intact,
    # then add strict production constraints. This avoids requiring OpenAI for animation.
    speech = "Персонаж естественно жестикулирует во время речи." if speaking else "Персонаж не говорит."
    return (
        f"{description_ru.strip()} {speech} "
        f"Ракурс: {view}. Полный рост персонажа всегда в кадре. "
        "Сохраняй в точности внешность, одежду, цвета, пропорции и стиль исходного персонажа. "
        "Естественные движения рук, ног, корпуса и головы; правильная анатомия, без лишних конечностей. "
        "Статичная камера. Один персонаж. Без текста, логотипов и дополнительных объектов. "
        "Фон должен быть однотонный ярко-зелёный (#00FF00), ровный, без теней и градиента, "
        "чтобы персонажа можно было вырезать chroma key."
    )


class KlingError(RuntimeError):
    pass


class KlingProvider:
    name = "kling"

    def __init__(self) -> None:
        self.base = settings.kling_api_base.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + _jwt(settings.kling_api_key, settings.kling_api_secret),
            "Content-Type": "application/json",
        }

    def generate(self, *, character_png: Path, description_ru: str, duration: float,
                 speaking: bool, view: str, output_path: Path) -> tuple[Path, str]:
        if not enabled():
            raise KlingError("Kling keys are not configured")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        seconds = "10" if duration > 5.2 else "5"
        payload: dict[str, Any] = {
            "model_name": settings.kling_model_name,
            "mode": settings.kling_mode,
            "duration": seconds,
            "image": _image_base64(character_png),
            "prompt": _technical_prompt(description_ru, speaking, view),
            "negative_prompt": "deformed body, extra arms, extra legs, missing limbs, duplicate character, text, watermark, camera movement, background objects",
            "cfg_scale": 0.5,
        }
        timeout = httpx.Timeout(45.0, connect=20.0)
        with httpx.Client(timeout=timeout) as client:
            r = client.post(self.base + "/v1/videos/image2video", headers=self.headers, json=payload)
            r.raise_for_status()
            data = r.json()
            if int(data.get("code", 0) or 0) != 0:
                raise KlingError(f"Kling submit error: {data}")
            task_id = str((data.get("data") or {}).get("task_id") or "")
            if not task_id:
                raise KlingError(f"Kling did not return task_id: {data}")

            deadline = time.time() + max(60, settings.kling_timeout_seconds)
            result_url = ""
            while time.time() < deadline:
                time.sleep(max(1.0, float(settings.kling_poll_seconds)))
                q = client.get(self.base + f"/v1/videos/image2video/{task_id}", headers=self.headers)
                q.raise_for_status()
                body = q.json()
                d = body.get("data") or {}
                status = str(d.get("task_status") or "").lower()
                if status in {"succeed", "success", "completed"}:
                    videos = ((d.get("task_result") or {}).get("videos") or [])
                    if videos:
                        result_url = str(videos[0].get("url") or "")
                    break
                if status in {"failed", "failure", "error"}:
                    raise KlingError(f"Kling generation failed: {d.get('task_status_msg') or body}")
            if not result_url:
                raise KlingError("Kling generation timed out or returned no video")
            vr = client.get(result_url, follow_redirects=True, timeout=90.0)
            vr.raise_for_status()
            output_path.write_bytes(vr.content)
        if output_path.stat().st_size < 10_000:
            raise KlingError("Kling returned an empty/too-small video")
        return output_path, result_url


    def lip_sync(self, *, video_url: str, audio_path: Path, output_path: Path) -> Path:
        """Apply the child's recorded audio to a Kling-generated motion clip.

        Kling's official Lip-Sync endpoint accepts audio2video tasks. If it is unavailable
        for the account/model, caller catches the error and keeps the body animation as a safe fallback.
        """
        if not video_url or not audio_path.exists() or audio_path.stat().st_size <= 0:
            raise KlingError("Lip-sync source video/audio is missing")
        audio_b64=base64.b64encode(audio_path.read_bytes()).decode('ascii')
        payload={
            "input": {
                "video_url": video_url,
                "mode": "audio2video",
                "audio_type": "file",
                "audio_file": audio_b64,
            }
        }
        with httpx.Client(timeout=httpx.Timeout(45.0,connect=20.0)) as client:
            r=client.post(self.base+"/v1/videos/lip-sync",headers=self.headers,json=payload)
            r.raise_for_status(); body=r.json()
            if int(body.get('code',0) or 0)!=0:
                raise KlingError(f"Kling lip-sync submit error: {body}")
            task_id=str((body.get('data') or {}).get('task_id') or '')
            if not task_id: raise KlingError(f"Kling lip-sync did not return task_id: {body}")
            deadline=time.time()+max(60,settings.kling_timeout_seconds)
            result_url=''
            while time.time()<deadline:
                time.sleep(max(1.0,float(settings.kling_poll_seconds)))
                q=client.get(self.base+f"/v1/videos/lip-sync/{task_id}",headers=self.headers)
                q.raise_for_status(); qb=q.json(); d=qb.get('data') or {}
                status=str(d.get('task_status') or '').lower()
                if status in {'succeed','success','completed'}:
                    videos=((d.get('task_result') or {}).get('videos') or [])
                    if videos: result_url=str(videos[0].get('url') or '')
                    break
                if status in {'failed','failure','error'}:
                    raise KlingError(f"Kling lip-sync failed: {d.get('task_status_msg') or qb}")
            if not result_url: raise KlingError('Kling lip-sync timed out or returned no video')
            vr=client.get(result_url,follow_redirects=True,timeout=90.0); vr.raise_for_status()
            output_path.parent.mkdir(parents=True,exist_ok=True); output_path.write_bytes(vr.content)
        if output_path.stat().st_size<10_000: raise KlingError('Kling lip-sync returned empty video')
        return output_path
