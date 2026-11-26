"""
Governance / integrity metrics for the ₹100–1,000 Cr deep-research band (Screener.in).

Reads:  data/screener_company_map.json, data/market_cap.json, data/valuation.json
Writes: data/governance.json

Fields: promoter %, pledge %, CFO/NP, ROCE, moat & capital allocation scores, growth, debt,
shareholding trends, Screener notes, integrity score + decision checklist.

Usage:
  .venv/bin/python3 build_governance.py
  GOV_RESUME=1 GOV_SLEEP=1.5 .venv/bin/python3 build_governance.py
  GOV_REFRESH=1 GOV_SYMBOLS=AXTEL .venv/bin/python3 build_governance.py   # re-fetch with v2 fields
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from governance_scores import assess_capital_allocation, assess_moat, build_decision_checklist
from screener_parse import (
    fetch_screener_for_rec,
    latest_row_value,
    parse_compounded_growth,
    parse_concalls,
    parse_meta_description,
    parse_results_table,
    parse_screener_notes,
    parse_shareholding_quarterly,
    parse_top_ratios,
    row_cagr_pct,
    section_slice,
    strip_html,
)
from screener_utils import build_kite_symbol_index, canonical_symbol, lookup_company_record, screener_slug
from universe_filter import (
    DEEP_RESEARCH_MAX_MCAP_CR,
    DEEP_RESEARCH_MIN_MCAP_CR,
    load_band_symbols,
    load_market_cap_index,
)

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
VAL_PATH = Path(os.environ.get("VALUATION_PATH", ROOT / "data" / "valuation.json"))
OUT_PATH = Path(os.environ.get("GOV_PATH", ROOT / "data" / "governance.json"))

RESUME = os.environ.get("GOV_RESUME", "1").strip().lower() in ("1", "true", "yes", "on")
REFRESH = os.environ.get("GOV_REFRESH", "0").strip().lower() in ("1", "true", "yes", "on")
SLEEP = float(os.environ.get("GOV_SLEEP", "1.5"))
MAX_SYMBOLS = os.environ.get("GOV_MAX", "").strip()
SYMBOL_FILTER = os.environ.get("GOV_SYMBOLS", "").strip()
USER_AGENT = os.environ.get(
    "GOV_USER_AGENT",
    "Mozilla/5.0 (compatible; nse_engine/2.0; +governance scan)",
)

JSON_VERSION = 2

PLEDGE_RE = re.compile(r"Promoters have pledged\s+([\d.]+)%", re.I)
ISSUE_LI_RE = re.compile(r"<li>([^<]{8,200})</li>", re.I)


def _parse_shareholding_promoter(page_html: str) -> float | None:
    chunk = section_slice(page_html, "shareholding")
    if not chunk:
        return None
    idx = chunk.find("showShareholders('promoters', 'quarterly'")
    if idx >= 0:
        sub = chunk[idx : idx + 4000]
        pcts = re.findall(r"([\d.]+)%", sub)
        if pcts:
            try:
                return float(pcts[0])
            except ValueError:
                pass
    return parse_meta_description(page_html).get("promoter_pct")


def _parse_pledge(page_html: str) -> float | None:
    m = PLEDGE_RE.search(page_html)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    head = page_html[:80_000]
    for li in ISSUE_LI_RE.findall(head):
        lm = PLEDGE_RE.search(li)
        if lm:
            try:
                return float(lm.group(1))
            except ValueError:
                pass
    return None


def _parse_issue_flags(page_html: str) -> list[str]:
    flags: list[str] = []
    head = page_html[:120_000]
    keywords = (
        "pledge",
        "debt",
        "loss",
        "declining",
        "low return",
        "not paying",
        "negative",
        "default",
        "fraud",
        "penalty",
    )
    for li in ISSUE_LI_RE.findall(head):
        text = strip_html(li)
        low = text.lower()
        if any(k in low for k in keywords) and text not in flags:
            flags.append(text)
    return flags[:8]


def _last_n(nums: list[float | None], n: int = 3) -> list[float]:
    clean = [float(x) for x in nums if x is not None]
    return clean[-n:] if clean else []


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def _compute_cfo_np(pl_rows: dict[str, list[float | None]], cf_rows: dict[str, list[float | None]]) -> dict:
    np_vals = _last_n(pl_rows.get("Net Profit", []), 3)
    cfo_key = next((k for k in cf_rows if "Cash from Operating Activity" in k), None)
    cfo_vals = _last_n(cf_rows.get(cfo_key or "", []), 3)
    np_avg = _avg(np_vals)
    cfo_avg = _avg(cfo_vals)
    ratio = round(cfo_avg / np_avg, 2) if cfo_avg is not None and np_avg and np_avg > 0 else None
    return {
        "np_3y_avg_cr": np_avg,
        "cfo_3y_avg_cr": cfo_avg,
        "cfo_np_ratio_3y": ratio,
        "latest_np_cr": np_vals[-1] if np_vals else None,
        "latest_cfo_cr": cfo_vals[-1] if cfo_vals else None,
    }


def _integrity_score(
    *,
    pledge_pct: float | None,
    cfo_np_ratio: float | None,
    revenue_cr: float | None,
    roce_pct: float | None,
    issue_flags: list[str],
) -> tuple[int, str, list[str]]:
    score = 100
    flags: list[str] = list(issue_flags)

    if pledge_pct is not None:
        if pledge_pct > 50:
            score -= 35
            flags.append(f"High promoter pledge ({pledge_pct:.1f}%)")
        elif pledge_pct > 25:
            score -= 20
            flags.append(f"Elevated promoter pledge ({pledge_pct:.1f}%)")
        elif pledge_pct > 10:
            score -= 8

    if cfo_np_ratio is not None:
        if cfo_np_ratio < 0.3:
            score -= 30
            flags.append(f"Weak cash conversion — CFO/NP 3Y avg {cfo_np_ratio:.2f}")
        elif cfo_np_ratio < 0.6:
            score -= 15
            flags.append(f"Below-par cash conversion — CFO/NP 3Y avg {cfo_np_ratio:.2f}")
        elif cfo_np_ratio < 0.8:
            score -= 5

    if revenue_cr is not None and revenue_cr < 50:
        score -= 12
        flags.append(f"Small revenue base (₹{revenue_cr:.0f} Cr TTM)")

    if roce_pct is not None and roce_pct < 10:
        score -= 10
        flags.append(f"Low ROCE ({roce_pct:.1f}%)")

    score = max(0, min(100, score))
    if score >= 75:
        tier = "strong"
    elif score >= 50:
        tier = "review"
    else:
        tier = "weak"

    deduped: list[str] = []
    seen: set[str] = set()
    for f in flags:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return score, tier, deduped[:10]


def build_record(kite: str, rec: dict, page_html: str, basis: str, url: str, slug: str) -> dict:
    ratios = parse_top_ratios(page_html)
    meta = parse_meta_description(page_html)
    promoter = _parse_shareholding_promoter(page_html)
    pledge = _parse_pledge(page_html)
    issue_flags = _parse_issue_flags(page_html)

    pl_dates, pl_rows = parse_results_table(section_slice(page_html, "profit-loss"))
    _, cf_rows = parse_results_table(section_slice(page_html, "cash-flow"))
    cfo_block = _compute_cfo_np(pl_rows, cf_rows)

    revenue = meta.get("revenue_ttm_cr")
    profit = meta.get("profit_ttm_cr")
    mcap = ratios.get("market_cap_cr")
    roce = ratios.get("roce_pct")

    score, tier, flags = _integrity_score(
        pledge_pct=pledge,
        cfo_np_ratio=cfo_block.get("cfo_np_ratio_3y"),
        revenue_cr=revenue,
        roce_pct=roce,
        issue_flags=issue_flags,
    )

    return {
        "symbol": kite,
        "name": rec.get("name") or kite,
        "market_cap_cr": mcap,
        "screener_slug": slug,
        "screener_url": url,
        "documents_url": f"{url.rstrip('/')}#documents",
        "data_basis": basis,
        "promoter_pct": promoter,
        "pledge_pct": pledge,
        "revenue_ttm_cr": revenue,
        "profit_ttm_cr": profit,
        "pe_ttm": ratios.get("pe_ttm"),
        "roce_pct": roce,
        "roe_pct": ratios.get("roe_pct"),
        **cfo_block,
        **parse_concalls(page_html),
        "integrity_score": score,
        "integrity_tier": tier,
        "flags": flags,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _load_existing() -> dict[str, dict]:
    if not OUT_PATH.is_file():
        return {}
    try:
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        return dict(payload.get("companies") or {})
    except json.JSONDecodeError:
        return {}


def _merge_mcap_fields(row: dict, mcap_index: dict[str, dict]) -> None:
    sym = str(row.get("symbol") or "").upper()
    if row.get("market_cap_cr") is not None:
        return
    mrow = mcap_index.get(sym)
    if mrow and mrow.get("market_cap_cr") is not None:
        row["market_cap_cr"] = float(mrow["market_cap_cr"])
        return
    if VAL_PATH.is_file():
        payload = json.loads(VAL_PATH.read_text(encoding="utf-8"))
        vrow = (payload.get("companies") or {}).get(sym)
        if vrow and vrow.get("market_cap_cr") is not None:
            row["market_cap_cr"] = float(vrow["market_cap_cr"])


def _write_output(companies: dict[str, dict], *, ok: int, skipped: int, err: int, t0: float, partial: bool) -> None:
    band_count = sum(
        1
        for row in companies.values()
        if row.get("market_cap_cr") is not None
        and DEEP_RESEARCH_MIN_MCAP_CR <= float(row["market_cap_cr"]) <= DEEP_RESEARCH_MAX_MCAP_CR
    )
    out = {
        "version": JSON_VERSION,
        "band_min_mcap_cr": DEEP_RESEARCH_MIN_MCAP_CR,
        "band_max_mcap_cr": DEEP_RESEARCH_MAX_MCAP_CR,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": {
            "total": len(companies),
            "in_band": band_count,
            "fetched_this_run": ok,
            "skipped": skipped,
            "errors": err,
            "partial": partial,
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "companies": dict(sorted(companies.items())),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}", file=sys.stderr)
        return 1

    company_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    companies_in: dict = company_map.get("companies") or {}
    kite_index = build_kite_symbol_index(companies_in)
    band = load_band_symbols()
    if not band:
        print(
            f"No symbols in ₹{DEEP_RESEARCH_MIN_MCAP_CR:.0f}–{DEEP_RESEARCH_MAX_MCAP_CR:.0f} Cr band — "
            "run build_market_cap.py first.",
            file=sys.stderr,
        )
        return 1

    filter_set = {s.strip().upper() for s in SYMBOL_FILTER.split(",") if s.strip()}
    if filter_set:
        band = band & filter_set

    existing = _load_existing()
    companies: dict[str, dict] = dict(existing)

    targets = sorted(band)
    if MAX_SYMBOLS.isdigit():
        pending = [s for s in targets if not (RESUME and s in companies and companies[s].get("integrity_score") is not None)]
        targets = pending[: int(MAX_SYMBOLS)] if pending else targets[: int(MAX_SYMBOLS)]

    ok = skipped = err = 0
    t0 = time.time()
    mcap_index = load_market_cap_index()
    print(
        f"=== Governance scan (₹{DEEP_RESEARCH_MIN_MCAP_CR:.0f}–{DEEP_RESEARCH_MAX_MCAP_CR:.0f} Cr) ===\n"
        f"Band size: {len(band):,} | this run: {len(targets):,} | resume: {RESUME}\n",
        flush=True,
    )

    for i, kite in enumerate(targets, 1):
        if RESUME and not REFRESH and kite in companies and companies[kite].get("integrity_score") is not None:
            skipped += 1
            continue
        rec = lookup_company_record(companies_in, kite, index=kite_index)
        slug_hint = screener_slug(rec) or canonical_symbol(kite) or kite
        print(f"[{i}/{len(targets)}] {kite}: screener.in/company/{slug_hint}/ …", flush=True)
        try:
            page_html, basis, url, slug = fetch_screener_for_rec(rec, user_agent=USER_AGENT)
            row = build_record(kite, rec, page_html, basis, url, slug)
            _merge_mcap_fields(row, mcap_index)
            val_row = None
            if VAL_PATH.is_file():
                val_payload = json.loads(VAL_PATH.read_text(encoding="utf-8"))
                val_row = (val_payload.get("companies") or {}).get(kite)
            from governance_scores import enrich_governance_record
            enrich_governance_record(row, val=val_row)
            companies[kite] = row
            ok += 1
            print(
                f"  → {row.get('track_verdict', '?')} · integrity {row['integrity_score']} · "
                f"moat {row.get('moat_score')} · prom {row.get('promoter_pct')}% · "
                f"concall {'yes' if row.get('has_concall') else 'no'}",
                flush=True,
            )
        except Exception as e:
            err += 1
            companies[kite] = {
                "symbol": kite,
                "name": rec.get("name") or kite,
                "error": str(e),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            print(f"  → error: {e}", flush=True)
        _write_output(companies, ok=ok, skipped=skipped, err=err, t0=t0, partial=True)
        if SLEEP > 0:
            time.sleep(SLEEP)

    _write_output(companies, ok=ok, skipped=skipped, err=err, t0=t0, partial=False)
    print("Running post-enrich (sector peers + track verdicts)…", flush=True)
    try:
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "enrich_governance.py")], check=False, cwd=str(ROOT))
    except OSError:
        pass
    print(
        f"\nDone → {OUT_PATH}\n"
        f"  fetched: {ok:,} | skipped: {skipped:,} | errors: {err:,} | total rows: {len(companies):,}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
