"""
Fetch Screener.in market cap for the full NSE universe; enforce ≥₹100 Cr eligibility.

Reads:  data/screener_company_map.json, data/valuation.json (merge)
Writes: data/market_cap.json

Usage:
  python3 build_market_cap.py
  MCAP_RESUME=1 MCAP_SLEEP=1.5 python3 build_market_cap.py
  MCAP_BOOTSTRAP=1 python3 build_market_cap.py   # valuation merge only, no HTTP
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path

from screener_parse import fetch_html, parse_market_cap
from screener_utils import screener_slug, screener_slug_candidates
from universe_filter import MIN_MCAP_CR

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
VAL_PATH = Path(os.environ.get("VALUATION_PATH", ROOT / "data" / "valuation.json"))
OUT_PATH = Path(os.environ.get("MCAP_PATH", ROOT / "data" / "market_cap.json"))

RESUME = os.environ.get("MCAP_RESUME", "1").strip().lower() in ("1", "true", "yes", "on")
BOOTSTRAP_ONLY = os.environ.get("MCAP_BOOTSTRAP", "0").strip().lower() in ("1", "true", "yes", "on")
SLEEP = float(os.environ.get("MCAP_SLEEP", "1.2"))
MAX_SYMBOLS = os.environ.get("MCAP_MAX", "").strip()
SYMBOL_FILTER = os.environ.get("MCAP_SYMBOLS", "").strip()
USER_AGENT = os.environ.get(
    "MCAP_USER_AGENT",
    "Mozilla/5.0 (compatible; nse_engine/2.0; +market-cap scan)",
)

JSON_VERSION = 1


def _fetch_market_cap(rec: dict) -> tuple[float | None, str | None]:
    for slug in screener_slug_candidates(rec):
        url = f"https://www.screener.in/company/{slug}/"
        try:
            html = fetch_html(url, user_agent=USER_AGENT)
            mcap = parse_market_cap(html)
            if mcap is not None:
                return mcap, slug
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
    return None, None


def _load_existing() -> dict:
    if not OUT_PATH.is_file():
        return {}
    try:
        return json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _merge_valuation(companies: dict[str, dict]) -> int:
    if not VAL_PATH.is_file():
        return 0
    payload = json.loads(VAL_PATH.read_text(encoding="utf-8"))
    merged = 0
    for sym, row in (payload.get("companies") or {}).items():
        kite = str(sym).strip().upper()
        mcap = row.get("market_cap_cr")
        if mcap is None:
            continue
        if companies.get(kite, {}).get("market_cap_cr") is not None:
            continue
        companies[kite] = {
            "market_cap_cr": float(mcap),
            "eligible": float(mcap) >= MIN_MCAP_CR,
            "source": "valuation",
            "screener_slug": row.get("screener_slug"),
        }
        merged += 1
    return merged


def _write_output(companies: dict[str, dict], *, ok: int, skipped: int, err: int, t0: float, partial: bool) -> None:
    eligible = sorted(
        sym for sym, row in companies.items()
        if row.get("eligible") or (row.get("market_cap_cr") or 0) >= MIN_MCAP_CR
    )
    out = {
        "version": JSON_VERSION,
        "min_mcap_cr": MIN_MCAP_CR,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": {
            "total": len(companies),
            "eligible": len(eligible),
            "fetched_this_run": ok,
            "skipped": skipped,
            "errors": err,
            "partial": partial,
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "eligible_symbols": eligible,
        "companies": dict(sorted(companies.items())),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}. Run build_screener_map.py first.", file=sys.stderr)
        sys.exit(1)

    company_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    companies_in = company_map.get("companies") or {}
    existing = _load_existing()
    companies: dict[str, dict] = dict(existing.get("companies") or {})

    val_merged = _merge_valuation(companies)
    if val_merged:
        print(f"Merged {val_merged:,} market caps from valuation.json", flush=True)

    filter_set = {s.strip().upper() for s in SYMBOL_FILTER.split(",") if s.strip()}
    targets = sorted(companies_in.values(), key=lambda r: (r.get("kite_symbol") or ""))
    if filter_set:
        targets = [
            r for r in targets
            if (r.get("kite_symbol") or r.get("canonical_symbol") or "").upper() in filter_set
        ]
    if MAX_SYMBOLS.isdigit():
        targets = targets[: int(MAX_SYMBOLS)]

    if BOOTSTRAP_ONLY:
        _write_output(companies, ok=0, skipped=0, err=0, t0=time.time(), partial=False)
        eligible = sum(1 for r in companies.values() if r.get("eligible"))
        print(f"Bootstrap only → {len(companies):,} with mcap, {eligible:,} eligible (≥₹{MIN_MCAP_CR:.0f} Cr)", flush=True)
        return

    ok = skipped = err = 0
    t0 = time.time()
    print(
        f"=== Market cap scan ===\n"
        f"Universe: {len(targets):,} | known mcap: {len(companies):,} | "
        f"min: ₹{MIN_MCAP_CR:.0f} Cr | resume: {RESUME}\n",
        flush=True,
    )

    for i, rec in enumerate(targets, 1):
        kite = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
        if not kite:
            continue
        if RESUME and kite in companies and companies[kite].get("market_cap_cr") is not None:
            skipped += 1
            if skipped == 1 or skipped % 500 == 0:
                print(f"… resume skip {skipped:,} already known", flush=True)
            continue

        slug = screener_slug(rec) or "?"
        print(f"[{i}/{len(targets)}] {kite}: fetching screener.in/company/{slug}/ …", flush=True)
        try:
            mcap, used_slug = _fetch_market_cap(rec)
            if mcap is None:
                companies[kite] = {
                    "market_cap_cr": None,
                    "eligible": False,
                    "source": "screener",
                    "screener_slug": used_slug or slug,
                }
                err += 1
                print("  → no market cap found", flush=True)
            else:
                eligible = mcap >= MIN_MCAP_CR
                companies[kite] = {
                    "market_cap_cr": mcap,
                    "eligible": eligible,
                    "source": "screener",
                    "screener_slug": used_slug or slug,
                }
                ok += 1
                tag = "eligible" if eligible else "below min"
                print(f"  → ₹{mcap:,.0f} Cr ({tag})", flush=True)

            if ok and ok % 100 == 0:
                _write_output(companies, ok=ok, skipped=skipped, err=err, t0=t0, partial=True)
                print(f"  … checkpoint saved ({len(companies):,} total)", flush=True)

        except Exception as e:
            err += 1
            print(f"  → error: {e}", flush=True)

        time.sleep(SLEEP)

    _write_output(companies, ok=ok, skipped=skipped, err=err, t0=t0, partial=False)
    eligible_n = sum(1 for r in companies.values() if r.get("eligible"))
    print(
        f"\nDone: fetched {ok:,}, skipped {skipped:,}, errors {err:,} | "
        f"{eligible_n:,} eligible ≥₹{MIN_MCAP_CR:.0f} Cr → {OUT_PATH}",
        flush=True,
    )


if __name__ == "__main__":
    main()
