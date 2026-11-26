"""
Backfill concall fields on governance.json from Screener documents (no full governance re-scrape).

Reads:  data/governance.json, data/screener_company_map.json
Writes: data/governance.json, data/concalls.json (lightweight index for UI)

Usage:
  .venv/bin/python3 enrich_concalls.py
  CONCALL_MAX=50 CONCALL_SLEEP=1.5 .venv/bin/python3 enrich_concalls.py
  CONCALL_REFRESH=1 .venv/bin/python3 enrich_concalls.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from screener_parse import fetch_screener_for_rec, parse_concalls
from screener_utils import build_kite_symbol_index, lookup_company_record

ROOT = Path(__file__).resolve().parent
GOV_PATH = ROOT / "data" / "governance.json"
CONCALLS_PATH = ROOT / "data" / "concalls.json"
MAP_PATH = ROOT / "data" / "screener_company_map.json"
SLEEP = float(os.environ.get("CONCALL_SLEEP", "1.0"))
MAX_SYMBOLS = os.environ.get("CONCALL_MAX", "").strip()
REFRESH = os.environ.get("CONCALL_REFRESH", "0").strip().lower() in ("1", "true", "yes", "on")
SAVE_EVERY = max(1, int(os.environ.get("CONCALL_SAVE_EVERY", "1")))
USER_AGENT = os.environ.get(
    "CONCALL_USER_AGENT",
    "Mozilla/5.0 (compatible; nse_engine/2.0; +concall enrich)",
)

CONCALL_KEYS = (
    "has_concall",
    "concall_count",
    "latest_concall_period",
    "latest_concall_has_transcript",
    "concall_section_present",
)


def _concall_stats(companies: dict) -> dict:
    yes = sum(1 for r in companies.values() if r.get("has_concall") is True)
    no = sum(1 for r in companies.values() if r.get("has_concall") is False)
    return {
        "concall_yes": yes,
        "concall_no": no,
        "concall_checked": yes + no,
        "concall_unknown": sum(1 for r in companies.values() if r.get("has_concall") is None),
    }


def _write_outputs(payload: dict, companies: dict, *, partial: bool) -> None:
    stats = _concall_stats(companies)
    payload["companies"] = dict(sorted(companies.items()))
    payload.setdefault("stats", {}).update(stats)
    payload["stats"]["concall_partial"] = partial
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    GOV_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    index = {
        sym: {k: rec[k] for k in CONCALL_KEYS if k in rec}
        for sym, rec in companies.items()
        if rec.get("has_concall") is not None
    }
    CONCALLS_PATH.write_text(
        json.dumps(
            {
                "updated_at": payload["updated_at"],
                "stats": stats,
                "partial": partial,
                "companies": index,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    if not GOV_PATH.is_file():
        print(f"Missing {GOV_PATH}", file=sys.stderr)
        return 1
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}", file=sys.stderr)
        return 1

    payload = json.loads(GOV_PATH.read_text(encoding="utf-8"))
    companies: dict = dict(payload.get("companies") or {})
    company_map = json.loads(MAP_PATH.read_text(encoding="utf-8")).get("companies") or {}
    kite_index = build_kite_symbol_index(company_map)

    targets = [
        sym
        for sym, rec in companies.items()
        if not rec.get("error")
        and (REFRESH or rec.get("has_concall") is None)
    ]
    if MAX_SYMBOLS.isdigit():
        targets = targets[: int(MAX_SYMBOLS)]

    yes = no = err = skipped = 0
    print(f"Concall backfill: {len(targets):,} symbols (refresh={REFRESH})", flush=True)
    for i, sym in enumerate(targets, 1):
        rec = companies.get(sym) or {}
        map_rec = lookup_company_record(company_map, sym, index=kite_index)
        if not map_rec:
            skipped += 1
            print(f"[{i}/{len(targets)}] {sym}: no screener map — skip", flush=True)
            continue
        print(f"[{i}/{len(targets)}] {sym} …", flush=True)
        try:
            page_html, _basis, _url, _slug = fetch_screener_for_rec(map_rec, user_agent=USER_AGENT)
            concall = parse_concalls(page_html)
            rec.update(concall)
            companies[sym] = rec
            if concall.get("has_concall"):
                yes += 1
                print(
                    f"  → yes · {concall.get('concall_count')} listed · latest {concall.get('latest_concall_period') or '—'}",
                    flush=True,
                )
            else:
                no += 1
                reason = "empty list" if concall.get("concall_section_present") else "no Screener section"
                print(f"  → no ({reason})", flush=True)
        except Exception as e:
            err += 1
            print(f"  → error: {e}", flush=True)
        if i % SAVE_EVERY == 0 or i == len(targets):
            _write_outputs(payload, companies, partial=(i < len(targets)))
        if SLEEP > 0:
            time.sleep(SLEEP)

    _write_outputs(payload, companies, partial=False)
    stats = _concall_stats(companies)
    print(
        f"Done → yes {yes:,} | no {no:,} | errors {err:,} | skipped {skipped:,} | "
        f"total checked {stats['concall_checked']:,}/{len(companies):,} | saved {GOV_PATH}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
