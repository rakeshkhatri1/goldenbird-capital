"""Environment-backed settings.

Secrets stay in process environment / ``.env`` (never committed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration for builders and object storage."""

    root: Path
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    r2_endpoint: str
    data_base_url: str

    @property
    def r2_configured(self) -> bool:
        return bool(
            self.r2_access_key_id
            and self.r2_secret_access_key
            and self.r2_bucket
            and self.r2_endpoint
        )


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@lru_cache(maxsize=1)
def get_settings(*, dotenv_path: Path | None = None) -> Settings:
    """Load settings once per process.

    Parameters
    ----------
    dotenv_path:
        Optional explicit ``.env`` path (useful in tests).
    """
    path = dotenv_path or (ROOT / ".env")
    # Explicit path (tests) overrides process env; normal boot does not clobber CI secrets.
    load_dotenv(path, override=dotenv_path is not None)
    account = _env("R2_ACCOUNT_ID")
    endpoint = _env("R2_ENDPOINT")
    if not endpoint and account:
        endpoint = f"https://{account}.r2.cloudflarestorage.com"

    return Settings(
        root=ROOT,
        r2_account_id=account,
        r2_access_key_id=_env("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_env("R2_SECRET_ACCESS_KEY"),
        r2_bucket=_env("R2_BUCKET", "goldenbird-nse-engine"),
        r2_endpoint=endpoint.rstrip("/"),
        data_base_url=_env("DATA_BASE_URL").rstrip("/"),
    )


def clear_settings_cache() -> None:
    """Reset cached settings (tests only)."""
    get_settings.cache_clear()
