from typing import Optional, Any
import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseSettings
    SettingsConfigDict = None
    _HAS_PYDANTIC_SETTINGS = False


class Settings(BaseSettings):
    # Brevo API configuration
    BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
    BREVO_SENDER_EMAIL: str = os.getenv("BREVO_SENDER_EMAIL", "noreply@autoinvoice.de")
    BREVO_SENDER_NAME: str = os.getenv("BREVO_SENDER_NAME", "AutoInvoice Buchhaltung")

    # JWT & Auth configuration
    JWT_SECRET: str = os.getenv(
        "JWT_SECRET",
        "autoinvoice_jwt_super_secret_key_change_me_in_production_32bytes",
    )
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Cookie security flag (False for local development over HTTP, True for production HTTPS)
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"

    # Database configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./invoices.db")
    SYNC_DATABASE_URL: str = os.getenv("SYNC_DATABASE_URL", "sqlite:///./invoices.db")

    # Rate limiting
    RATELIMIT_STORAGE_URL: str = os.getenv("RATELIMIT_STORAGE_URL", "memory://")

    # Testing flag
    TESTING: bool = os.getenv("TESTING", "false").lower() == "true"

    if _HAS_PYDANTIC_SETTINGS and SettingsConfigDict is not None:
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )
    else:
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"

    def __init__(self, **values: Any):
        super().__init__(**values)
        self._sync_paths()

    def _sync_paths(self) -> None:
        chat_db = os.getenv("CHAT_DB_PATH")
        if chat_db:
            if "sqlite+aiosqlite:///" in chat_db:
                self.DATABASE_URL = chat_db
                self.SYNC_DATABASE_URL = chat_db.replace("sqlite+aiosqlite:///", "sqlite:///")
            elif chat_db.startswith("sqlite:///"):
                self.SYNC_DATABASE_URL = chat_db
                self.DATABASE_URL = chat_db.replace("sqlite:///", "sqlite+aiosqlite:///")
            elif "://" in chat_db:
                self.DATABASE_URL = chat_db
                self.SYNC_DATABASE_URL = chat_db
            else:
                abs_path = os.path.abspath(chat_db).replace("\\", "/")
                self.DATABASE_URL = f"sqlite+aiosqlite:///{abs_path}"
                self.SYNC_DATABASE_URL = f"sqlite:///{abs_path}"


settings = Settings()
