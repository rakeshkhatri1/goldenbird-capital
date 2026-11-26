"""
Remove stored data for symbols below ₹100 Cr after build_market_cap.py completes.

Reads:  data/market_cap.json (source of truth for eligibility)
Prunes: historical cache, sector_tags, valuation, governance, business_updates,
        cache_index.json; optionally rebuilds nse_market_data.json.

Usage (dry run — shows what would be deleted):
  python3 prune_ineligible_universe.py

Execute deletions + JSON prune:
  PRUNE_EXECUTE=1 python3 prune_ineligible_universe.py

Also rebuild UI market data from eligible cache only:
  PRUNE_EXECUTE=1 PRUNE_REBUILD_TECHNICALS=1 python3 prune_ineligible_universe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from universe_filter import (
    MIN_MCAP_CR,
    load_eligible_symbols,
    load_known_ineligible_symbols,
    load_market_cap_index,
    prune_json_companies,
)

ROOT = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("CACHE_DIR", ROOT / "data" / "historical_cache"))
CACHE_INDEX = Path(os.environ.get("CACHE_INDEX_PATH", ROOT / "data" / "cache_index.json"))
SECTOR_TAGS = Path(os.environ.get("SECTOR_TAGS_PATH", ROOT / "data" / "sector_tags.json"))
VALUATION = Path(os.environ.get("VALUATION_PATH", ROOT / "data" / "valuation.json"))
GOVERNANCE = Path(os.environ.get("GOV_PATH", ROOT / "data" / "governance.json"))
BUSINESS_UPDATES = Path(os.environ.get("BU_OUT_PATH", ROOT / "data" / "business_updates.json"))

EXECUTE = os.environ.get("PRUNE_EXECUTE", "0").strip().lower() in ("1", "true", "yes", "on")
REBUILD_TECH = os.environ.get("PRUNE_REBUILD_TECHNICALS", "1").strip().lower() in ("1", "true", "yes", "on")


def _project_python() -> str:
    venv_py = ROOT / ".venv" / "bin" / "python3"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _rebuild_technicals() -> int:
    """Run build_technicals.py in the project venv (needs pandas)."""
    py = _project_python()
    script = ROOT / "build_technicals.py"
    print(f"Rebuilding nse_market_data.json ({py} build_technicals.py) …")
    rc = subprocess.run([py, str(script)], cwd=str(ROOT)).returncode
    if rc != 0:
        print(
            f"build_technicals.py failed (exit {rc}). "
            f"Run manually: {py} build_technicals.py",
            file=sys.stderr,
        )
    return rc


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    mcap_idx = load_market_cap_index()
    if not mcap_idx:
        print("Missing data/market_cap.json — run build_market_cap.py first.", file=sys.stderr)
        return 1

    eligible = load_eligible_symbols(strict=False)
    ineligible = load_known_ineligible_symbols()
    if not ineligible:
        print(f"No symbols with confirmed mcap < ₹{MIN_MCAP_CR:.0f} Cr in market_cap.json.")
        return 0

    cache_files = sorted(
        p for sym in ineligible for p in [CACHE_DIR / f"{sym}.json"] if p.is_file()
    )

    print(
        f"=== Prune universe (< ₹{MIN_MCAP_CR:.0f} Cr) ===\n"
        f"Eligible (keep):     {len(eligible):,}\n"
        f"Ineligible (remove): {len(ineligible):,}\n"
        f"Cache files found:   {len(cache_files):,}\n"
        f"Mode:                {'EXECUTE' if EXECUTE else 'DRY RUN (set PRUNE_EXECUTE=1)'}\n"
    )

    if not EXECUTE:
        if cache_files[:8]:
            print("Sample cache files to delete:")
            for p in cache_files[:8]:
                print(f"  {p.name}")
            if len(cache_files) > 8:
                print(f"  … and {len(cache_files) - 8:,} more")
        return 0

    deleted = 0
    for fp in cache_files:
        try:
            fp.unlink()
            deleted += 1
        except OSError as e:
            print(f"  skip delete {fp.name}: {e}", file=sys.stderr)

    print(f"Deleted {deleted:,} historical cache files.")

    for label, path, key in (
        ("sector_tags", SECTOR_TAGS, "companies"),
        ("valuation", VALUATION, "companies"),
        ("governance", GOVERNANCE, "companies"),
    ):
        payload = _load_json(path)
        if not payload:
            continue
        n = prune_json_companies(payload, ineligible, key=key)
        if n:
            _save_json(path, payload)
            print(f"Pruned {n:,} symbols from {label} → {path.name}")

    bu = _load_json(BUSINESS_UPDATES)
    if bu and isinstance(bu.get("updates"), list):
        before = len(bu["updates"])
        bu["updates"] = [
            u for u in bu["updates"]
            if str(u.get("symbol") or "").upper() not in ineligible
        ]
        removed = before - len(bu["updates"])
        if removed:
            bu.setdefault("stats", {})["total"] = len(bu["updates"])
            _save_json(BUSINESS_UPDATES, bu)
            print(f"Pruned {removed:,} business update rows.")

    idx = _load_json(CACHE_INDEX)
    if idx and isinstance(idx.get("symbols"), list):
        before = len(idx["symbols"])
        idx["symbols"] = sorted(s for s in idx["symbols"] if str(s).upper() not in ineligible)
        idx["count"] = len(idx["symbols"])
        _save_json(CACHE_INDEX, idx)
        print(f"cache_index.json: {before:,} → {idx['count']:,} symbols")

    if REBUILD_TECH and deleted:
        if _rebuild_technicals() != 0:
            return 1

    print("\nDone. All pipelines should use load_eligible_symbols() / filter_targets().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
