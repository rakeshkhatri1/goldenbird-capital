"""Upload curated UI JSON packs to R2 ``ui/``.

Usage
-----
::

    python -m goldenbird.cli.upload_ui
    # or after editable install:
    gb-r2-upload-ui
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from goldenbird.config import get_settings
from goldenbird.storage.r2 import UI_PREFIX, create_r2_store

DEFAULT_NAMES = (
    "realty_peer_compare.json",
    "realty_demand_pulse.json",
    "realty_universe_screener.json",
    "realty_vs_nifty_realty_1y.json",
    "realty_vs_nifty_realty_5y.json",
    "fii_dii.json",
    "research_feeds.json",
)


def _candidate_dirs(root: Path) -> list[Path]:
    return [root / "site" / "data", root / "data"]


def resolve_pack(root: Path, name: str) -> Path | None:
    for directory in _candidate_dirs(root):
        path = directory / name
        if path.is_file():
            return path
    return None


def upload_ui_packs(
    *,
    names: tuple[str, ...] = DEFAULT_NAMES,
    dry_run: bool = False,
) -> list[str]:
    settings = get_settings()
    uploaded: list[str] = []
    store = None if dry_run else create_r2_store(settings)

    for name in names:
        local = resolve_pack(settings.root, name)
        if local is None:
            print(f"skip (missing): {name}", file=sys.stderr)
            continue
        key = f"{UI_PREFIX}{name}"
        if dry_run:
            print(f"dry-run: {local} -> {key}")
        else:
            assert store is not None
            store.upload_file(local, key)
            print(f"uploaded: {key} ({local.stat().st_size} bytes)")
        uploaded.append(key)
    return uploaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload UI JSON packs to Cloudflare R2")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without contacting R2",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="FILE",
        help="Upload only these filenames (default: curated UI set)",
    )
    args = parser.parse_args(argv)
    names = tuple(args.only) if args.only else DEFAULT_NAMES
    upload_ui_packs(names=names, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
