"""Shared Screener.in slug resolution for company map records."""
from __future__ import annotations

# Keep in sync with build_screener_map.SERIES_SUFFIXES (subset used for slug strip)
SERIES_SUFFIXES = ("-BE", "-SM", "-ST", "-BZ", "-IV", "-RR", "-SZ", "-HCIL", "-DVR", "-N1", "-N2", "-EQ")


def canonical_symbol(kite_symbol: str) -> str:
    """Strip NSE/BSE series suffix for stable company key (VETO-BE → VETO)."""
    u = str(kite_symbol or "").strip().upper()
    for suf in SERIES_SUFFIXES:
        if u.endswith(suf):
            return u[: -len(suf)]
    return u


def build_kite_symbol_index(companies: dict) -> dict[str, dict]:
    """Map any kite / canonical / exchange symbol → company record."""
    idx: dict[str, dict] = {}
    for key, rec in companies.items():
        if not isinstance(rec, dict):
            continue
        keys = {
            str(key).strip().upper(),
            str(rec.get("canonical_symbol") or "").strip().upper(),
            str(rec.get("kite_symbol") or "").strip().upper(),
            str(rec.get("nse_symbol") or "").strip().upper(),
            str(rec.get("bse_symbol") or "").strip().upper(),
        }
        for sid in rec.get("screener_ids") or []:
            s = str(sid).strip().upper()
            if s:
                keys.add(s)
        for s in keys:
            if s:
                idx.setdefault(s, rec)
    return idx


def lookup_company_record(companies: dict, symbol: str, *, index: dict[str, dict] | None = None) -> dict:
    """Resolve map row by kite tradingsymbol (handles VETO-BE → VETO canonical key)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"kite_symbol": symbol, "canonical_symbol": symbol}
    idx = index if index is not None else build_kite_symbol_index(companies)
    hit = idx.get(sym)
    if hit:
        return hit
    base = canonical_symbol(sym)
    if base != sym:
        hit = idx.get(base)
        if hit:
            return hit
    return {"kite_symbol": sym, "canonical_symbol": base, "screener_ids": [base] if base != sym else []}


def screener_slug_candidates(rec: dict) -> list[str]:
    """
    Ordered Screener /company/{slug}/ candidates.

    BSE-only names often 404 on ticker but work on numeric BSE scrip code
    (e.g. 1STCUS → 511122).
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str | None) -> None:
        s = str(raw or "").strip()
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s)

    nse = (rec.get("nse_symbol") or "").strip().upper()
    kite = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
    canon = (rec.get("canonical_symbol") or canonical_symbol(kite) or "").strip().upper()

    # Screener uses base slug (VETO), not NSE series ticker (VETO-BE).
    if canon and canon not in (nse, kite):
        add(canon)

    if nse:
        add(nse)

    alpha_ids: list[str] = []
    numeric_ids: list[str] = []
    for sid in rec.get("screener_ids") or []:
        s = str(sid).strip()
        if not s:
            continue
        if s.isdigit():
            numeric_ids.append(s)
        else:
            alpha_ids.append(s.upper())

    for s in alpha_ids:
        add(s)

    # Prefer BSE numeric id before BSE ticker when there is no NSE symbol.
    if not nse:
        for s in numeric_ids:
            add(s)

    if kite:
        add(kite)

    for s in numeric_ids:
        add(s)

    return out


def screener_slug(rec: dict) -> str | None:
    cands = screener_slug_candidates(rec)
    return cands[0] if cands else None
