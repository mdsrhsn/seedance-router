"""Configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "See .env.example for the full list."
        )
    return value


# Railway hands out postgres:// but SQLAlchemy 2.x expects postgresql://
_db_url = _require("DATABASE_URL")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

DATABASE_URL = _db_url
SECRET_KEY = _require("SECRET_KEY")
FERNET_KEY = _require("FERNET_KEY")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = _require("ADMIN_PASSWORD")
ARK_BASE_URL = os.environ.get(
    "ARK_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"
)

# Router behaviour
RATE_LIMIT_COOLDOWN_SECONDS = int(os.environ.get("RATE_LIMIT_COOLDOWN_SECONDS", "60"))
SERVER_ERROR_COOLDOWN_SECONDS = int(os.environ.get("SERVER_ERROR_COOLDOWN_SECONDS", "30"))
MAX_ATTEMPTS_PER_JOB = int(os.environ.get("MAX_ATTEMPTS_PER_JOB", "4"))
WORKER_INTERVAL_SECONDS = int(os.environ.get("WORKER_INTERVAL_SECONDS", "10"))

# Warn when a credential drops below this much remaining quota
LOW_QUOTA_THRESHOLD = float(os.environ.get("LOW_QUOTA_THRESHOLD", "20"))
