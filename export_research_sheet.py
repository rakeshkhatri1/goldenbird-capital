"""
Export a shareable research CSV for a watchlist of company names or symbols.

Joins governance.json, valuation.json, sector_tags.json, concalls.json,
and nse_market_data.json. Computes moat, track verdict, profit trend,
multibagger potential, price trend, and capex hints.

Usage:
  .venv/bin/python3 export_research_sheet.py --file data/watchlists/screener_band_2900.txt
  .venv/bin/python3 export_research_sheet.py --symbols JTLIND,DEEPINDS,RAJOOENG
  .venv/bin/python3 export_research_sheet.py --file list.txt --out data/research_sheets/my_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from governance_scores import (
    assess_capital_allocation,
    assess_moat,
    assess_track_verdict,
    compute_research_score,
    enrich_governance_record,
)
from sector_stats import attach_sector_context, build_sector_peer_index

ROOT = Path(__file__).resolve().parent
GOV_PATH = ROOT / "data" / "governance.json"
VAL_PATH = ROOT / "data" / "valuation.json"
SECTOR_PATH = ROOT / "data" / "sector_tags.json"
CONCALL_PATH = ROOT / "data" / "concalls.json"
MAP_PATH = ROOT / "data" / "screener_company_map.json"
TECH_PATH = ROOT / "nse_market_data.json"
OUT_DIR = ROOT / "data" / "research_sheets"

NAME_OVERRIDES: dict[str, str] = {
    "R SYSTEMS INTL": "RSYSTEMS",
    "R SYSTEMS INTL.": "RSYSTEMS",
    "MARATHON NEXTGEN": "MARATHON",
    "GATEWAY DISTRI": "GATEWAY",
    "GATEWAY DISTRIPARKS": "GATEWAY",
    "DREDGING CORPN": "DREDGECORP",
    "DREDGING CORPN.": "DREDGECORP",
    "S P APPARELS": "SPAL",
    "S. P. APPARELS": "SPAL",
    "GANESHA ECOSPHE": "GANECOS",
    "GANESHA ECOSPHE.": "GANECOS",
    "ARVIND SMARTSP": "ARVSMART",
    "ARVIND SMARTSP.": "ARVSMART",
    "EASY TRIP PLANN": "EASEMYTRIP",
    "EASY TRIP PLANN.": "EASEMYTRIP",
    "INSOLATION ENER": "INA",
    "ANTELOPUS SELAN": "ANTELOPUS",
    "CONCORD CONTROL": "CNCRD",
    "ELPRO INTERNATIO": "ELPROINTL",
    "RELIANCE INFRA": "RELINFRA",
    "RELIANCE INFRA.": "RELINFRA",
    "PRINCE PIPES": "PRINCEPIPE",
    "DIVGI TORQ": "DIVGIITTS",
    "ASHIKA CREDIT": "ASHIKA",
    "DDEV PLASTIKS": "DDEVPLSTIK",
    "SATIN CREDITCARE": "SATIN",
    "NALWA SONS INVST": "NSIL",
    "ZAGGLE PREPAID": "ZAGGLE",
    "BN AGROCHEM": "BNAGROCHEM",
    "PRATAAP SNACKS": "DIAMONDYD",
    "YASH HIGHVOLTAGE": "YASHHV",
    "INDO THAI SEC": "INDOTHAI",
    "INDO THAI SEC.": "INDOTHAI",
    "MONARCH NETWORTH": "MONARCH",
    "UNITED FOODBRANDS": "UFBL",
}


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_name(s: str) -> str:
    s = s.upper().strip()
    s = re.sub(r"[^\w\s&().-]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def resolve_symbol(name_or_sym: str, company_map: dict) -> tuple[str | None, str]:
    raw = name_or_sym.strip()
    if not raw:
        return None, raw
    key = _norm_name(raw)
    if key in NAME_OVERRIDES:
        sym = NAME_OVERRIDES[key]
        rec = company_map.get(sym) or {}
        return sym, rec.get("name") or raw

    if raw.upper() in company_map:
        rec = company_map[raw.upper()]
        return raw.upper(), rec.get("name") or raw

    # Exact name match
    for sym, rec in company_map.items():
        if _norm_name(rec.get("name", "")) == key:
            return sym, rec["name"]

    # Prefix / contains
    words = [w for w in re.split(r"[\s.()]+", key) if len(w) > 2]
    candidates: list[tuple[int, str, str]] = []
    for sym, rec in company_map.items():
        rn = _norm_name(rec.get("name", ""))
        score = 0
        if key in rn or rn.startswith(key):
            score += 10
        if words and all(w in rn for w in words[: min(3, len(words))]):
            score += 8
        if words and rn.startswith(words[0]):
            score += 4
        if score:
            candidates.append((score, sym, rec["name"]))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        sym, nm = candidates[0][1], candidates[0][2]
        return sym, nm
    return None, raw


def assess_financial_consistency(rec: dict, val: dict | None) -> dict:
    notes: list[str] = []
    tier = "unknown"
    label = "Data sparse"
    val = val or {}
    history = val.get("quarterly_yoy_history") or []
    yoys = [float(h["yoy_pct"]) for h in history if h.get("yoy_pct") is not None]
    np_latest = rec.get("latest_np_cr")
    np_avg = rec.get("np_3y_avg_cr")
    cfo = rec.get("cfo_np_ratio_3y")

    if len(yoys) >= 3:
        neg = sum(1 for y in yoys if y < 0)
        span = max(yoys) - min(yoys)
        flips = sum(1 for i in range(1, len(yoys)) if (yoys[i] >= 0) != (yoys[i - 1] >= 0))
        if neg == 0 and span <= 18:
            tier, label = "consistent", "Consistent — all recent Q YoY positive"
        elif neg >= 2 or flips >= 2:
            tier, label = "volatile", "Up & down — mixed quarterly profit YoY"
            notes.append(f"{neg} of last {len(yoys)} quarters had negative YoY profit")
        elif val.get("growth_trend") == "accelerating":
            tier, label = "improving", "Improving — profit growth accelerating"
        elif val.get("growth_trend") == "decelerating" or val.get("yoy_off_peak"):
            tier, label = "slowing", "Slowing — growth normalizing vs prior peak"
            if val.get("growth_trend_display"):
                notes.append(str(val["growth_trend_display"]))
        else:
            tier, label = "stable", "Mostly stable — moderate quarter moves"
        notes.append(f"Recent Q YoY range: {min(yoys):.0f}% to {max(yoys):.0f}%")

    if np_latest is not None and np_avg is not None:
        if float(np_latest) < 0:
            notes.append("Latest NP negative")
        elif float(np_avg) > 0 and float(np_latest) < float(np_avg) * 0.65:
            notes.append("Latest NP materially below 3Y average")
        elif float(np_avg) > 0 and float(np_latest) > float(np_avg) * 1.2:
            notes.append("Latest NP above 3Y average — inflection")

    if cfo is not None:
        c = float(cfo)
        if c >= 0.85:
            notes.append(f"CFO/NP {c:.2f} — cash tracks profit")
        elif c < 0.4:
            notes.append(f"CFO/NP {c:.2f} — profit not backed by cash")

    if tier == "unknown" and notes:
        tier, label = "mixed", "Mixed — see notes"
    return {"tier": tier, "label": label, "notes": notes[:6]}


def infer_capex(rec: dict, val: dict | None) -> tuple[str, str]:
    hints: list[str] = []
    for note in rec.get("capital_notes") or []:
        low = str(note).lower()
        if any(k in low for k in ("capex", "fcf", "cash burn", "heavy", "investment", "capacity")):
            hints.append(str(note))
    for line in (val or {}).get("val_rationale") or []:
        low = str(line).lower()
        if any(k in low for k in ("capex", "capacity", "expansion", "plant", "investment")):
            hints.append(str(line).replace("Rating:", "").strip()[:120])
    fcf = rec.get("fcf_latest_cr")
    if fcf is not None and float(fcf) < 0:
        hints.append(f"Negative FCF ₹{float(fcf):.1f} Cr — likely heavy capex or WC")
    if hints:
        return "Yes / likely", "; ".join(dict.fromkeys(hints)[:3])
    cap_tier = rec.get("capital_tier") or assess_capital_allocation(rec).get("capital_tier")
    if cap_tier == "strong":
        return "Moderate / self-funded", "Positive FCF or strong cash conversion — capex likely manageable"
    return "Unknown", "No explicit capex signal — check annual report / concall"


def assess_multibagger(rec: dict, val: dict | None, fin: dict, track: dict) -> tuple[str, str]:
    val = val or {}
    reasons: list[str] = []
    score = 0
    peg = val.get("peg_forward") or val.get("peg_trailing")
    g3 = val.get("growth_3y_pct")
    g_fwd = val.get("growth_fwd_pct") or val.get("growth_primary_pct")
    moat_t = rec.get("moat_tier") or assess_moat(rec).get("moat_tier")
    gap = val.get("val_gap")
    fin_t = fin.get("tier")

    if peg is not None and float(peg) < 1.0:
        score += 3
        reasons.append(f"PEG {float(peg):.2f}")
    elif peg is not None and float(peg) < 1.5:
        score += 2
    if g_fwd is not None and float(g_fwd) >= 20:
        score += 2
        reasons.append(f"Forward growth {float(g_fwd):.0f}%")
    elif g3 is not None and float(g3) >= 15:
        score += 1
    if moat_t == "strong":
        score += 2
        reasons.append("Strong moat")
    elif moat_t == "review":
        score += 1
    if gap == "cheap":
        score += 2
        reasons.append("Cheap vs growth")
    if fin_t in ("consistent", "improving", "stable"):
        score += 1
    if val.get("growth_trend") == "accelerating":
        score += 1
        reasons.append("Accelerating quarterly trend")
    if track.get("track_verdict") == "skip":
        score -= 3
    if rec.get("integrity_tier") == "weak":
        score -= 2

    if score >= 6:
        return "High potential", "; ".join(reasons[:5]) or "Multiple quality + growth signals"
    if score >= 4:
        return "Possible", "; ".join(reasons[:5]) or "Some growth + quality signals"
    if score >= 2:
        return "Unlikely", "; ".join(reasons[:4]) or "Needs exceptional execution"
    return "Low", "Rich valuation, weak moat, or skip flags"


def assess_price_action(val: dict | None, tech: dict | None, screener: dict | None) -> tuple[str, str]:
    val = val or {}
    tech = tech or {}
    screener = screener or {}
    vs_high = val.get("vs_high_pct")
    vs_low = val.get("vs_low_pct")
    down_all = screener.get("downalltime")
    ema = tech.get("emaState") or ""
    rsi = tech.get("rsi")
    parts: list[str] = []

    trending = False
    corrected = False

    if vs_high is not None and float(vs_high) > -12:
        trending = True
        parts.append(f"Near 52w high ({float(vs_high):+.0f}%)")
    if down_all is not None and float(down_all) >= 35:
        corrected = True
        parts.append(f"Down {float(down_all):.0f}% from ATH (screener)")
    elif vs_high is not None and float(vs_high) <= -35:
        corrected = True
        parts.append(f"{float(vs_high):.0f}% below 52w high")
    if vs_low is not None and float(vs_low) >= 50 and vs_high is not None and float(vs_high) < -20:
        corrected = True
        parts.append(f"Recovered +{float(vs_low):.0f}% from 52w low")

    if val.get("growth_trend") == "accelerating":
        trending = True
        parts.append("Earnings trend accelerating")
    if ema:
        parts.append(f"EMA stack: {ema}")
    if rsi is not None:
        parts.append(f"RSI {float(rsi):.0f}")

    if trending and not corrected:
        label = "Trending"
    elif corrected and not trending:
        label = "Corrected / beaten down"
    elif trending and corrected:
        label = "Recovering trend"
    else:
        label = "Neutral / range"
    return label, "; ".join(parts[:5])


def assess_fundamental_quality(rec: dict, track: dict) -> str:
    v = track.get("track_verdict")
    grade = rec.get("research_grade")
    if v == "track" and grade in ("A", "B"):
        return "Good"
    if v == "track":
        return "Good / worth tracking"
    if v == "review":
        return "Borderline — manual review"
    if v == "skip":
        return "Weak / avoid"
    return "Unknown"


def assess_garbage(rec: dict, track: dict, fin: dict) -> tuple[str, str]:
    skip = track.get("skip_reasons") or []
    if track.get("track_verdict") == "skip" and len(skip) >= 2:
        return "Likely garbage", "; ".join(skip[:4])
    if rec.get("integrity_tier") == "weak" and (rec.get("profit_ttm_cr") or 0) < 0:
        return "High risk", "Weak integrity + loss-making"
    if fin.get("tier") == "volatile" and rec.get("moat_tier") == "weak":
        return "Speculative", "Lumpy profits + no moat"
    return "No", ""


def assess_downturn(rec: dict, val: dict | None, fin: dict) -> tuple[str, str]:
    val = val or {}
    reasons: list[str] = []
    if val.get("growth_trend") == "decelerating":
        reasons.append("Profit growth decelerating")
    if val.get("yoy_off_peak"):
        reasons.append("Off prior YoY peak")
    if fin.get("tier") in ("slowing", "volatile"):
        reasons.append(fin.get("label", ""))
    g3 = val.get("growth_3y_pct")
    if g3 is not None and float(g3) < 0:
        reasons.append(f"3Y profit growth {float(g3):.0f}%")
    if rec.get("profit_ttm_cr") is not None and float(rec["profit_ttm_cr"]) < 0:
        reasons.append("TTM loss")
    if reasons:
        return "Yes — watch", "; ".join(r for r in reasons if r)[:200]
    return "No clear downturn", ""


def build_merged_record(sym: str, gov: dict, val: dict, concall: dict) -> dict:
    rec = dict(gov.get(sym) or {})
    v = val.get(sym) or {}
    for k, vval in v.items():
        if rec.get(k) is None and vval is not None:
            rec[k] = vval
    c = concall.get(sym) or {}
    for k, cval in c.items():
        if rec.get(k) is None and cval is not None:
            rec[k] = cval
    rec.setdefault("symbol", sym)
    rec.setdefault("name", v.get("name") or sym)
    if rec.get("integrity_score") is None and v.get("quality_score") is not None:
        rec["integrity_score"] = max(0, min(100, int(float(v["quality_score"]) * 2.5)))
        qs = float(v["quality_score"])
        rec["integrity_tier"] = "strong" if qs >= 28 else "review" if qs >= 18 else "weak"
    if rec.get("latest_np_cr") is None:
        rec["latest_np_cr"] = v.get("latest_quarter_np_cr")
    return rec


def parse_input_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    rows: list[dict] = []
    if "\t" in text.splitlines()[0] if text.splitlines() else "":
        reader = csv.DictReader(text.splitlines(), delimiter="\t")
        rows.extend(reader)
    elif "," in text.splitlines()[0] if text.splitlines() else "":
        reader = csv.DictReader(text.splitlines())
        rows.extend(reader)
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append({"Name": line})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Export research sheet CSV for a watchlist")
    ap.add_argument("--file", type=Path, help="Text/CSV file with company names (column Name)")
    ap.add_argument("--symbols", help="Comma-separated NSE symbols")
    ap.add_argument("--out", type=Path, help="Output CSV path")
    args = ap.parse_args()

    if not args.file and not args.symbols:
        ap.error("Provide --file or --symbols")

    gov_payload = _load_json(GOV_PATH)
    val_payload = _load_json(VAL_PATH)
    sector_payload = _load_json(SECTOR_PATH)
    concall_payload = _load_json(CONCALL_PATH)
    map_payload = _load_json(MAP_PATH)
    tech_payload = _load_json(TECH_PATH)

    gov = gov_payload.get("companies") or {}
    val = val_payload.get("companies") or {}
    sector_tags = sector_payload.get("companies") or {}
    concall = concall_payload.get("companies") or {}
    company_map = map_payload.get("companies") or {}
    tech_index = {r["symbol"]: r for r in tech_payload.get("weekly_data") or [] if r.get("symbol")}

    peer_index = build_sector_peer_index(sector_tags, valuation=val, governance=gov)

    input_rows: list[dict] = []
    if args.file:
        input_rows = parse_input_file(args.file)
    else:
        for sym in args.symbols.split(","):
            sym = sym.strip().upper()
            if sym:
                rec = company_map.get(sym) or {}
                input_rows.append({"Name": rec.get("name") or sym, "Symbol": sym})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_path = args.out or OUT_DIR / f"research_sheet_{ts}.csv"

    fieldnames = [
        "S.No.",
        "Input Name",
        "Symbol",
        "Company Name",
        "Sector",
        "Industry",
        "CMP Rs.",
        "Down from ATH %",
        "P/E",
        "Mar Cap Rs.Cr.",
        "Div Yld %",
        "NP Qtr Rs.Cr.",
        "Qtr Profit Var %",
        "Sales Qtr Rs.Cr.",
        "Qtr Sales Var %",
        "ROCE %",
        "CMP / BV",
        "Fundamentally Good?",
        "Research Grade",
        "Research Score",
        "Track Verdict",
        "Track Reason",
        "Moat",
        "Moat Score",
        "Moat Notes",
        "Integrity",
        "Integrity Score",
        "Profit Trend",
        "Sector Wind",
        "Future Growth %",
        "Growth 3Y %",
        "Growth Trend",
        "PEG",
        "Valuation Tag",
        "Multibagger Potential",
        "Multibagger Reason",
        "Defects / Flags",
        "Downturn?",
        "Downturn Reason",
        "Garbage?",
        "Garbage Reason",
        "Capex?",
        "Capex Notes",
        "Concall",
        "Latest Concall",
        "Concall Transcript",
        "Concall / Outlook Notes",
        "Price Action",
        "Price Action Detail",
        "Screener URL",
        "Match Status",
    ]

    output_rows: list[dict] = []
    for i, inp in enumerate(input_rows, 1):
        name_key = inp.get("Name") or inp.get("name") or inp.get("Company") or ""
        sym_hint = (inp.get("Symbol") or inp.get("symbol") or "").strip().upper()
        if sym_hint and sym_hint in company_map:
            sym, resolved_name = sym_hint, company_map[sym_hint].get("name", name_key)
        else:
            sym, resolved_name = resolve_symbol(name_key, company_map)

        row: dict = {k: "" for k in fieldnames}
        row["S.No."] = i
        row["Input Name"] = name_key
        screener_cols = {
            "CMP Rs.": "CMP Rs.",
            "downalltime": "Down from ATH %",
            "P/E": "P/E",
            "Mar Cap Rs.Cr. ": "Mar Cap Rs.Cr.",
            "Mar Cap Rs.Cr.": "Mar Cap Rs.Cr.",
            "Div Yld %": "Div Yld %",
            "NP Qtr Rs.Cr.": "NP Qtr Rs.Cr.",
            "Qtr Profit Var %": "Qtr Profit Var %",
            "Sales Qtr Rs.Cr.": "Sales Qtr Rs.Cr.",
            "Qtr Sales Var %": "Qtr Sales Var %",
            "ROCE %": "ROCE %",
            "CMP / BV": "CMP / BV",
        }
        for src, dst in screener_cols.items():
            if inp.get(src) not in (None, ""):
                row[dst] = inp[src]

        if not sym:
            row["Match Status"] = "NOT FOUND — check symbol manually"
            row["Company Name"] = resolved_name
            output_rows.append(row)
            continue

        merged = build_merged_record(sym, gov, val, concall)
        v = val.get(sym) or {}
        tag = sector_tags.get(sym) or sector_tags.get(str(sym).upper())
        enrich_governance_record(merged, val=v, sector_tag=tag)
        attach_sector_context(merged, tag, peer_index)
        track = assess_track_verdict(merged, val=v, sector_ctx=merged.get("sector"))
        merged.update(track)
        merged.update(compute_research_score(merged, val=v))
        fin = assess_financial_consistency(merged, v)
        capex_label, capex_notes = infer_capex(merged, v)
        mb_label, mb_reason = assess_multibagger(merged, v, fin, track)
        price_label, price_detail = assess_price_action(v, tech_index.get(sym), inp)
        garbage, garbage_r = assess_garbage(merged, track, fin)
        downturn, downturn_r = assess_downturn(merged, v, fin)

        flags = list(merged.get("flags") or [])
        skip = track.get("skip_reasons") or []
        defects = "; ".join([*flags[:4], *skip[:3]])[:400]

        has_concall = merged.get("has_concall")
        if has_concall is True:
            concall_txt = "Yes"
        elif has_concall is False:
            concall_txt = "No"
        else:
            concall_txt = "Not checked"

        outlook = merged.get("management_outlook") or v.get("management_outlook") or ""
        if not outlook:
            for line in v.get("val_rationale") or []:
                if "concall" in str(line).lower():
                    outlook = str(line)
                    break

        rescue = track.get("rescue_reasons") or []
        skip_r = track.get("skip_reasons") or []
        track_reason = "; ".join(rescue[:2] if track.get("track_verdict") == "track" else skip_r[:3] or rescue[:2])

        sec = merged.get("sector") or {}
        row.update({
            "Symbol": sym,
            "Company Name": merged.get("name") or resolved_name,
            "Sector": sec.get("broad_sector") or sec.get("sector") or "",
            "Industry": sec.get("sub_tag") or sec.get("industry") or "",
            "CMP Rs.": row["CMP Rs."] or v.get("ltp") or "",
            "Down from ATH %": row["Down from ATH %"] or (f"{-float(v['vs_high_pct']):.1f}" if v.get("vs_high_pct") is not None else ""),
            "P/E": row["P/E"] or v.get("pe_ttm") or merged.get("pe_ttm") or "",
            "Mar Cap Rs.Cr.": row["Mar Cap Rs.Cr."] or v.get("market_cap_cr") or merged.get("market_cap_cr") or "",
            "ROCE %": row["ROCE %"] or merged.get("roce_pct") or v.get("roce_pct") or "",
            "Fundamentally Good?": assess_fundamental_quality(merged, track),
            "Research Grade": merged.get("research_grade") or "",
            "Research Score": merged.get("research_score") or "",
            "Track Verdict": track.get("track_verdict") or "",
            "Track Reason": track_reason,
            "Moat": merged.get("moat_tier") or "",
            "Moat Score": merged.get("moat_score") or "",
            "Moat Notes": "; ".join(merged.get("moat_notes") or [])[:300],
            "Integrity": merged.get("integrity_tier") or "",
            "Integrity Score": merged.get("integrity_score") or "",
            "Profit Trend": fin.get("label") or "",
            "Sector Wind": sec.get("sector_wind_label") or "",
            "Future Growth %": v.get("growth_fwd_pct") or v.get("growth_primary_pct") or "",
            "Growth 3Y %": v.get("growth_3y_pct") or "",
            "Growth Trend": v.get("growth_trend_display") or v.get("growth_trend") or "",
            "PEG": v.get("peg_forward") or v.get("peg_trailing") or "",
            "Valuation Tag": v.get("val_gap") or "",
            "Multibagger Potential": mb_label,
            "Multibagger Reason": mb_reason,
            "Defects / Flags": defects,
            "Downturn?": downturn,
            "Downturn Reason": downturn_r,
            "Garbage?": garbage,
            "Garbage Reason": garbage_r,
            "Capex?": capex_label,
            "Capex Notes": capex_notes[:300],
            "Concall": concall_txt,
            "Latest Concall": merged.get("latest_concall_period") or "",
            "Concall Transcript": "Yes" if merged.get("latest_concall_has_transcript") else ("No" if has_concall else ""),
            "Concall / Outlook Notes": outlook[:400],
            "Price Action": price_label,
            "Price Action Detail": price_detail,
            "Screener URL": merged.get("screener_url") or v.get("screener_url") or "",
            "Match Status": "OK",
        })
        output_rows.append(row)

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    ok = sum(1 for r in output_rows if r.get("Match Status") == "OK")
    print(f"Wrote {len(output_rows)} rows ({ok} matched) → {out_path}")
    missing = [r["Input Name"] for r in output_rows if r.get("Match Status") != "OK"]
    if missing:
        print("Unmatched:", ", ".join(missing), file=sys.stderr)
    return 0 if ok == len(output_rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
