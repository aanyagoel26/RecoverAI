"""Environment configuration. Secrets are read from the environment only and never sent to the frontend."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # development | production | test
    app_name: str = "RecoverAI"
    # SQLite works out of the box for local dev and tests; use the Supabase Postgres URL in production.
    database_url: str = "sqlite:///./recoverai.db"
    jwt_secret: str = "change-me-in-production-please-32+chars"
    jwt_ttl_minutes: int = 60 * 12
    frontend_url: str = "http://localhost:5173"
    public_api_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:5173"

    # PayPal (server-side only)
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""
    paypal_currency: str = "USD"

    # Encryption key for credentials saved from the dashboard (Fernet, urlsafe base64 32 bytes).
    # If empty, a key is derived from JWT_SECRET (fine for dev; set it explicitly in production).
    secrets_key: str = ""

    # AI (optional; deterministic fallback when absent). Dashboard-saved keys take precedence.
    ai_provider: str = "auto"  # auto | anthropic | openai
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    # Google Gemini (free tier) via Google's OpenAI-compatible endpoint
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"    ai_timeout_seconds: int = 45

    # Email (optional; preview-only mode when absent)
    resend_api_key: str = ""
    email_from: str = "RecoverAI <recovery@example.com>"

    # SMS (optional) — Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Agent
    agent_loop_seconds: int = 0  # 0 disables the background loop; POST /api/agent/run still works
    seed_demo_on_start: bool = True
    # The demo workspace is shared by everyone who clicks "Explore the demo". Saving credentials there would let one
    # visitor plant keys (or break PayPal) for all others, so integration changes are locked on it by default.
    demo_integrations_locked: bool = True

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_is_sandbox(self) -> bool:
        return "sandbox" in self.paypal_base_url

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key)

    @property
    def email_configured(self) -> bool:
        return bool(self.resend_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
