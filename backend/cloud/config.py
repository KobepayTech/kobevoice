"""Configuration for the Kobevoice Cloud control-plane.

All settings come from environment variables (or a local ``.env``) so the
service is twelve-factor and deployable without code changes. Defaults are
development-friendly and safe to run with no configuration at all.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KOBEVOICE_CLOUD_",
        env_file=".env",
        extra="ignore",
    )

    # --- Core ---
    database_url: str = "sqlite:///./data/cloud.db"
    frontend_url: str = "http://localhost:5173"
    environment: str = "development"
    # Where per-user generated audio is stored (local dir; swap for S3/R2 later).
    audio_dir: str = "./data/cloud_audio"

    # --- Auth / JWT ---
    # CHANGE THIS IN PRODUCTION. A random value is acceptable for dev only.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # --- Seed admin (the internal Kobevoice "studio" backoffice operator) ---
    admin_email: str = "admin@kobevoice.app"
    admin_password: str = "changeme123"

    # --- Voice engine (the GPU/ML backend this control-plane proxies to) ---
    engine_url: str = "http://127.0.0.1:8000"

    # --- Stripe (optional — billing is gracefully disabled when unset) ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Comma-free: map plan slug -> Stripe price id via env, e.g.
    #   KOBEVOICE_CLOUD_STRIPE_PRICE_CLOUD=price_123
    stripe_price_cloud: str = ""
    stripe_price_pro: str = ""

    # Dev-only: when true (and Stripe is NOT configured), "checkout" activates
    # the chosen plan immediately in the local DB so the full upgrade flow can
    # be exercised without Stripe keys. Never enable in production.
    billing_dev_mode: bool = False

    @property
    def billing_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def billing_available(self) -> bool:
        """Either real Stripe or the local dev simulator can process upgrades."""
        return self.billing_enabled or self.billing_dev_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()
