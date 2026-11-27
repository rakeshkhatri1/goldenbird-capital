from __future__ import annotations

from pathlib import Path

from goldenbird.config import clear_settings_cache, get_settings


def test_get_settings_builds_endpoint_from_account_id(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "R2_ACCOUNT_ID=abc123",
                "R2_ACCESS_KEY_ID=key",
                "R2_SECRET_ACCESS_KEY=secret",
                "R2_BUCKET=test-bucket",
                "DATA_BASE_URL=https://cdn.example/ui/",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("R2_ENDPOINT", raising=False)
    clear_settings_cache()
    settings = get_settings(dotenv_path=env_file)

    assert settings.r2_configured is True
    assert settings.r2_endpoint == "https://abc123.r2.cloudflarestorage.com"
    assert settings.r2_bucket == "test-bucket"
    assert settings.data_base_url == "https://cdn.example/ui"
    clear_settings_cache()


def test_r2_not_configured_without_keys(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("R2_BUCKET=only-bucket\n", encoding="utf-8")
    for key in (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_ENDPOINT",
        "R2_ACCOUNT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    clear_settings_cache()
    settings = get_settings(dotenv_path=env_file)
    assert settings.r2_configured is False
    clear_settings_cache()
