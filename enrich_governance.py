"""
Recompute moat / track verdict / sector context on existing governance.json (no Screener fetch).

Reads:  data/governance.json, data/valuation.json, data/sector_tags.json
Writes: data/governance.json (in place)

Usage:
  .venv/bin/python3 enrich_governance.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from governance_scores import assign_research_ranks, enrich_governance_record
from sector_stats import attach_sector_context, build_sector_peer_index

ROOT = Path(__file__).resolve().parent
GOV_PATH = ROOT / "data" / "governance.json"
VAL_PATH = ROOT / "data" / "valuation.json"
SECTOR_PATH = ROOT / "data" / "sector_tags.json"


def main() -> int:
    if not GOV_PATH.is_file():
        print(f"Missing {GOV_PATH}", file=sys.stderr)
        return 1

    payload = json.loads(GOV_PATH.read_text(encoding="utf-8"))
    companies: dict = dict(payload.get("companies") or {})

    valuation: dict = {}
    if VAL_PATH.is_file():
        valuation = json.loads(VAL_PATH.read_text(encoding="utf-8")).get("companies") or {}

    sector_tags: dict = {}
    if SECTOR_PATH.is_file():
        sector_tags = json.loads(SECTOR_PATH.read_text(encoding="utf-8")).get("companies") or {}

    peer_index = build_sector_peer_index(sector_tags, valuation=valuation, governance=companies)

    CONCALL_KEYS = (
        "has_concall",
        "concall_count",
        "latest_concall_period",
        "latest_concall_has_transcript",
        "concall_section_present",
    )
    merged_concall = 0
    for sym, rec in companies.items():
        if rec.get("error") or rec.get("has_concall") is not None:
            continue
        val = valuation.get(sym) or valuation.get(str(sym).upper())
        if not val or val.get("has_concall") is None:
            continue
        for k in CONCALL_KEYS:
            if val.get(k) is not None:
                rec[k] = val[k]
        merged_concall += 1

    track = review = skip = 0
    for sym, rec in companies.items():
        if rec.get("error"):
            continue
        tag = sector_tags.get(sym) or sector_tags.get(str(sym).upper())
        enrich_governance_record(
            rec,
            val=valuation.get(sym),
            sector_tag=tag,
        )
        attach_sector_context(rec, tag, peer_index)
        enrich_governance_record(rec, val=valuation.get(sym), sector_ctx=rec.get("sector"))
        v = rec.get("track_verdict")
        if v == "track":
            track += 1
        elif v == "skip":
            skip += 1
        else:
            review += 1

    ranked = assign_research_ranks(companies)

    payload["companies"] = dict(sorted(companies.items()))
    payload.setdefault("stats", {})["track"] = track
    payload["stats"]["review"] = review
    payload["stats"]["skip"] = skip
    payload["stats"]["ranked"] = ranked
    concall_yes = sum(1 for r in companies.values() if r.get("has_concall") is True)
    concall_no = sum(1 for r in companies.values() if r.get("has_concall") is False)
    payload["stats"]["concall_yes"] = concall_yes
    payload["stats"]["concall_no"] = concall_no
    payload["stats"]["concall_checked"] = concall_yes + concall_no
    payload["stats"]["concall_unknown"] = sum(1 for r in companies.values() if r.get("has_concall") is None)
    GOV_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Enriched {len(companies):,} rows → track {track:,} | review {review:,} | skip {skip:,}")
    if merged_concall:
        print(f"Merged concall from valuation for {merged_concall:,} symbols")
    print(f"Ranked {ranked:,} companies (#1 = best research_score)")

    top = sorted(
        (r for r in companies.values() if not r.get("error") and r.get("research_rank")),
        key=lambda r: r["research_rank"],
    )[:5]
    bot = sorted(
        (r for r in companies.values() if not r.get("error") and r.get("research_rank")),
        key=lambda r: r["research_rank"],
        reverse=True,
    )[:3]
    print("Top 5:", ", ".join(f"{r['symbol']} #{r['research_rank']} ({r['research_grade']} {r['research_score']})" for r in top))
    print("Bottom 3:", ", ".join(f"{r['symbol']} #{r['research_rank']} ({r['research_grade']} {r['research_score']})" for r in bot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
