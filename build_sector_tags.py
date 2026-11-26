"""
Fetch sector / industry tags from Screener.in for every company in the map.

Reads:  data/screener_company_map.json
Writes: data/sector_tags.json

Hierarchy per company (from Screener + sub_tag rules):
  broad_sector → sector → broad_industry → industry → sub_tag

Examples:
  HDFCBANK: Financial Services › Banks › Private Sector Bank
  SBIN:     Financial Services › Banks › Public Sector Bank
  UJJIVANSFB: Financial Services › Banks › Small Finance Bank

Usage:
  python3 build_sector_tags.py                    # all ~5.3k companies
  SECTOR_TAGS_MAX=20 python3 build_sector_tags.py # smoke test
  SECTOR_TAGS_SLEEP=1.0 SECTOR_TAGS_RESUME=1 python3 build_sector_tags.py

Resume skips symbols already in sector_tags.json. Default sleep 2s per request.
On HTTP 429 (rate limit), retries with exponential backoff before giving up.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from screener_parse import fetch_html
from screener_utils import screener_slug, screener_slug_candidates
from universe_filter import load_eligible_symbols

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
OUT_PATH = Path(os.environ.get("SECTOR_TAGS_PATH", ROOT / "data" / "sector_tags.json"))
SLEEP = float(os.environ.get("SECTOR_TAGS_SLEEP", "2.0"))
MAX_SYMBOLS = os.environ.get("SECTOR_TAGS_MAX", "").strip()
RESUME = os.environ.get("SECTOR_TAGS_RESUME", "1").strip().lower() in ("1", "true", "yes", "on")
MAX_RETRIES = int(os.environ.get("SECTOR_TAGS_MAX_RETRIES", "6"))
BACKOFF_BASE = float(os.environ.get("SECTOR_TAGS_BACKOFF", "10.0"))
USER_AGENT = os.environ.get(
    "SECTOR_TAGS_USER_AGENT",
    "nse_engine/1.0 (+sector tagging; respectful scraper)",
)

FIELD_RE = re.compile(
    r'title="(Broad Sector|Sector|Broad Industry|Industry)">([^<]+)</a>',
    re.I,
)

GROUP_LEVELS = ["broad_sector", "sector", "broad_industry", "industry", "sub_tag"]


def _fetch_company_html(slug: str) -> str:
    return fetch_html(f"https://www.screener.in/company/{slug}/", user_agent=USER_AGENT)


def _fetch_classification(rec: dict) -> tuple[str, dict[str, str]]:
    """Try slug candidates until one returns sector fields."""
    last_err: Exception | None = None
    slugs = screener_slug_candidates(rec)
    if not slugs:
        raise ValueError("no_screener_slug")

    for slug in slugs:
        try:
            page = _fetch_company_html(slug)
            fields = _parse_classification(page)
            if fields.get("industry") or fields.get("sector"):
                return slug, fields
            last_err = ValueError("no_sector_fields")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue
            raise
    if isinstance(last_err, urllib.error.HTTPError):
        raise last_err
    if last_err:
        raise last_err
    raise ValueError("no_sector_fields")


def _parse_classification(page_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    key_map = {
        "broad sector": "broad_sector",
        "sector": "sector",
        "broad industry": "broad_industry",
        "industry": "industry",
    }
    for m in FIELD_RE.finditer(page_html):
        label = m.group(1).strip().lower()
        val = html.unescape(m.group(2).strip())
        key = key_map.get(label)
        if key and val:
            fields[key] = val
    return fields


def derive_sub_tag(kite_symbol: str, name: str, fields: dict[str, str]) -> str:
    """Finer peer group within industry (e.g. private vs PSU vs SFB banks)."""
    industry = (fields.get("industry") or "").strip()
    broad_industry = (fields.get("broad_industry") or "").strip()
    sym = kite_symbol.upper()
    nm = (name or "").upper()
    il = industry.lower()
    bi = broad_industry.lower()

    if bi == "banks" or "bank" in il:
        if "private sector" in il:
            return "Private Sector Bank"
        if "public sector" in il:
            return "Public Sector Bank"
        if sym.endswith("SFB") or "SMALL FINANCE" in nm:
            return "Small Finance Bank"
        if "foreign" in il:
            return "Foreign Bank"
        if "payment" in il:
            return "Payments Bank"
        if industry:
            return industry
        return "Other Bank"

    if industry:
        return industry
    if broad_industry:
        return broad_industry
    return fields.get("sector") or fields.get("broad_sector") or "Untagged"


def build_group_path(fields: dict[str, str], sub_tag: str) -> str:
    parts = []
    for key in ("broad_sector", "broad_industry", "sub_tag"):
        val = sub_tag if key == "sub_tag" else fields.get(key)
        if val and (not parts or parts[-1] != val):
            parts.append(val)
    return " › ".join(parts) if parts else sub_tag


def enrich_record(kite: str, rec: dict, fields: dict[str, str], slug: str) -> dict:
    name = rec.get("name") or kite
    sub_tag = derive_sub_tag(kite, name, fields)
    return {
        "kite_symbol": kite,
        "canonical_symbol": rec.get("canonical_symbol") or kite,
        "name": name,
        "screener_slug": slug,
        "screener_url": f"https://www.screener.in/company/{slug}/",
        **fields,
        "sub_tag": sub_tag,
        "group_path": build_group_path(fields, sub_tag),
    }


def _load_existing() -> dict:
    if not OUT_PATH.is_file():
        return {"version": 1, "companies": {}, "failures": []}
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "companies": {}, "failures": []}
    data.setdefault("companies", {})
    data.setdefault("failures", [])
    return data


def _re_enrich_all(tagged: dict, companies_in: dict) -> None:
    """Recompute sub_tag / group_path for existing rows (no HTTP)."""
    for kite, row in list(tagged.items()):
        rec = companies_in.get(kite) or companies_in.get(row.get("canonical_symbol", "")) or {}
        fields = {k: row[k] for k in ("broad_sector", "sector", "broad_industry", "industry") if row.get(k)}
        if not fields:
            continue
        tagged[kite] = enrich_record(
            kite,
            {"name": row.get("name") or rec.get("name"), "canonical_symbol": row.get("canonical_symbol")},
            fields,
            row.get("screener_slug") or screener_slug(rec) or kite,
        )


def main() -> None:
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}. Run build_screener_map.py first.", file=sys.stderr)
        sys.exit(1)

    company_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    companies_in = company_map.get("companies") or {}
    existing = _load_existing()
    tagged: dict = dict(existing.get("companies") or {})
    failures: list = [f for f in (existing.get("failures") or []) if f.get("kite_symbol") not in tagged]

    _re_enrich_all(tagged, companies_in)

    targets = sorted(companies_in.values(), key=lambda r: (r.get("kite_symbol") or ""))
    eligible = load_eligible_symbols(strict=False)
    if eligible:
        targets = [
            r for r in targets
            if (r.get("kite_symbol") or r.get("canonical_symbol") or "").upper() in eligible
        ]
    if MAX_SYMBOLS.isdigit():
        targets = targets[: int(MAX_SYMBOLS)]

    to_process = 0
    for rec in targets:
        kite = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
        if not kite:
            continue
        if RESUME and kite in tagged and tagged[kite].get("sub_tag"):
            continue
        to_process += 1

    print(
        f"=== Sector tags run ===\n"
        f"Universe: {len(targets):,} | already tagged: {len(tagged):,} | "
        f"to fetch this run: {to_process:,}\n"
        f"Resume: {RESUME} | sleep: {SLEEP}s | out: {OUT_PATH}\n",
        flush=True,
    )

    ok = skipped = err = 0
    t0 = time.time()

    for i, rec in enumerate(targets, 1):
        kite = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
        if not kite:
            continue
        if RESUME and kite in tagged and tagged[kite].get("sub_tag"):
            skipped += 1
            if skipped == 1 or skipped % 500 == 0:
                print(f"… resume skip {skipped:,} already tagged", flush=True)
            continue

        slug = screener_slug(rec)
        slug_hint = slug or "?"
        print(f"[{i}/{len(targets)}] {kite}: fetching screener.in/company/{slug_hint}/ …", flush=True)
        try:
            used_slug, fields = _fetch_classification(rec)
            if used_slug != slug_hint:
                print(f"  … used alternate slug {used_slug}", flush=True)

            tagged[kite] = enrich_record(kite, rec, fields, used_slug)
            ok += 1
            failures = [f for f in failures if f.get("kite_symbol") != kite]
            print(f"  → OK ({ok:,} this run): {tagged[kite]['group_path']}", flush=True)

            if ok % 100 == 0:
                _write_output(companies_in, tagged, failures, ok, skipped, err, t0, partial=True)
                print(f"  … checkpoint saved ({len(tagged):,} total tagged)", flush=True)

        except urllib.error.HTTPError as e:
            failures.append({"kite_symbol": kite, "screener_slug": slug, "reason": f"http_{e.code}"})
            err += 1
            print(f"  → HTTP {e.code} (tried {len(screener_slug_candidates(rec))} slug(s))", flush=True)
        except ValueError as e:
            reason = str(e)
            failures.append({"kite_symbol": kite, "screener_slug": slug, "reason": reason})
            err += 1
            print(f"  → {reason}", flush=True)
        except Exception as e:
            failures.append({"kite_symbol": kite, "screener_slug": slug, "reason": str(e)[:120]})
            err += 1
            print(f"  → error: {e}", flush=True)

        time.sleep(SLEEP)

    _write_output(companies_in, tagged, failures, ok, skipped, err, t0, partial=False)


def _write_output(
    companies_in: dict,
    tagged: dict,
    failures: list,
    ok: int,
    skipped: int,
    err: int,
    t0: float,
    *,
    partial: bool,
) -> None:
    out = {
        "version": 2,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "screener.in company pages",
        "map_source": str(MAP_PATH),
        "group_levels": GROUP_LEVELS,
        "group_level_default": "sub_tag",
        "stats": {
            "map_companies": len(companies_in),
            "tagged": len(tagged),
            "processed_this_run": ok,
            "skipped_resume": skipped,
            "failures": len(failures),
            "elapsed_sec": round(time.time() - t0, 1),
            "partial": partial,
        },
        "companies": dict(sorted(tagged.items())),
        "failures": failures,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not partial:
        print()
        print("=== Sector tags ===")
        print(f"Tagged companies: {len(tagged):,} / {len(companies_in):,}")
        print(f"This run OK:      {ok:,} | skipped: {skipped:,} | errors: {err:,}")
        print(f"Wrote:            {OUT_PATH}")


if __name__ == "__main__":
    main()
