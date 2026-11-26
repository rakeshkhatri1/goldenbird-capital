"""Shared ≥₹100 Cr universe filter for pipelines and UI bootstrap."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MCAP_PATH = Path(os.environ.get("MCAP_PATH", ROOT / "data" / "market_cap.json"))
VALUATION_PATH = Path(os.environ.get("VALUATION_PATH", ROOT / "data" / "valuation.json"))

MIN_MCAP_CR = float(os.environ.get("MIN_MCAP_CR", "100"))
DEEP_RESEARCH_MIN_MCAP_CR = float(os.environ.get("DEEP_RESEARCH_MIN_MCAP_CR", "100"))
DEEP_RESEARCH_MAX_MCAP_CR = float(os.environ.get("DEEP_RESEARCH_MAX_MCAP_CR", "1000"))


def _eligible_from_mcap_cr(mcap: float | None) -> bool:
    if mcap is None:
        return False
    return float(mcap) >= MIN_MCAP_CR


def load_market_cap_index() -> dict[str, dict]:
    if not MCAP_PATH.is_file():
        return {}
    payload = json.loads(MCAP_PATH.read_text(encoding="utf-8"))
    return dict(payload.get("companies") or {})


def load_eligible_symbols(*, strict: bool = True) -> set[str]:
    """
    Return kite symbols with market cap ≥ MIN_MCAP_CR.

    strict=True: only symbols explicitly marked eligible in market_cap.json.
    strict=False: also include valuation.json rows with market_cap_cr ≥ threshold.
    """
    eligible: set[str] = set()
    if MCAP_PATH.is_file():
        payload = json.loads(MCAP_PATH.read_text(encoding="utf-8"))
        for sym in payload.get("eligible_symbols") or []:
            s = str(sym).strip().upper()
            if s:
                eligible.add(s)
        if eligible:
            return eligible
        for sym, row in (payload.get("companies") or {}).items():
            if row.get("eligible") or _eligible_from_mcap_cr(row.get("market_cap_cr")):
                eligible.add(str(sym).strip().upper())
        if eligible:
            return eligible

    if not strict and VALUATION_PATH.is_file():
        payload = json.loads(VALUATION_PATH.read_text(encoding="utf-8"))
        for sym, row in (payload.get("companies") or {}).items():
            if _eligible_from_mcap_cr(row.get("market_cap_cr")):
                eligible.add(str(sym).strip().upper())
    return eligible


def load_known_ineligible_symbols() -> set[str]:
    """Symbols with fetched market cap strictly below MIN_MCAP_CR."""
    out: set[str] = set()
    for sym, row in load_market_cap_index().items():
        mcap = row.get("market_cap_cr")
        if mcap is not None and float(mcap) < MIN_MCAP_CR:
            out.add(str(sym).strip().upper())
    if VALUATION_PATH.is_file():
        payload = json.loads(VALUATION_PATH.read_text(encoding="utf-8"))
        for sym, row in (payload.get("companies") or {}).items():
            mcap = row.get("market_cap_cr")
            if mcap is not None and float(mcap) < MIN_MCAP_CR:
                out.add(str(sym).strip().upper())
    return out


def is_known_ineligible_symbol(symbol: str) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return True
    return sym in load_known_ineligible_symbols()


def kite_from_rec(rec: dict) -> str:
    return (rec.get("kite_symbol") or rec.get("canonical_symbol") or rec.get("symbol") or "").strip().upper()


def filter_targets(
    records: list[dict],
    *,
    eligible_only: bool = True,
    symbol_filter: set[str] | None = None,
) -> list[dict]:
    """Keep only ≥ MIN_MCAP_CR symbols when eligible_only and market_cap.json has data."""
    eligible: set[str] | None = None
    if eligible_only:
        eligible = load_eligible_symbols(strict=False)
        if not eligible:
            eligible = None
    out: list[dict] = []
    for rec in records:
        kite = kite_from_rec(rec)
        if not kite:
            continue
        if symbol_filter and kite not in symbol_filter:
            continue
        if eligible is not None and kite not in eligible:
            continue
        out.append(rec)
    return out


def prune_json_companies(payload: dict, ineligible: set[str], *, key: str = "companies") -> int:
    """Drop ineligible symbols from a JSON object with a companies dict. Returns removed count."""
    companies = payload.get(key)
    if not isinstance(companies, dict):
        return 0
    removed = 0
    for sym in list(companies.keys()):
        if str(sym).upper() in ineligible:
            del companies[sym]
            removed += 1
    return removed


def is_eligible_symbol(symbol: str, index: dict[str, dict] | None = None) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    eligible = load_eligible_symbols()
    if eligible:
        return sym in eligible
    idx = index if index is not None else load_market_cap_index()
    row = idx.get(sym)
    if row:
        return bool(row.get("eligible")) or _eligible_from_mcap_cr(row.get("market_cap_cr"))
    return False


def _mcap_in_band(mcap: float | None, *, min_cr: float, max_cr: float) -> bool:
    if mcap is None:
        return False
    return min_cr <= float(mcap) <= max_cr


def load_band_symbols(
    *,
    min_cr: float | None = None,
    max_cr: float | None = None,
    strict: bool = False,
) -> set[str]:
    """Return symbols with market cap in [min_cr, max_cr] (default ₹100–1,000 Cr)."""
    lo = DEEP_RESEARCH_MIN_MCAP_CR if min_cr is None else float(min_cr)
    hi = DEEP_RESEARCH_MAX_MCAP_CR if max_cr is None else float(max_cr)
    band: set[str] = set()
    idx = load_market_cap_index()
    for sym, row in idx.items():
        if _mcap_in_band(row.get("market_cap_cr"), min_cr=lo, max_cr=hi):
            band.add(str(sym).strip().upper())
    if band:
        return band
    if not strict and VALUATION_PATH.is_file():
        payload = json.loads(VALUATION_PATH.read_text(encoding="utf-8"))
        for sym, row in (payload.get("companies") or {}).items():
            if _mcap_in_band(row.get("market_cap_cr"), min_cr=lo, max_cr=hi):
                band.add(str(sym).strip().upper())
    return band


def is_in_mcap_band(
    symbol: str,
    *,
    min_cr: float | None = None,
    max_cr: float | None = None,
    index: dict[str, dict] | None = None,
) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    lo = DEEP_RESEARCH_MIN_MCAP_CR if min_cr is None else float(min_cr)
    hi = DEEP_RESEARCH_MAX_MCAP_CR if max_cr is None else float(max_cr)
    idx = index if index is not None else load_market_cap_index()
    row = idx.get(sym)
    if row and _mcap_in_band(row.get("market_cap_cr"), min_cr=lo, max_cr=hi):
        return True
    if VALUATION_PATH.is_file():
        payload = json.loads(VALUATION_PATH.read_text(encoding="utf-8"))
        vrow = (payload.get("companies") or {}).get(sym)
        if vrow and _mcap_in_band(vrow.get("market_cap_cr"), min_cr=lo, max_cr=hi):
            return True
    return False
