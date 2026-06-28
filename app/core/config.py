from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "DarkAtlas Asset Management"
    API_V1_STR: str = "/api/v1"

    # Database
    POSTGRES_ASYNC_URI: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/darkatlas"
    )

    # Cache
    REDIS_URI: str = "redis://localhost:6379/0"

    # Security
    JWT_SECRET: str = "super-secret-jwt-key-for-darkatlas-asm-platform-2026"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Asset Management
    STALE_THRESHOLD_DAYS: int = 30

    # Allow reading from environment variables and an optional .env file
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
