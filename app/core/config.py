from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./storage/app.db"
    storage_root: Path = Path("./storage")
    content_root: Path = Path("./content")
    ffmpeg_bin: str = "ffmpeg"
    admin_telegram_ids: str = ""
    openai_api_key: str = ""
    openai_text_model: str = "gpt-5-mini"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_image_model: str = "gpt-image-1-mini"
    webapp_base_url: str = ""
    railway_public_domain: str = ""
    webapp_port: int = 8080
    payment_url: str = ""
    payment_url_trial: str = ""
    payment_url_group: str = ""
    payment_url_individual: str = ""
    support_chat_url: str = ""
    support_call_label: str = "+995000000000"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_starttls: bool = True
    smscenter_api_key: str = ""
    smscenter_sender_id: str = "DOME"
    smscenter_api_url: str = "https://sms-api.wifisher.com/api/v2/send"
    sms_otp_ttl_seconds: int = 600
    sms_otp_max_attempts: int = 5
    sms_otp_resend_cooldown_seconds: int = 60
    consent_hash_secret: str = ""
    voice_consent_version: str = "2026-08-05-v2-legal-representative"
    payment_consent_version: str = "2026-08-04-v1"
    email_reports_default: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @property
    def effective_webapp_base_url(self) -> str:
        explicit = self.webapp_base_url.strip().rstrip("/")
        if explicit.endswith("/index.html"):
            explicit = explicit[:-len("/index.html")]
        if explicit:
            return explicit
        domain = self.railway_public_domain.strip().strip("/")
        if domain:
            return domain if domain.startswith("https://") else f"https://{domain}"
        return ""

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip().isdigit()}
settings = Settings()
