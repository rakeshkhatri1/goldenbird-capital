"""
NSE EQ cache + scanner pipeline

1) One-time load (per symbol file): if ``data/historical_cache/<SYMBOL>.json`` does not exist,
   we pull daily candles (chunked, rate-limited), then write the file.

   - Default: last ``INITIAL_HISTORY_DAYS`` calendar days (default 2000).
   - Optional: ``INITIAL_HISTORY_FULL=1`` keeps requesting older chunks until Kite returns no
     rows — as much **daily** history as Zerodha has for that symbol (still subject to their
     archive; IPOs list less than multi-decade names).

2) Incremental runs: if the cache file exists, we only request from (last cached date + 1 day)
   through today and append + de-duplicate — light daily load.

3) After cache sync, ``build_technicals.py`` runs automatically (or run it standalone):
   reads ``data/historical_cache/*.json``, computes EMA/SuperTrend/RSI/ADX/BB/relVol on
   weekly (W-FRI) and monthly (ME) bars, writes ``nse_market_data.json``.

Cache file layout (``data/historical_cache/<SYMBOL>.json`` — filename = uppercased tradingsymbol):

  ``{"version": 1, "symbol": "RELIANCE", "name": "...", "exchange": "NSE",
    "instrument_token": 738561, "updated_at": "...", "bar_count": N, "bars": [OHLCV...]}``

  Legacy plain ``[{date, open, ...}, ...]`` arrays are still read on load and rewritten to v1.

Universe:
  ``SCANNER_UNIVERSE=all`` — Zerodha ``instruments("NSE")`` (EQ, incl. debt).
  ``SCANNER_UNIVERSE=screener`` — companies from ``data/screener_company_map.json``
  (~5.3k stocks; NSE or BSE per map). Build map first: ``build_screener_map.py``.
  Otherwise ``SCANNER_SYMBOLS`` (comma list).

3) Symbol venue (Zerodha only): when ``USE_SYMBOL_VENUE_REGISTRY=1`` (default), we load NSE and
   BSE EQ master lists. For each symbol, if it exists on both exchanges we request one **daily**
   history window on each side, pick the venue with an **older first bar** (tie: more bars in
   that window, then NSE). Choice + ``instrument_token`` are stored in
   ``data/symbol_venue_registry.json`` (path override: ``SYMBOL_VENUE_REGISTRY_PATH``). Stale
   entries are re-compared every ``REGISTRY_VENUE_REFRESH_DAYS`` (default 7). When registry is
   off, behaviour is NSE-only (one ``instruments`` call), same as before.

Env (optional):
  INITIAL_HISTORY_DAYS — fixed window for first-time cache (default 2000); ignored if FULL=1.
  INITIAL_HISTORY_FULL=1 — first-time cache walks backward until empty (max history).
  BACKFILL_MAX_CHUNKS — safety cap for FULL mode (default 80).
  FORCE_REBACKFILL=1 — delete cache files for symbols in this run, then re-do step (1).
  SCANNER_UNIVERSE=all | screener | else SCANNER_SYMBOLS list.
  SCREENER_MAP_PATH — company map JSON (default: data/screener_company_map.json).
  USE_SYMBOL_VENUE_REGISTRY — 1 (default) enable NSE vs BSE depth compare + JSON registry; 0 = NSE only.
  SYMBOL_VENUE_REGISTRY_PATH — registry file (default: data/symbol_venue_registry.json).
  REGISTRY_VENUE_REFRESH_DAYS — re-run depth compare after this many days (default 7).
  FORCE_VENUE_RECOMPARE=1 — refresh venue for every symbol this run (adds historical API calls).
  FORCE_REGISTRY_RESET=1 — delete registry file at startup (rebuild from scratch).
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect, exceptions

from universe_filter import filter_targets

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.environ.get("KITE_API_KEY", "").strip()
ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "").strip()

CACHE_DIR = "data/historical_cache"
CACHE_JSON_VERSION = 1

HISTORICAL_MIN_INTERVAL = float(os.environ.get("KITE_HISTORICAL_MIN_INTERVAL", "0.35"))
DAY_CHUNK_MAX_DAYS = int(os.environ.get("KITE_DAY_CHUNK_DAYS", "1990"))
INITIAL_HISTORY_DAYS = int(os.environ.get("INITIAL_HISTORY_DAYS", "2000"))
_INITIAL_FULL_RAW = os.environ.get("INITIAL_HISTORY_FULL", "").strip().lower()
INITIAL_HISTORY_FULL = _INITIAL_FULL_RAW in ("1", "true", "yes", "on")
_FORCE_RE_RAW = os.environ.get("FORCE_REBACKFILL", "").strip().lower()
FORCE_REBACKFILL = _FORCE_RE_RAW in ("1", "true", "yes", "on")
SCANNER_UNIVERSE = os.environ.get("SCANNER_UNIVERSE", "").strip().lower()
SCANNER_SYMBOLS = os.environ.get("SCANNER_SYMBOLS", "YESBANK").strip()
SCANNER_MAX_SYMBOLS = os.environ.get("SCANNER_MAX_SYMBOLS", "").strip()
SCREENER_MAP_PATH = os.environ.get(
    "SCREENER_MAP_PATH",
    str(Path(CACHE_DIR).parent / "screener_company_map.json"),
).strip()
SYMBOL_VENUE_REGISTRY_PATH = os.environ.get(
    "SYMBOL_VENUE_REGISTRY_PATH",
    str(Path(CACHE_DIR).parent / "symbol_venue_registry.json"),
).strip()
_USE_REG_RAW = os.environ.get("USE_SYMBOL_VENUE_REGISTRY", "").strip().lower()
USE_SYMBOL_VENUE_REGISTRY = _USE_REG_RAW not in ("0", "false", "no", "off")
REGISTRY_VENUE_REFRESH_DAYS = int(os.environ.get("REGISTRY_VENUE_REFRESH_DAYS", "7"))
_FORCE_VENUE_RAW = os.environ.get("FORCE_VENUE_RECOMPARE", "").strip().lower()
FORCE_VENUE_RECOMPARE = _FORCE_VENUE_RAW in ("1", "true", "yes", "on")
_FORCE_REG_RESET_RAW = os.environ.get("FORCE_REGISTRY_RESET", "").strip().lower()
FORCE_REGISTRY_RESET = _FORCE_REG_RESET_RAW in ("1", "true", "yes", "on")

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)


def _kite_client():
    if not API_KEY or not ACCESS_TOKEN:
        print(
            "Missing KITE_API_KEY or KITE_ACCESS_TOKEN.\n"
            "  Put them in a .env file in this folder, or:\n"
            "  export KITE_API_KEY='your_api_key'\n"
            "  export KITE_ACCESS_TOKEN='token_from_generate_session'\n"
            "Generate a fresh access token with: python kite_session.py",
            file=sys.stderr,
        )
        sys.exit(1)
    k = KiteConnect(api_key=API_KEY)
    k.set_access_token(ACCESS_TOKEN)
    return k


def verify_kite_session(kite_instance):
    try:
        profile = kite_instance.profile()
        print(f"Kite session OK: {profile.get('user_name', profile.get('user_id'))}")
        return True
    except exceptions.TokenException as e:
        print(
            "Zerodha rejected the access token (expired, wrong key, or token is actually a request_token).\n"
            "Access tokens expire daily — run `python kite_session.py` and export the new KITE_ACCESS_TOKEN.",
            file=sys.stderr,
        )
        print(f"Detail: {e}", file=sys.stderr)
        return False
    except exceptions.NetworkException as e:
        print(f"Network error talking to Kite: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Kite session check failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


kite = _kite_client()


def build_eq_instrument_index(kite_instance, exchange: str):
    """Zerodha ``instruments(exchange)``: sym -> {instrument_token, name, exchange}."""
    expected_seg = "NSE" if exchange == "NSE" else "BSE"
    out = {}
    for inst in kite_instance.instruments(exchange):
        if inst.get("instrument_type") != "EQ":
            continue
        if inst.get("segment") != expected_seg:
            continue
        sym = inst.get("tradingsymbol")
        if not sym:
            continue
        out[sym.upper()] = {
            "instrument_token": int(inst["instrument_token"]),
            "name": (inst.get("name") or "").strip(),
            "exchange": exchange,
        }
    return out


def build_nse_eq_symbol_to_token(kite_instance):
    """NSE cash EQ (segment NSE)."""
    return build_eq_instrument_index(kite_instance, "NSE")


def build_bse_eq_symbol_to_token(kite_instance):
    """BSE cash EQ (segment BSE)."""
    return build_eq_instrument_index(kite_instance, "BSE")


def _idx_token(idx: dict, sym_u: str):
    entry = idx.get(sym_u)
    if not entry:
        return None
    if isinstance(entry, int):
        return entry
    return entry.get("instrument_token")


def _idx_name(idx: dict, sym_u: str) -> str:
    entry = idx.get(sym_u)
    if not entry or isinstance(entry, int):
        return ""
    return (entry.get("name") or "").strip()


def _display_name(idx: dict, sym_u: str, venue_idx: dict | None = None) -> str:
    """
    Zerodha ``name`` for many NCD/debenture rows is blank in instruments().
    Fall back to tradingsymbol so cache/UI always have a label.
    """
    sym_u = sym_u.upper()
    for source in (venue_idx, idx):
        if not source:
            continue
        n = _idx_name(source, sym_u)
        if n:
            return n
    return sym_u


def _cache_filepath(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol.strip().upper()}.json")


def _load_cache_payload(filepath: str, symbol: str) -> dict:
    """Return v1-shaped dict with symbol, name, exchange, instrument_token, bars."""
    sym_u = symbol.strip().upper()
    with open(filepath, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {
            "version": CACHE_JSON_VERSION,
            "symbol": sym_u,
            "name": "",
            "exchange": "",
            "instrument_token": None,
            "bars": raw,
        }
    bars = raw.get("bars") or raw.get("records") or []
    return {
        "version": raw.get("version", CACHE_JSON_VERSION),
        "symbol": (raw.get("symbol") or sym_u).upper(),
        "name": raw.get("name") or "",
        "exchange": raw.get("exchange") or "",
        "instrument_token": raw.get("instrument_token"),
        "bars": bars,
    }


def _normalize_bar_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], utc=True)
    return out


def _bars_to_dataframe(bars) -> pd.DataFrame | None:
    if not bars:
        return None
    df = pd.DataFrame(bars)
    if df.empty or "date" not in df.columns:
        return None
    df = _normalize_bar_dates(df)
    return df.drop_duplicates(subset=["date"]).sort_values("date")


def _naive_datetime(ts) -> datetime:
    """Cache ISO dates are UTC-aware; Kite + datetime.now() are naive."""
    t = pd.to_datetime(ts, utc=True)
    return t.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)


def _write_symbol_cache(
    symbol: str,
    name: str,
    exchange: str,
    instrument_token,
    df: pd.DataFrame,
    filepath: str,
) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = _normalize_bar_dates(df)
    df = df.drop_duplicates(subset=["date"]).sort_values("date")
    bars = json.loads(df.to_json(orient="records", date_format="iso"))
    payload = {
        "version": CACHE_JSON_VERSION,
        "symbol": symbol.strip().upper(),
        "name": (name or "").strip() or symbol.strip().upper(),
        "exchange": exchange or "",
        "instrument_token": int(instrument_token) if instrument_token is not None else None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "bar_count": len(bars),
        "bars": bars,
    }
    tmp = filepath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, filepath)
    return df


def _write_cache_index():
    """UI sidebar charts use data/cache_index.json to pick symbols with history."""
    cache_path = Path(CACHE_DIR)
    if not cache_path.is_dir():
        return
    syms = sorted(p.stem.upper() for p in cache_path.glob("*.json"))
    out = cache_path.parent / "cache_index.json"
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(syms),
        "symbols": syms,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    print(f"Cache index → {out} ({len(syms):,} symbols)")


def _chunk_oldest_bar_stats(limiter, token):
    """One day-candle window up to DAY_CHUNK_MAX_DAYS; return (oldest_ts_utc or None, bar_count)."""
    if token is None:
        return None, 0
    end = datetime.now()
    start = end - timedelta(days=DAY_CHUNK_MAX_DAYS)
    part = limiter.safe_historical_data(token, start, end, interval="day")
    if not part:
        return None, 0
    oldest = min(pd.to_datetime(p["date"], utc=True) for p in part)
    return oldest, len(part)


def resolve_venue_by_history(sym: str, nse_idx: dict, bse_idx: dict, limiter):
    """
    Pick NSE vs BSE by deeper daily history in one long window (earlier first bar;
    tie-break bar count, then NSE). Returns (instrument_token, venue, meta_dict) or (None, None, None).
    """
    sym_u = sym.upper()
    tn = _idx_token(nse_idx, sym_u)
    tb = _idx_token(bse_idx, sym_u)
    meta = {"symbol": sym_u}
    if tn is None and tb is None:
        return None, None, None
    if tn is None:
        ts_b, c_b = _chunk_oldest_bar_stats(limiter, tb)
        meta.update(
            {
                "nse_token": None,
                "bse_token": tb,
                "nse_oldest_iso": None,
                "bse_oldest_iso": ts_b.isoformat() if ts_b is not None else None,
                "nse_bar_count": 0,
                "bse_bar_count": c_b,
                "reason": "bse_only",
            }
        )
        return tb, "BSE", meta
    if tb is None:
        ts_n, c_n = _chunk_oldest_bar_stats(limiter, tn)
        meta.update(
            {
                "nse_token": tn,
                "bse_token": None,
                "nse_oldest_iso": ts_n.isoformat() if ts_n is not None else None,
                "bse_oldest_iso": None,
                "nse_bar_count": c_n,
                "bse_bar_count": 0,
                "reason": "nse_only",
            }
        )
        return tn, "NSE", meta

    ts_n, c_n = _chunk_oldest_bar_stats(limiter, tn)
    ts_b, c_b = _chunk_oldest_bar_stats(limiter, tb)
    meta.update(
        {
            "nse_token": tn,
            "bse_token": tb,
            "nse_oldest_iso": ts_n.isoformat() if ts_n is not None else None,
            "bse_oldest_iso": ts_b.isoformat() if ts_b is not None else None,
            "nse_bar_count": c_n,
            "bse_bar_count": c_b,
        }
    )
    if ts_n is None and ts_b is None:
        return None, None, None
    if ts_n is None:
        meta["reason"] = "bse_has_data"
        return tb, "BSE", meta
    if ts_b is None:
        meta["reason"] = "nse_has_data"
        return tn, "NSE", meta
    if ts_b < ts_n:
        meta["reason"] = "bse_older_first_bar"
        return tb, "BSE", meta
    if ts_n < ts_b:
        meta["reason"] = "nse_older_first_bar"
        return tn, "NSE", meta
    if c_b > c_n:
        meta["reason"] = "tie_older_same_day_more_bars_bse"
        return tb, "BSE", meta
    meta["reason"] = "tie_older_same_day_more_bars_or_nse_default"
    return tn, "NSE", meta


def _registry_path():
    return Path(SYMBOL_VENUE_REGISTRY_PATH).expanduser()


def load_symbol_venue_registry():
    path = _registry_path()
    if not path.is_file():
        return {"version": 1, "updated_at": None, "symbols": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "updated_at": None, "symbols": {}}
    if "symbols" not in data:
        data["symbols"] = {}
    return data


def save_symbol_venue_registry(data):
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def _venue_entry_stale(rec: dict) -> bool:
    if FORCE_VENUE_RECOMPARE:
        return True
    if REGISTRY_VENUE_REFRESH_DAYS <= 0:
        return True
    ts = rec.get("venue_compared_at")
    if not ts:
        return True
    try:
        compared = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if compared.tzinfo:
            compared = compared.replace(tzinfo=None)
    except ValueError:
        return True
    return datetime.now() - compared > timedelta(days=REGISTRY_VENUE_REFRESH_DAYS)


def _flatten_registry_entry(sym: str, venue: str, token: int, meta: dict) -> dict:
    rec = {
        "venue": venue,
        "instrument_token": int(token),
        "venue_compared_at": datetime.now().isoformat(timespec="seconds"),
    }
    for k, v in meta.items():
        if k == "symbol":
            continue
        if v is not None and k not in rec:
            rec[k] = v
    return rec


def pick_token_with_venue_registry(symbol, nse_idx, bse_idx, limiter, registry_data: dict):
    """
    Use registry when entry exists and not stale; else resolve by history depth.
    Returns (token, venue, registry_changed).
    """
    sym_u = symbol.upper()
    sym_map = registry_data.setdefault("symbols", {})
    rec = sym_map.get(sym_u)
    if rec and not _venue_entry_stale(rec):
        return rec["instrument_token"], rec["venue"], False

    token, venue, meta = resolve_venue_by_history(symbol, nse_idx, bse_idx, limiter)
    if token is None:
        return None, None, False
    new_entry = _flatten_registry_entry(sym_u, venue, token, meta or {})
    old = sym_map.get(sym_u)
    if old and (old.get("venue") != venue or old.get("instrument_token") != token):
        fp = os.path.join(CACHE_DIR, f"{sym_u}.json")
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                print(
                    f"  {sym_u}: venue changed {old.get('venue')}→{venue}; removed stale cache {fp}",
                    file=sys.stderr,
                )
            except OSError as e:
                print(f"  {sym_u}: could not remove cache after venue change: {e}", file=sys.stderr)
    sym_map[sym_u] = new_entry
    return token, venue, True


def _retryable_kite_error(exc):
    if not isinstance(exc, exceptions.KiteException):
        return False
    return exc.code in (429, 503)


class HistoricalPacer:
    def __init__(self, min_interval: float):
        self.min_interval = max(min_interval, 0.01)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()


class KiteRateLimiter:
    def __init__(self, kite_instance, pacer: HistoricalPacer):
        self.kite = kite_instance
        self.pacer = pacer

    def safe_historical_data(self, instrument_token, from_date, to_date, interval, max_retries=8):
        token = int(instrument_token) if instrument_token is not None else None
        retries = 0
        while retries < max_retries:
            self.pacer.wait()
            try:
                return self.kite.historical_data(
                    instrument_token=token,
                    from_date=from_date,
                    to_date=to_date,
                    interval=interval,
                )
            except exceptions.InputException as e:
                if "invalid token" in str(e).lower():
                    print(
                        f"Invalid instrument_token {token!r} for historical API.",
                        file=sys.stderr,
                    )
                else:
                    print(f"Input error for instrument {token}: {e}", file=sys.stderr)
                break
            except exceptions.KiteException as e:
                if _retryable_kite_error(e):
                    wait_time = max(2 ** min(retries, 6), 1.0)
                    print(f"Kite HTTP {e.code} ({e}); backing off {wait_time:.1f}s...", file=sys.stderr)
                    time.sleep(wait_time)
                    retries += 1
                    continue
                print(f"Error fetching {token}: {e}", file=sys.stderr)
                break
            except Exception as e:
                print(f"Error fetching {token}: {e}", file=sys.stderr)
                break
        return None


def backfill_daily_fixed_window(symbol, token, limiter, filepath, name="", exchange="NSE"):
    to_dt = datetime.now()
    start = to_dt - timedelta(days=INITIAL_HISTORY_DAYS)
    cur = start
    rows = []
    while cur <= to_dt:
        chunk_end = min(to_dt, cur + timedelta(days=DAY_CHUNK_MAX_DAYS))
        part = limiter.safe_historical_data(token, cur, chunk_end, interval="day")
        if part:
            rows.extend(part)
        if chunk_end >= to_dt:
            break
        cur = chunk_end + timedelta(days=1)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return _write_symbol_cache(symbol, name, exchange, token, df, filepath)


def backfill_daily_max_available(symbol, token, limiter, filepath, name="", exchange="NSE"):
    window_end = datetime.now()
    rows = []
    max_chunks = int(os.environ.get("BACKFILL_MAX_CHUNKS", "80"))
    for _ in range(max_chunks):
        start = window_end - timedelta(days=DAY_CHUNK_MAX_DAYS)
        if start >= window_end:
            break
        part = limiter.safe_historical_data(token, start, window_end, interval="day")
        if not part:
            break
        rows.extend(part)
        ts_pd = min(pd.to_datetime(p["date"], utc=True) for p in part)
        oldest_naive = ts_pd.tz_convert("UTC").to_pydatetime().replace(tzinfo=None)
        next_window_end = oldest_naive - timedelta(days=1)
        if next_window_end >= window_end:
            break
        window_end = next_window_end
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return _write_symbol_cache(symbol, name, exchange, token, df, filepath)


def backfill_daily_to_cache(symbol, token, limiter, filepath, name="", exchange="NSE"):
    if INITIAL_HISTORY_FULL:
        return backfill_daily_max_available(
            symbol, token, limiter, filepath, name=name, exchange=exchange
        )
    return backfill_daily_fixed_window(
        symbol, token, limiter, filepath, name=name, exchange=exchange
    )


def fetch_delta_data(symbol, token, limiter, name="", exchange="NSE"):
    filepath = _cache_filepath(symbol)
    sym_u = symbol.strip().upper()

    if not os.path.exists(filepath):
        return backfill_daily_to_cache(
            sym_u, token, limiter, filepath, name=name, exchange=exchange
        )

    payload = _load_cache_payload(filepath, sym_u)
    df = _bars_to_dataframe(payload["bars"])
    if df is None or df.empty:
        return backfill_daily_to_cache(
            sym_u, token, limiter, filepath, name=name, exchange=exchange
        )

    cache_name = (payload.get("name") or "").strip()
    cache_exchange = payload.get("exchange") or exchange
    cache_token = payload.get("instrument_token") or token
    display_name = (name or "").strip() or cache_name or sym_u

    last_date = _naive_datetime(df["date"].iloc[-1])
    from_date = last_date + timedelta(days=1)
    to_date = datetime.now()
    if from_date >= to_date:
        return df

    new_data = limiter.safe_historical_data(token, from_date, to_date, interval="day")
    if new_data:
        df_new = pd.DataFrame(new_data)
        df_combined = pd.concat([df, df_new]).drop_duplicates(subset=["date"])
        return _write_symbol_cache(
            sym_u,
            display_name,
            cache_exchange or exchange,
            cache_token or token,
            df_combined,
            filepath,
        )
    return df


def _apply_symbol_cap(symbols: list) -> list:
    if SCANNER_MAX_SYMBOLS.isdigit():
        cap = int(SCANNER_MAX_SYMBOLS)
        if cap > 0:
            return symbols[:cap]
    return symbols


def _symbols_to_sync(eq_index):
    if SCANNER_UNIVERSE == "all":
        symbols = sorted(eq_index.keys())
    else:
        symbols = [s.strip().upper() for s in SCANNER_SYMBOLS.split(",") if s.strip()]
    return _apply_symbol_cap(symbols)


def load_screener_sync_targets() -> list[dict]:
    """One row per company from screener_company_map.json (kite_symbol + venue + token)."""
    path = Path(SCREENER_MAP_PATH)
    if not path.is_file():
        print(
            f"Missing {path}. Run: .venv/bin/python3 build_screener_map.py",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    companies = data.get("companies") or {}
    targets = []
    for rec in companies.values():
        kite_sym = (rec.get("kite_symbol") or "").strip().upper()
        if not kite_sym:
            continue
        targets.append(
            {
                "kite_symbol": kite_sym,
                "canonical_symbol": (rec.get("canonical_symbol") or kite_sym).upper(),
                "venue": (rec.get("venue") or "NSE").upper(),
                "instrument_token": rec.get("instrument_token"),
                "name": (rec.get("name") or "").strip(),
            }
        )
    targets.sort(key=lambda t: t["kite_symbol"])
    before = len(targets)
    targets = filter_targets(targets, eligible_only=True)
    if before != len(targets):
        print(
            f"Universe filter (≥₹100 Cr): {before:,} → {len(targets):,} screener companies",
            flush=True,
        )
    if SCANNER_MAX_SYMBOLS.isdigit():
        cap = int(SCANNER_MAX_SYMBOLS)
        if cap > 0:
            targets = targets[:cap]
    return targets


def run_pipeline():
    if not verify_kite_session(kite):
        sys.exit(1)

    print("Loading NSE EQ instrument index from Zerodha (one API call)...")
    eq_index = build_nse_eq_symbol_to_token(kite)
    bse_idx = {}
    registry_data = None
    use_screener = SCANNER_UNIVERSE == "screener"
    need_bse = USE_SYMBOL_VENUE_REGISTRY or use_screener
    if need_bse:
        print("Loading BSE EQ instrument index...")
        bse_idx = build_bse_eq_symbol_to_token(kite)
        print(f"  NSE EQ: {len(eq_index):,} | BSE EQ: {len(bse_idx):,}")
    if USE_SYMBOL_VENUE_REGISTRY:
        registry_data = load_symbol_venue_registry()
        if FORCE_REGISTRY_RESET:
            p = _registry_path()
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
                print(f"FORCE_REGISTRY_RESET=1: removed {p}")
            registry_data = {"version": 1, "updated_at": None, "symbols": {}}
        else:
            n_reg = len(registry_data.get("symbols", {}))
            print(
                f"Symbol venue registry: {_registry_path()} ({n_reg:,} symbols); "
                f"refresh depth compare every {REGISTRY_VENUE_REFRESH_DAYS}d "
                f"(FORCE_VENUE_RECOMPARE=1 to refresh all this run)."
            )
    elif not use_screener:
        print(
            "USE_SYMBOL_VENUE_REGISTRY=0: NSE EQ only (no BSE instruments call, no depth compare)."
        )

    if use_screener:
        sync_targets = load_screener_sync_targets()
        print(
            f"SCANNER_UNIVERSE=screener: {len(sync_targets):,} companies from {SCREENER_MAP_PATH}"
        )
    else:
        sync_targets = [
            {"kite_symbol": s, "venue": None, "instrument_token": None, "name": ""}
            for s in _symbols_to_sync(eq_index)
        ]
    if not sync_targets:
        print("No symbols to sync.", file=sys.stderr)
        sys.exit(1)

    pacer = HistoricalPacer(HISTORICAL_MIN_INTERVAL)
    limiter = KiteRateLimiter(kite, pacer)
    print(
        f"Pacing: ≥{HISTORICAL_MIN_INTERVAL:.2f}s between historical requests "
        f"(~{1.0 / HISTORICAL_MIN_INTERVAL:.1f}/s max, under Zerodha 3/s)."
    )
    if INITIAL_HISTORY_FULL:
        print(
            "First-time cache: INITIAL_HISTORY_FULL=1 — backward chunks until no data "
            f"(cap BACKFILL_MAX_CHUNKS={os.environ.get('BACKFILL_MAX_CHUNKS', '80')})."
        )
    else:
        print(
            f"First-time cache: last {INITIAL_HISTORY_DAYS} calendar days per symbol "
            f"(set INITIAL_HISTORY_FULL=1 for max available daily history)."
        )
    print(
        "Incremental rule: existing cache → fetch only after last stored date; "
        "missing cache → one-time backfill for that symbol."
    )

    if FORCE_REBACKFILL:
        for target in sync_targets:
            sym = target["kite_symbol"]
            fp = os.path.join(CACHE_DIR, f"{sym}.json")
            if os.path.isfile(fp):
                os.remove(fp)
        print("FORCE_REBACKFILL=1: removed cache JSON for symbols in this run.")

    total = len(sync_targets)
    registry_dirty = False
    for i, target in enumerate(sync_targets, start=1):
        sym_u = target["kite_symbol"]
        on_nse = sym_u in eq_index
        on_bse = sym_u in bse_idx
        if USE_SYMBOL_VENUE_REGISTRY and on_nse and on_bse:
            token, venue, reg_changed = pick_token_with_venue_registry(
                sym_u, eq_index, bse_idx, limiter, registry_data
            )
            if reg_changed:
                registry_dirty = True
        elif target.get("instrument_token"):
            token = int(target["instrument_token"])
            venue = target.get("venue") or ("NSE" if on_nse else "BSE")
        elif on_nse:
            token = _idx_token(eq_index, sym_u)
            venue = "NSE"
        elif on_bse:
            token = _idx_token(bse_idx, sym_u)
            venue = "BSE"
        else:
            token = None
            venue = target.get("venue") or "NSE"
        venue_idx = bse_idx if venue == "BSE" else eq_index
        inst_name = target.get("name") or _display_name(eq_index, sym_u, venue_idx)
        if token is None:
            if USE_SYMBOL_VENUE_REGISTRY or use_screener:
                msg = "not on Zerodha NSE/BSE EQ (or no daily history in compare window on either)."
            else:
                msg = "not in Zerodha NSE EQ universe."
            print(f"[{i}/{total}] Skip {sym_u}: {msg}", file=sys.stderr)
            continue
        print(f"[{i}/{total}] Syncing {sym_u} [{venue}]...")
        df = fetch_delta_data(sym_u, token, limiter, name=inst_name, exchange=venue)
        if df is None or df.empty:
            continue

    if USE_SYMBOL_VENUE_REGISTRY and registry_dirty:
        save_symbol_venue_registry(registry_data)
        print(f"Saved symbol venue registry → {_registry_path()}")

    _write_cache_index()
    print("Cache sync complete. Building nse_market_data.json from historical cache …")
    from build_technicals import write_market_data

    write_market_data()
    print("Pipeline complete.")


if __name__ == "__main__":
    run_pipeline()
