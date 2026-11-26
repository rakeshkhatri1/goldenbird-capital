#!/usr/bin/env python3
"""
On-demand valuation v2 — Screener.in consolidated fundamentals + rule engine.

Reads:  data/screener_company_map.json, nse_market_data.json (LTP)
Writes: data/valuation.json

Per symbol (Screener consolidated, fallback standalone):
  - Trailing P/E, forward P/E (price / forward EPS estimate)
  - Profit growth: TTM, 3Y, latest quarter YoY, forward FY estimate YoY
  - Growth trend: accelerating | decelerating | stable
  - PEG trailing & forward (with low-base / extreme-TTM safeguards)
  - val_gap tag + detailed val_rationale bullets for UI

Optional Gemini (VALUATION_GEMINI=1): management-style outlook paragraph from facts only.

Usage:
  python3 build_valuation.py
  VALUATION_SYMBOLS=CGCL,EFCIL python3 build_valuation.py
  VALUATION_MAX=50 VALUATION_RESUME=0 python3 build_valuation.py
  VALUATION_GEMINI=1 VALUATION_SYMBOLS=CGCL python3 build_valuation.py
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

from screener_parse import (
    fetch_html,
    fetch_screener_for_rec,
    fetch_screener_pages,
    parse_number,
    parse_pct,
    parse_concalls,
    parse_results_table,
    parse_top_ratios,
    section_slice,
    strip_html,
)
from screener_utils import screener_slug, screener_slug_candidates
from universe_filter import MIN_MCAP_CR, filter_targets

ROOT = Path(__file__).resolve().parent
MAP_PATH = Path(os.environ.get("SCREENER_MAP_PATH", ROOT / "data" / "screener_company_map.json"))
OUT_PATH = Path(os.environ.get("VALUATION_PATH", ROOT / "data" / "valuation.json"))
MARKET_DATA_PATH = Path(os.environ.get("MARKET_DATA_PATH", ROOT / "nse_market_data.json"))
SLEEP = float(os.environ.get("VALUATION_SLEEP", "2.0"))
MAX_SYMBOLS = os.environ.get("VALUATION_MAX", "").strip()
SYMBOL_FILTER = os.environ.get("VALUATION_SYMBOLS", "").strip()
RESUME = os.environ.get("VALUATION_RESUME", "1").strip().lower() in ("1", "true", "yes", "on")
USE_GEMINI = os.environ.get("VALUATION_GEMINI", "0").strip().lower() in ("1", "true", "yes", "on")
EXTREME_TTM_GROWTH_CAP = float(os.environ.get("VALUATION_TTM_GROWTH_CAP", "150"))
USER_AGENT = os.environ.get(
    "VALUATION_USER_AGENT",
    "Mozilla/5.0 (compatible; nse_engine/2.0; +valuation scan)",
)

JSON_VERSION = 2

PROFIT_GROWTH_BLOCK_RE = re.compile(
    r"Compounded Profit Growth</th>(.*?)</table>",
    re.DOTALL | re.I,
)
GROWTH_ROW_RE = re.compile(
    r"<tr>\s*<td>\s*([^<]+?)\s*</td>\s*<td>\s*([^<]+?)\s*</td>\s*</tr>",
    re.DOTALL | re.I,
)


def _fetch_html(url: str) -> str:
    return fetch_html(url, user_agent=USER_AGENT)


def _fetch_screener_pages(slug: str) -> tuple[str, str, str]:
    return fetch_screener_pages(slug, user_agent=USER_AGENT)


def _fetch_screener_pages_for_rec(rec: dict) -> tuple[str, str, str, str]:
    return fetch_screener_for_rec(rec, user_agent=USER_AGENT)


def _strip_html(fragment: str) -> str:
    return strip_html(fragment)


def _parse_pct(raw: str) -> float | None:
    return parse_pct(raw)


def _parse_number(raw: str) -> float | None:
    return parse_number(raw)


def _parse_top_ratios(page_html: str) -> dict[str, float | None]:
    return parse_top_ratios(page_html)


def _parse_compounded_profit_growth(page_html: str) -> dict[str, float | None]:
    m = PROFIT_GROWTH_BLOCK_RE.search(page_html)
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, float | None] = {}
    key_map = {
        "ttm": "growth_ttm_pct",
        "3 years": "growth_3y_pct",
        "5 years": "growth_5y_pct",
        "10 years": "growth_10y_pct",
    }
    for row_m in GROWTH_ROW_RE.finditer(block):
        label = _strip_html(row_m.group(1)).lower().rstrip(":")
        val = _parse_pct(row_m.group(2))
        key = key_map.get(label)
        if key and val is not None:
            out[key] = val
    return out


def _section_slice(page_html: str, section_id: str) -> str:
    return section_slice(page_html, section_id)


def _parse_results_table(section_html: str) -> tuple[list[str], dict[str, list[float | None]]]:
    return parse_results_table(section_html)


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1.0) * 100.0, 1)


def _quarter_short(date_str: str) -> str:
    if len(date_str) >= 7:
        month = date_str[5:7]
        labels = {"03": "Mar", "06": "Jun", "09": "Sep", "12": "Dec"}
        if month in labels:
            return f"{labels[month]}'{date_str[2:4]}"
    return date_str[:7] if date_str else ""


YOy_PEAK_LOOKBACK = 6
YOy_OFF_PEAK_PP = 15.0


def _quarterly_analysis(dates: list[str], net_profit: list[float | None]) -> dict:
    out: dict = {
        "growth_quarterly_yoy_pct": None,
        "latest_quarter": None,
        "latest_quarter_np_cr": None,
        "growth_trend": "unknown",
        "quarterly_yoy_history": [],
        "yoy_peak_pct": None,
        "yoy_peak_quarter": None,
        "yoy_vs_peak_pp": None,
        "yoy_off_peak": False,
        "growth_trend_display": None,
    }
    if not dates or not net_profit or len(dates) != len(net_profit):
        return out

    series = list(zip(dates, net_profit))
    yoy_points: list[tuple[str, float]] = []
    for i, (d, v) in enumerate(series):
        if v is None or i < 4:
            continue
        prev = net_profit[i - 4]
        ch = _pct_change(v, prev)
        if ch is not None:
            yoy_points.append((d, ch))

    out["quarterly_yoy_history"] = [{"date": d, "yoy_pct": p} for d, p in yoy_points[-6:]]

    if yoy_points:
        out["growth_quarterly_yoy_pct"] = yoy_points[-1][1]
        out["latest_quarter"] = yoy_points[-1][0]
        idx = dates.index(yoy_points[-1][0])
        out["latest_quarter_np_cr"] = net_profit[idx]

    if len(yoy_points) >= 2:
        last_two = [p for _, p in yoy_points[-2:]]
        delta = last_two[1] - last_two[0]
        if delta >= 8:
            out["growth_trend"] = "accelerating"
        elif delta <= -8:
            out["growth_trend"] = "decelerating"
        else:
            out["growth_trend"] = "stable"

    recent = yoy_points[-YOy_PEAK_LOOKBACK:]
    if len(recent) >= 3 and yoy_points:
        peak_val = max(p for _, p in recent)
        peak_date = next(d for d, p in reversed(recent) if p == peak_val)
        latest_val = recent[-1][1]
        latest_date = recent[-1][0]
        vs_peak = round(latest_val - peak_val, 1)
        out["yoy_peak_pct"] = peak_val
        out["yoy_peak_quarter"] = peak_date
        out["yoy_vs_peak_pp"] = vs_peak
        at_peak = peak_date == latest_date or abs(vs_peak) < 1.0
        if not at_peak and vs_peak <= -YOy_OFF_PEAK_PP:
            out["yoy_off_peak"] = True

    trend = out["growth_trend"]
    if out["yoy_off_peak"] and out["yoy_vs_peak_pp"] is not None:
        pk = _quarter_short(out.get("yoy_peak_quarter") or "")
        pp = out["yoy_vs_peak_pp"]
        base = {"accelerating": "Accelerating", "decelerating": "Slowing", "stable": "Stable"}.get(
            trend, "Unknown"
        )
        out["growth_trend_display"] = f"{base} · {pp:+.0f}pp vs peak ({pk})"
    elif trend and trend != "unknown":
        out["growth_trend_display"] = {
            "accelerating": "Accelerating",
            "decelerating": "Slowing (last 2 Q)",
            "stable": "Stable (last 2 Q)",
        }.get(trend)

    return out


def _annual_forward_metrics(
    dates: list[str],
    net_profit: list[float | None],
    eps: list[float | None],
    ltp: float | None,
) -> dict:
    out: dict = {
        "growth_fwd_pct": None,
        "forward_fy_label": None,
        "forward_np_cr": None,
        "forward_eps": None,
        "pe_fwd": None,
        "latest_reported_fy_np_cr": None,
    }
    if not dates or not net_profit:
        return out

    pairs = [(d, net_profit[i], eps[i] if eps and i < len(eps) else None) for i, d in enumerate(dates)]
    numeric = [(d, npv, ep) for d, npv, ep in pairs if npv is not None]

    if len(numeric) >= 2:
        out["latest_reported_fy_np_cr"] = numeric[-2][1]

    if len(numeric) >= 2:
        prev_d, prev_np, _ = numeric[-2]
        last_d, last_np, last_eps = numeric[-1]
        # Screener often marks next FY as estimate when date is ahead of last reported
        fwd_g = _pct_change(last_np, prev_np)
        if fwd_g is not None and last_d > prev_d:
            out["growth_fwd_pct"] = fwd_g
            out["forward_fy_label"] = last_d[:4]
            out["forward_np_cr"] = last_np
            if last_eps and last_eps > 0:
                out["forward_eps"] = last_eps
                if ltp:
                    out["pe_fwd"] = round(ltp / last_eps, 1)

    return out


def _pick_growth_for_peg(
    growth_ttm: float | None,
    growth_3y: float | None,
    growth_fwd: float | None,
    growth_q_yoy: float | None,
    latest_reported_np: float | None,
) -> tuple[float | None, str, list[str]]:
    warnings: list[str] = []
    low_base = latest_reported_np is not None and latest_reported_np < 15
    extreme_ttm = growth_ttm is not None and growth_ttm > EXTREME_TTM_GROWTH_CAP

    if extreme_ttm or low_base:
        if extreme_ttm:
            warnings.append(
                f"TTM profit growth {growth_ttm:.0f}% is extreme (> {EXTREME_TTM_GROWTH_CAP:.0f}%) — "
                "often low-base distortion; forward/3Y growth weighted more for PEG."
            )
        if low_base:
            warnings.append(
                f"Latest reported annual profit is only ₹{latest_reported_np:.0f} Cr — "
                "high % growth may not be sustainable."
            )
        for candidate, label in (
            (growth_fwd, "forward_fy_estimate"),
            (growth_3y, "profit_3y_cagr"),
            (growth_q_yoy, "latest_quarter_yoy"),
            (growth_ttm, "profit_ttm"),
        ):
            if candidate is not None and 5 <= candidate <= EXTREME_TTM_GROWTH_CAP:
                return candidate, label, warnings
        if growth_fwd is not None:
            return growth_fwd, "forward_fy_estimate", warnings
        if growth_3y is not None:
            return growth_3y, "profit_3y_cagr", warnings

    if growth_ttm is not None:
        return growth_ttm, "profit_ttm", warnings
    if growth_3y is not None:
        return growth_3y, "profit_3y_cagr", warnings
    if growth_fwd is not None:
        return growth_fwd, "forward_fy_estimate", warnings
    return growth_q_yoy, "latest_quarter_yoy", warnings


def _load_ltp_index() -> dict[str, float]:
    if not MARKET_DATA_PATH.is_file():
        return {}
    try:
        data = json.loads(MARKET_DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for key in ("weekly_data", "monthly_data"):
        for row in data.get(key) or []:
            sym = (row.get("symbol") or "").strip().upper()
            ltp = row.get("ltp")
            if sym and ltp is not None:
                out[sym] = float(ltp)
    return out


def _pct_distance(price: float | None, ref: float | None) -> float | None:
    if price is None or ref is None or ref == 0:
        return None
    return round((price / ref - 1.0) * 100.0, 2)


def _classify_valuation(
    *,
    pe_ttm: float | None,
    pe_fwd: float | None,
    peg_trailing: float | None,
    peg_forward: float | None,
    peg_primary: float | None,
    growth_primary: float | None,
    vs_high_pct: float | None,
    vs_low_pct: float | None,
    roce_pct: float | None,
    growth_trend: str,
) -> dict:
    gap_score = 0.0
    drivers: list[str] = []
    confidence = "low"

    peg = peg_primary or peg_forward or peg_trailing
    pe = pe_fwd or pe_ttm

    if peg is not None and peg > 0:
        gap_score += (1.25 - peg) * 28.0
        confidence = "med"
        if peg <= 0.8:
            drivers.append("growth_gap")
        elif peg >= 2.0:
            drivers.append("multiple_stretch")

    if pe is not None and growth_primary is not None and growth_primary > 0:
        fair_pe = growth_primary * 0.85
        gap_score += (fair_pe - pe) * 0.6
        confidence = "med"
        if pe > fair_pe * 1.35:
            drivers.append("multiple_stretch")
        elif pe < fair_pe * 0.75:
            drivers.append("growth_gap")

    if pe_fwd and pe_ttm and pe_fwd < pe_ttm * 0.85 and growth_primary and growth_primary > 10:
        gap_score += 8
        drivers.append("forward_discount")

    if vs_high_pct is not None:
        if vs_high_pct <= -20:
            gap_score += 12
            drivers.append("event_selloff")
        elif vs_high_pct >= -3 and peg and peg > 1.4:
            gap_score -= 10
            drivers.append("momentum_premium")

    if vs_low_pct is not None and vs_low_pct <= 25 and growth_primary and growth_primary >= 15:
        gap_score += 5
        if "cycle_low" not in drivers:
            drivers.append("cycle_low")

    if growth_trend == "accelerating" and peg and peg < 1.5:
        gap_score += 4
    elif growth_trend == "decelerating" and peg and peg > 1.2:
        gap_score -= 6
        drivers.append("growth_deceleration")

    if roce_pct is not None and roce_pct >= 18 and peg and peg < 1.2:
        gap_score += 3

    gap_score = round(max(-100.0, min(100.0, gap_score)), 1)

    if pe_ttm is None and peg is None:
        val_gap = "unknown"
    elif gap_score >= 12:
        val_gap = "cheap"
    elif gap_score <= -12:
        val_gap = "rich"
    else:
        val_gap = "fair"

    size = "mild"
    if abs(gap_score) >= 30:
        size = "deep"
    elif abs(gap_score) >= 15:
        size = "moderate"

    if pe_ttm and growth_primary and roce_pct:
        confidence = "high"
    elif confidence == "low" and (pe_ttm or growth_primary):
        confidence = "med"

    driver = drivers[0] if drivers else ("unknown" if val_gap == "unknown" else "mixed")

    return {
        "gap_score": gap_score,
        "val_gap": val_gap,
        "val_gap_size": size,
        "val_driver": driver,
        "val_confidence": confidence,
    }


def _build_rationale(rec: dict) -> list[str]:
    lines: list[str] = []
    sym = rec.get("symbol") or "Stock"
    basis = rec.get("data_basis") or "screener"
    gap = rec.get("val_gap", "unknown")
    size = rec.get("val_gap_size", "")

    lines.append(
        f"Rating: {gap.upper()}"
        + (f" ({size})" if size and size != "mild" else "")
        + f" — score {rec.get('gap_score', '—')} | driver: {rec.get('val_driver', 'mixed')} | "
        f"confidence: {rec.get('val_confidence', 'low')}."
    )

    if rec.get("ltp") is not None:
        lines.append(f"Price ₹{rec['ltp']:.2f} (pipeline LTP); Screener basis: {basis}.")

    pe_ttm = rec.get("pe_ttm")
    pe_fwd = rec.get("pe_fwd")
    if pe_ttm is not None:
        lines.append(f"Trailing P/E (Screener Stock P/E): {pe_ttm:.1f}.")
    if pe_fwd is not None:
        fy = rec.get("forward_fy_label") or "next FY"
        eps = rec.get("forward_eps")
        lines.append(
            f"Forward P/E ≈ {pe_fwd:.1f} (price ÷ Screener estimate EPS {eps:.2f} for FY {fy})."
        )
    elif rec.get("forward_eps") is None:
        lines.append("Forward P/E: not available (no Screener forward EPS estimate on page).")

    g_ttm = rec.get("growth_ttm_pct")
    g_3y = rec.get("growth_3y_pct")
    g_fwd = rec.get("growth_fwd_pct")
    g_q = rec.get("growth_quarterly_yoy_pct")
    if g_ttm is not None:
        lines.append(
            f"Compounded profit growth TTM (Screener card below P&L): {g_ttm:.0f}%."
        )
    if g_3y is not None:
        lines.append(f"Compounded profit growth 3Y: {g_3y:.0f}%.")
    if g_fwd is not None:
        lines.append(
            f"Forward profit growth (estimate FY vs prior FY): {g_fwd:.0f}%."
        )
    if g_q is not None:
        qd = rec.get("latest_quarter") or "latest quarter"
        lines.append(f"Latest quarter YoY profit growth ({qd}): {g_q:.1f}%.")

    trend = rec.get("growth_trend")
    if trend and trend != "unknown":
        disp = rec.get("growth_trend_display")
        if disp:
            lines.append(f"Quarterly profit trend: {disp}.")
        else:
            lines.append(f"Quarterly profit trend: {trend} (comparing recent quarter YoY rates).")
    if rec.get("yoy_off_peak") and rec.get("yoy_peak_pct") is not None:
        pk = _quarter_short(rec.get("yoy_peak_quarter") or "")
        lines.append(
            f"YoY growth cooled from recent peak {rec['yoy_peak_pct']:.0f}% ({pk}) "
            f"to latest {rec.get('growth_quarterly_yoy_pct', '—')}% "
            f"({rec.get('yoy_vs_peak_pp', 0):+.0f} pp vs peak)."
        )

    peg_t = rec.get("peg_trailing")
    peg_f = rec.get("peg_forward")
    peg_p = rec.get("peg_primary")
    peg_label = rec.get("peg_primary_label") or ""
    if peg_t is not None:
        lines.append(f"PEG trailing (P/E ÷ TTM growth): {peg_t:.2f}.")
    if peg_f is not None:
        lines.append(f"PEG forward (forward P/E ÷ forward growth): {peg_f:.2f}.")
    if peg_p is not None and peg_label:
        lines.append(f"Primary PEG used for score: {peg_p:.2f} ({peg_label.replace('_', ' ')}).")

    if rec.get("vs_high_pct") is not None:
        lines.append(f"Price vs 52-week high: {rec['vs_high_pct']:+.1f}%.")
    if rec.get("vs_low_pct") is not None:
        lines.append(f"Price vs 52-week low: {rec['vs_low_pct']:+.1f}%.")

    if pe_ttm and g_ttm and peg_t and peg_t < 0.9:
        lines.append(
            "Cheap signal: trailing multiple looks low versus Screener TTM profit growth (PEG < 0.9)."
        )
    if pe_fwd and g_fwd and peg_f and peg_f < 1.0:
        lines.append(
            "Forward cheap signal: forward P/E vs Screener estimate profit growth suggests room (PEG < 1)."
        )
    if gap == "rich":
        lines.append("Rich signal: market multiple appears ahead of visible profit growth.")
    if gap == "fair":
        lines.append("Fair signal: price/multiple broadly aligned with Screener growth metrics.")

    for w in rec.get("warnings") or []:
        lines.append(f"⚠ {w}")

    lines.append(
        "Management concall: not scraped automatically — use VALUATION_GEMINI=1 for AI outlook "
        "from these facts, or review Screener documents / latest concall manually."
    )

    return lines


def _template_narrative(rec: dict) -> str:
    parts = []
    if rec.get("ltp") is not None:
        parts.append(f"{rec.get('symbol')} at ₹{rec['ltp']:.0f}.")
    if rec.get("pe_ttm") is not None:
        parts.append(f"Trailing P/E {rec['pe_ttm']:.1f}.")
    if rec.get("pe_fwd") is not None:
        parts.append(f"Forward P/E ~{rec['pe_fwd']:.1f}.")
    g = rec.get("growth_primary_pct")
    if g is not None:
        parts.append(f"Primary growth {g:.0f}% ({rec.get('peg_primary_label', '').replace('_', ' ')}).")
    if rec.get("peg_primary") is not None:
        parts.append(f"PEG {rec['peg_primary']:.2f}.")
    if rec.get("growth_trend") not in (None, "unknown"):
        parts.append(f"Quarterly trend: {rec['growth_trend']}.")
    gap = rec.get("val_gap")
    if gap == "cheap":
        parts.append("Tag: potential discount vs growth (rule-based, Screener data).")
    elif gap == "rich":
        parts.append("Tag: premium vs visible growth.")
    elif gap == "fair":
        parts.append("Tag: broadly fair vs Screener growth.")
    return " ".join(parts)


def _gemini_outlook(rec: dict) -> str:
    from gemini_client import GeminiClient, GeminiError

    facts = {k: v for k, v in rec.items() if k not in ("narrative", "management_outlook", "val_rationale")}
    prompt = (
        "Indian equity valuation memo. Use ONLY the JSON facts (Screener consolidated/standalone scrape). "
        "Do not invent numbers. Structure:\n"
        "1) Current valuation (trailing & forward P/E, PEG)\n"
        "2) Growth quality (TTM, 3Y, quarterly YoY, accelerating/decelerating)\n"
        "3) Why the val_gap tag is justified\n"
        "4) Management / forward outlook — if concall not in data, state what growth Screener "
        "estimates imply and one key risk (base effect, cyclicality, etc.)\n\n"
        f"{json.dumps(facts, indent=2, default=str)}"
    )
    system = "Equity research assistant. Plain text, 5-7 sentences, no markdown."
    try:
        client = GeminiClient()
        return client.generate(prompt, system=system, temperature=0.25, max_output_tokens=700)
    except GeminiError:
        return _template_narrative(rec)


def build_record(
    kite: str,
    rec: dict,
    slug: str,
    page_html: str,
    data_basis: str,
    source_url: str,
    ltp_index: dict[str, float],
) -> dict:
    ratios = _parse_top_ratios(page_html)
    compounded = _parse_compounded_profit_growth(page_html)

    q_dates, q_rows = _parse_results_table(_section_slice(page_html, "quarters"))
    q_np = q_rows.get("Net Profit", [])
    q_analysis = _quarterly_analysis(q_dates, q_np)

    pl_dates, pl_rows = _parse_results_table(_section_slice(page_html, "profit-loss"))
    ann_np = pl_rows.get("Net Profit", [])
    ann_eps = pl_rows.get("EPS in Rs", [])
    if not ann_np and q_np and len(q_np) >= 4:
        ann_np, ann_eps = q_np, pl_rows.get("EPS in Rs", q_rows.get("EPS in Rs", []))

    ltp = ltp_index.get(kite) or ratios.get("screener_price")
    fwd = _annual_forward_metrics(pl_dates or q_dates, ann_np, ann_eps, ltp)

    growth_ttm = compounded.get("growth_ttm_pct")
    growth_3y = compounded.get("growth_3y_pct")
    growth_fwd = fwd.get("growth_fwd_pct")
    growth_q_yoy = q_analysis.get("growth_quarterly_yoy_pct")

    warnings: list[str] = []
    growth_primary, peg_label, w2 = _pick_growth_for_peg(
        growth_ttm,
        growth_3y,
        growth_fwd,
        growth_q_yoy,
        fwd.get("latest_reported_fy_np_cr"),
    )
    warnings.extend(w2)
    if growth_ttm and growth_3y and growth_ttm > max(50, growth_3y * 2.5):
        warnings.append(
            f"TTM profit growth {growth_ttm:.0f}% is much higher than 3Y CAGR {growth_3y:.0f}% "
            "— discount tag may rely on a temporary surge."
        )
    if q_analysis.get("yoy_off_peak"):
        warnings.append(
            "Quarterly YoY profit growth is below its recent peak — growth may be slowly normalizing."
        )

    pe_ttm = ratios.get("pe_ttm")
    pe_fwd = fwd.get("pe_fwd")
    peg_trailing = round(pe_ttm / growth_ttm, 2) if pe_ttm and growth_ttm and growth_ttm > 0 else None
    peg_forward = (
        round(pe_fwd / growth_fwd, 2) if pe_fwd and growth_fwd and growth_fwd > 0 else None
    )
    peg_primary = None
    if peg_label == "profit_ttm" and peg_trailing is not None:
        peg_primary = peg_trailing
    elif peg_label == "forward_fy_estimate" and peg_forward is not None:
        peg_primary = peg_forward
    elif growth_primary and growth_primary > 0:
        pe_use = pe_fwd or pe_ttm
        if pe_use:
            peg_primary = round(pe_use / growth_primary, 2)

    high = ratios.get("high_52w")
    low = ratios.get("low_52w")
    vs_high = _pct_distance(ltp, high)
    vs_low = _pct_distance(ltp, low)

    roce = ratios.get("roce_pct")
    roe = ratios.get("roe_pct")
    quality_score = (
        round(min(100.0, (roce + roe) / 2.0 * 2.5), 1) if roce is not None and roe is not None else None
    )

    classified = _classify_valuation(
        pe_ttm=pe_ttm,
        pe_fwd=pe_fwd,
        peg_trailing=peg_trailing,
        peg_forward=peg_forward,
        peg_primary=peg_primary,
        growth_primary=growth_primary,
        vs_high_pct=vs_high,
        vs_low_pct=vs_low,
        roce_pct=roce,
        growth_trend=q_analysis.get("growth_trend") or "unknown",
    )

    row: dict = {
        "symbol": kite,
        "name": rec.get("name") or kite,
        "screener_slug": slug,
        "screener_url": source_url,
        "data_basis": data_basis,
        **parse_concalls(page_html),
        "ltp": ltp,
        "market_cap_cr": ratios.get("market_cap_cr"),
        "pe_ttm": pe_ttm,
        "pe_fwd": pe_fwd,
        "forward_eps": fwd.get("forward_eps"),
        "forward_fy_label": fwd.get("forward_fy_label"),
        "forward_np_cr": fwd.get("forward_np_cr"),
        "growth_ttm_pct": growth_ttm,
        "growth_3y_pct": growth_3y,
        "growth_5y_pct": compounded.get("growth_5y_pct"),
        "growth_fwd_pct": growth_fwd,
        "growth_quarterly_yoy_pct": growth_q_yoy,
        "latest_quarter": q_analysis.get("latest_quarter"),
        "latest_quarter_np_cr": q_analysis.get("latest_quarter_np_cr"),
        "growth_trend": q_analysis.get("growth_trend"),
        "growth_trend_display": q_analysis.get("growth_trend_display"),
        "yoy_peak_pct": q_analysis.get("yoy_peak_pct"),
        "yoy_peak_quarter": q_analysis.get("yoy_peak_quarter"),
        "yoy_vs_peak_pp": q_analysis.get("yoy_vs_peak_pp"),
        "yoy_off_peak": q_analysis.get("yoy_off_peak"),
        "quarterly_yoy_history": q_analysis.get("quarterly_yoy_history"),
        "growth_primary_pct": growth_primary,
        "peg_primary_label": peg_label,
        "peg_trailing": peg_trailing,
        "peg_forward": peg_forward,
        "peg_primary": peg_primary,
        "peg": peg_primary,
        "growth_pct": growth_primary,
        "growth_horizon": peg_label,
        "roce_pct": roce,
        "roe_pct": roe,
        "quality_score": quality_score,
        "high_52w": high,
        "low_52w": low,
        "vs_high_pct": vs_high,
        "vs_low_pct": vs_low,
        "warnings": warnings,
        **classified,
        "analyzed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    row["val_rationale"] = _build_rationale(row)
    row["management_outlook"] = _gemini_outlook(row) if USE_GEMINI else None
    row["narrative"] = row["management_outlook"] or _template_narrative(row)
    return row


def _load_existing() -> dict:
    if not OUT_PATH.is_file():
        return {"version": JSON_VERSION, "companies": {}, "failures": []}
    try:
        data = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": JSON_VERSION, "companies": {}, "failures": []}
    data.setdefault("companies", {})
    data.setdefault("failures", [])
    return data


def _should_skip_resume(kite: str, valued: dict) -> bool:
    row = valued.get(kite)
    if not row:
        return False
    if row.get("version") != JSON_VERSION:
        return False
    return bool(row.get("val_gap"))


def _write_output(
    companies: dict,
    failures: list,
    ok: int,
    skipped: int,
    err: int,
    t0: float,
    *,
    partial: bool,
) -> None:
    cheap = sum(1 for r in companies.values() if r.get("val_gap") == "cheap")
    rich = sum(1 for r in companies.values() if r.get("val_gap") == "rich")
    out = {
        "version": JSON_VERSION,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "screener.in consolidated (fallback standalone) + rule engine v2"
        + (" + gemini outlook" if USE_GEMINI else ""),
        "stats": {
            "valued": len(companies),
            "cheap": cheap,
            "rich": rich,
            "processed_this_run": ok,
            "skipped_resume": skipped,
            "failures": len(failures),
            "elapsed_sec": round(time.time() - t0, 1),
            "partial": partial,
        },
        "companies": dict(sorted(companies.items())),
        "failures": failures,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not partial:
        print()
        print("=== Valuation v2 ===")
        print(f"Valued:  {len(companies):,} (cheap {cheap:,} | rich {rich:,})")
        print(f"This run: OK {ok:,} | skipped {skipped:,} | errors {err:,}")
        print(f"Wrote:   {OUT_PATH}")


def main() -> None:
    if not MAP_PATH.is_file():
        print(f"Missing {MAP_PATH}. Run build_screener_map.py first.", file=sys.stderr)
        sys.exit(1)

    company_map = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    companies_in = company_map.get("companies") or {}
    existing = _load_existing()
    valued: dict = dict(existing.get("companies") or {})
    failures: list = [f for f in (existing.get("failures") or []) if f.get("kite_symbol") not in valued]
    ltp_index = _load_ltp_index()

    filter_set = {s.strip().upper() for s in SYMBOL_FILTER.split(",") if s.strip()}
    targets = sorted(companies_in.values(), key=lambda r: (r.get("kite_symbol") or ""))
    if not filter_set:
        targets = filter_targets(targets, eligible_only=True)
    else:
        targets = filter_targets(targets, eligible_only=False, symbol_filter=filter_set)
    if MAX_SYMBOLS.isdigit():
        targets = targets[: int(MAX_SYMBOLS)]

    ok = skipped = err = 0
    t0 = time.time()

    for i, rec in enumerate(targets, 1):
        kite = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
        if not kite:
            continue
        if RESUME and _should_skip_resume(kite, valued):
            skipped += 1
            continue

        slug = screener_slug(rec)
        if not slug:
            failures.append({"kite_symbol": kite, "reason": "no_screener_slug"})
            err += 1
            continue

        try:
            page_html, data_basis, source_url, slug_used = _fetch_screener_pages_for_rec(rec)
            row = build_record(kite, rec, slug_used, page_html, data_basis, source_url, ltp_index)
            row["version"] = JSON_VERSION
            mcap = row.get("market_cap_cr")
            if mcap is not None and mcap < MIN_MCAP_CR:
                skipped += 1
                print(f"[{i}/{len(targets)}] {kite}: skip mcap ₹{mcap:.0f} Cr < ₹{MIN_MCAP_CR:.0f} Cr", flush=True)
                continue
            valued[kite] = row
            ok += 1
            failures = [f for f in failures if f.get("kite_symbol") != kite]
            print(
                f"[{i}/{len(targets)}] {kite} [{data_basis}]: {row['val_gap']} "
                f"score={row['gap_score']} P/E={row.get('pe_ttm')} fwd={row.get('pe_fwd')} "
                f"PEG={row.get('peg_primary')} trend={row.get('growth_trend')}"
            )
            if ok % 25 == 0:
                _write_output(valued, failures, ok, skipped, err, t0, partial=True)
        except urllib.error.HTTPError as e:
            failures.append({"kite_symbol": kite, "screener_slug": slug, "reason": f"http_{e.code}"})
            err += 1
            print(f"HTTP {e.code} for {kite}", file=sys.stderr)
        except Exception as e:
            failures.append({"kite_symbol": kite, "screener_slug": slug, "reason": str(e)[:120]})
            err += 1
            print(f"Error {kite}: {e}", file=sys.stderr)

        time.sleep(SLEEP)

    _write_output(valued, failures, ok, skipped, err, t0, partial=False)


if __name__ == "__main__":
    main()
