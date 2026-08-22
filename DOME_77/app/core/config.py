from pathlib import Path
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator

class Settings(BaseSettings):
    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./storage/app.db"
    storage_root: Path = Path("./storage")
    data_dir: Path | None = None  # legacy/current Railway alias; DATA_DIR=/data is supported
    content_root: Path = Path("./content")
    ffmpeg_bin: str = "ffmpeg"
    admin_telegram_ids: str = ""
    openai_api_key: str = ""
    openai_text_model: str = "gpt-5-mini"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"
    child_tts_voice: str = "coral"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_image_model: str = "gpt-image-1-mini"
    # Optional full-body character animation. If Kling keys are empty, DOME uses the stable PNG renderer.
    kling_api_key: str = ""
    kling_api_secret: str = ""
    kling_api_base: str = "https://api-singapore.klingai.com"
    kling_model_name: str = "kling-v1-6"
    kling_mode: str = "std"
    kling_poll_seconds: float = 5.0
    kling_timeout_seconds: int = 360
    character_ai_animation: str = "auto"
    character_animation_max_retries: int = 2
    character_animation_qc: bool = True
    webapp_base_url: str = ""
    railway_public_domain: str = ""
    webapp_port: int = 8080
    port: int = 0  # Railway injects PORT; 0 means use webapp_port locally.
    payment_url: str = ""
    payment_provider: str = "custom"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # UniPAY Georgia / UniPAY API V3. Endpoint is supplied by the merchant account.
    unipay_subscription_url: str = ""
    unipay_subscription_update_url: str = ""
    unipay_access_token: str = ""
    unipay_merchant_id: str = ""
    unipay_api_key: str = ""
    unipay_webhook_secret: str = ""
    unipay_webhook_token: str = ""
    unipay_api_key_header: str = "X-Api-Key"
    unipay_merchant_id_header: str = "X-Merchant-Id"
    unipay_webhook_signature_header: str = "X-UniPAY-Signature"
    unipay_webhook_token_header: str = "X-Webhook-Token"
    unipay_webhook_signature_prefix: str = "sha256="
    # Unlimit (official recurring/subscription API). Exact merchant endpoints may vary by region/account.
    unlimit_payment_url: str = ""
    unlimit_token_url: str = ""
    unlimit_recurring_url: str = ""
    unlimit_recurring_update_url: str = ""
    unlimit_api_token: str = ""
    unlimit_terminal_code: str = ""
    unlimit_password: str = ""
    unlimit_callback_secret: str = ""
    unlimit_signature_header: str = "X-Signature"
    # PayPal Subscriptions REST API
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_mode: str = "sandbox"
    paypal_product_id: str = ""
    payment_url_trial: str = ""
    payment_url_group: str = ""
    payment_url_individual: str = ""
    support_chat_url: str = ""
    support_call_label: str = "+995000000000"
    mobile_auth_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "DOME"
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
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def smtp_missing_variables(self) -> tuple[str, ...]:
        """Return missing SMTP secret/config names without exposing their values."""
        required = (
            ("SMTP_HOST", self.smtp_host),
            ("SMTP_USERNAME", self.smtp_username),
            ("SMTP_PASSWORD", self.smtp_password),
            ("SMTP_FROM_EMAIL", self.smtp_from_email),
        )
        return tuple(name for name, value in required if not str(value).strip())

    @model_validator(mode="after")
    def _resolve_persistent_storage(self):
        """Keep DB, imported lessons and settings on the same persistent Railway volume.

        Priority: explicit STORAGE_ROOT > DATA_DIR > ./storage. DOME never auto-adopts /data.
        If DATABASE_URL is still the bundled default, move SQLite beside that storage root.
        """
        storage_explicit = bool(os.getenv("STORAGE_ROOT"))
        data_env = os.getenv("DATA_DIR")
        if not storage_explicit and data_env:
            # DOME never auto-binds to /data just because another Railway service uses it.
            # Set DATA_DIR or STORAGE_ROOT explicitly for this DOME service when persistent storage is desired.
            self.storage_root = Path(data_env)
        if self.database_url.startswith("postgres://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url[len("postgres://"):]
        elif self.database_url.startswith("postgresql://"):
            self.database_url = "postgresql+asyncpg://" + self.database_url[len("postgresql://"):]
        if str(self.database_url) == "sqlite+aiosqlite:///./storage/app.db":
            db_path = (self.storage_root / "app.db").resolve()
            self.database_url = "sqlite+aiosqlite:////" + str(db_path).lstrip("/")
        return self
    @property
    def effective_webapp_port(self) -> int:
        return self.port or self.webapp_port

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
