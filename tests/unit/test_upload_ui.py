from __future__ import annotations

from pathlib import Path

from goldenbird.cli.upload_ui import resolve_pack


def test_resolve_pack_prefers_site_data(tmp_path: Path, monkeypatch) -> None:
    site = tmp_path / "site" / "data"
    data = tmp_path / "data"
    site.mkdir(parents=True)
    data.mkdir(parents=True)
    (site / "fii_dii.json").write_text("{}", encoding="utf-8")
    (data / "fii_dii.json").write_text('{"local":1}', encoding="utf-8")

    found = resolve_pack(tmp_path, "fii_dii.json")
    assert found == site / "fii_dii.json"


def test_resolve_pack_falls_back_to_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "fii_dii.json").write_text("{}", encoding="utf-8")
    assert resolve_pack(tmp_path, "fii_dii.json") == data / "fii_dii.json"
