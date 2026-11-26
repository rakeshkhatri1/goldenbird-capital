#!/usr/bin/env python3
"""
Build nse_market_data.json from data/historical_cache/*.json (no Kite calls).

Run after nse_universe_scanner.py (or standalone when cache is already fresh):

  python3 build_technicals.py

Weekly tab: indicators on W-FRI resampled bars.
Monthly tab: indicators on calendar-month (ME) resampled bars.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

from technical_indicators import resample_ohlcv, row_from_indicator_frame
from universe_filter import load_eligible_symbols

CACHE_DIR = os.environ.get("HISTORICAL_CACHE_DIR", "data/historical_cache")
OUTPUT_FILE = os.environ.get("MARKET_DATA_PATH", "nse_market_data.json")
SCREENER_MAP_PATH = os.environ.get(
    "SCREENER_MAP_PATH",
    str(Path(CACHE_DIR).parent / "screener_company_map.json"),
)
TECHNICALS_MAX = os.environ.get("TECHNICALS_MAX", "").strip()


def _load_cache_payload(filepath: Path, symbol: str) -> dict:
    sym_u = symbol.strip().upper()
    with open(filepath, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {
            "symbol": sym_u,
            "name": "",
            "exchange": "",
            "bars": raw,
        }
    return {
        "symbol": (raw.get("symbol") or sym_u).upper(),
        "name": raw.get("name") or "",
        "exchange": raw.get("exchange") or "",
        "bars": raw.get("bars") or raw.get("records") or [],
    }


def _bars_to_indexed_df(bars) -> pd.DataFrame | None:
    if not bars:
        return None
    df = pd.DataFrame(bars)
    if df.empty or "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.drop_duplicates(subset=["date"]).sort_values("date")
    df = df.set_index("date")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    return df if not df.empty else None


def _canonical_symbol_map() -> dict[str, str]:
    path = Path(SCREENER_MAP_PATH)
    if not path.is_file():
        return {}
    one: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for rec in (data.get("companies") or {}).values():
        kite = (rec.get("kite_symbol") or "").strip().upper()
        canon = (rec.get("canonical_symbol") or kite).strip().upper()
        if kite:
            one[kite] = canon
    return one


def _discover_symbols() -> list[str]:
    """Prefer screener map order; fall back to cache filenames."""
    path = Path(SCREENER_MAP_PATH)
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        companies = data.get("companies") or {}
        symbols = []
        for rec in companies.values():
            sym = (rec.get("kite_symbol") or rec.get("canonical_symbol") or "").strip().upper()
            if sym:
                symbols.append(sym)
        symbols.sort()
        if symbols:
            return symbols

    cache = Path(CACHE_DIR)
    if not cache.is_dir():
        return []
    return sorted(p.stem.upper() for p in cache.glob("*.json"))


def _build_row_for_timeframe(
    daily_df: pd.DataFrame,
    rule: str,
    *,
    symbol: str,
    name: str,
    exchange: str,
) -> dict | None:
    resampled = resample_ohlcv(daily_df, rule)
    if resampled is None or len(resampled) < 2:
        return None
    return row_from_indicator_frame(
        resampled,
        symbol=symbol,
        name=name,
        exchange=exchange,
    )


def write_market_data(*, verbose: bool = True) -> dict:
    symbols = _discover_symbols()
    eligible = load_eligible_symbols(strict=False)
    if eligible:
        before = len(symbols)
        symbols = [s for s in symbols if s in eligible]
        if verbose and before != len(symbols):
            print(f"Universe filter (≥₹100 Cr): {before:,} → {len(symbols):,} symbols")
    canon_map = _canonical_symbol_map()
    if TECHNICALS_MAX.isdigit():
        cap = int(TECHNICALS_MAX)
        if cap > 0:
            symbols = symbols[:cap]

    weekly_rows: list[dict] = []
    monthly_rows: list[dict] = []
    skipped = 0
    total = len(symbols)

    for i, sym in enumerate(symbols, start=1):
        fp = Path(CACHE_DIR) / f"{sym}.json"
        if not fp.is_file():
            skipped += 1
            continue
        try:
            payload = _load_cache_payload(fp, sym)
            daily = _bars_to_indexed_df(payload["bars"])
            if daily is None or len(daily) < 30:
                skipped += 1
                continue
            name = payload["name"] or sym
            exchange = payload["exchange"] or "NSE"
            symbol = canon_map.get(sym) or payload["symbol"] or sym

            w_row = _build_row_for_timeframe(
                daily, "W-FRI", symbol=symbol, name=name, exchange=exchange
            )
            m_row = _build_row_for_timeframe(
                daily, "ME", symbol=symbol, name=name, exchange=exchange
            )
            if w_row:
                weekly_rows.append(w_row)
            if m_row:
                monthly_rows.append(m_row)
            if verbose and i % 250 == 0:
                print(f"  [{i}/{total}] processed {sym} …")
        except Exception as e:
            skipped += 1
            if verbose:
                print(f"  Skip {sym}: {e}", file=sys.stderr)

    out = {"weekly_data": weekly_rows, "monthly_data": monthly_rows}
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f)
    os.replace(tmp, OUTPUT_FILE)

    if verbose:
        print(
            f"Wrote {len(weekly_rows)} weekly + {len(monthly_rows)} monthly rows "
            f"→ {OUTPUT_FILE} ({skipped} skipped)."
        )
    return out


def main():
    if not Path(CACHE_DIR).is_dir():
        print(f"Missing cache dir {CACHE_DIR}. Run nse_universe_scanner.py first.", file=sys.stderr)
        sys.exit(1)
    print(f"Building market data from {CACHE_DIR} …")
    write_market_data()


if __name__ == "__main__":
    main()
