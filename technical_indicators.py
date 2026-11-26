"""
TradingView-aligned SuperTrend (3/10 and 3/7), EMA 30/50/100, RSI, ADX, BB, rel vol.

Ported from zerodha_supertrend_project indicators (same Pine inputs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta

_ATOL = 1e-9


def _same(a: float, b: float) -> bool:
    return bool(np.isclose(a, b, rtol=_ATOL, atol=_ATOL))


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("ATR period must be >= 1")
    tr = true_range(high, low, close)
    atr = pd.Series(index=tr.index, dtype="float64")
    if len(tr) < period:
        return atr
    atr.iloc[period - 1] = tr.iloc[:period].mean()
    alpha = 1.0 / period
    for i in range(period, len(tr)):
        atr.iloc[i] = atr.iloc[i - 1] * (1 - alpha) + tr.iloc[i] * alpha
    return atr


def ema(source: pd.Series, length: int) -> pd.Series:
    if length < 1:
        raise ValueError("EMA length must be >= 1")
    return source.ewm(span=length, adjust=False).mean()


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_period: int,
    factor: float,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Returns supertrend, direction (-1 up, +1 down), final_upper, final_lower."""
    if factor <= 0:
        raise ValueError("factor must be > 0")

    atr = atr_wilder(high, low, close, atr_period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + factor * atr
    basic_lower = hl2 - factor * atr

    n = len(close)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)

    idx = close.index
    nan4 = (
        pd.Series(np.nan, index=idx),
        pd.Series(direction, index=idx),
        pd.Series(np.nan, index=idx),
        pd.Series(np.nan, index=idx),
    )

    i0 = atr_period - 1
    if n <= i0:
        return nan4

    final_upper[i0] = basic_upper.iloc[i0]
    final_lower[i0] = basic_lower.iloc[i0]

    if close.iloc[i0] <= final_upper[i0]:
        st[i0] = final_upper[i0]
        direction[i0] = 1
    else:
        st[i0] = final_lower[i0]
        direction[i0] = -1

    for i in range(i0 + 1, n):
        bu = basic_upper.iloc[i]
        bl = basic_lower.iloc[i]
        pc = close.iloc[i - 1]

        if bu < final_upper[i - 1] or pc > final_upper[i - 1]:
            final_upper[i] = bu
        else:
            final_upper[i] = final_upper[i - 1]

        if bl > final_lower[i - 1] or pc < final_lower[i - 1]:
            final_lower[i] = bl
        else:
            final_lower[i] = final_lower[i - 1]

        prev_st = st[i - 1]
        fu = final_upper[i]
        fl = final_lower[i]
        c = close.iloc[i]
        pfu = final_upper[i - 1]
        pfl = final_lower[i - 1]

        if _same(prev_st, pfu):
            if c <= fu:
                st[i] = fu
                direction[i] = 1
            else:
                st[i] = fl
                direction[i] = -1
        elif _same(prev_st, pfl):
            if c >= fl:
                st[i] = fl
                direction[i] = -1
            else:
                st[i] = fu
                direction[i] = 1
        else:
            if c <= fu:
                st[i] = fu
                direction[i] = 1
            else:
                st[i] = fl
                direction[i] = -1

    return (
        pd.Series(st, index=idx),
        pd.Series(direction, index=idx),
        pd.Series(final_upper, index=idx),
        pd.Series(final_lower, index=idx),
    )


@dataclass(frozen=True)
class IndicatorConfig:
    st1_atr_period: int = 10
    st1_factor: float = 3.0
    st2_atr_period: int = 7
    st2_factor: float = 3.0
    ema_30_period: int = 30
    ema_50_period: int = 50
    ema_100_period: int = 100


def _last_plotted_on_last_bullish_bar(df: pd.DataFrame, dir_col: str, st_col: str) -> float | None:
    if dir_col not in df.columns or st_col not in df.columns:
        return None
    d_arr = np.asarray(pd.to_numeric(df[dir_col], errors="coerce"), dtype=np.float64)
    st_arr = np.asarray(pd.to_numeric(df[st_col], errors="coerce"), dtype=np.float64)
    with np.errstate(invalid="ignore"):
        m = (d_arr == -1.0) & np.isfinite(st_arr)
    if m.size == 0 or not np.any(m):
        return None
    pos = int(np.flatnonzero(m)[-1])
    v = float(st_arr[pos])
    return v if math.isfinite(v) else None


def compute_indicator_frame(df: pd.DataFrame, cfg: IndicatorConfig | None = None) -> pd.DataFrame:
    """Add indicator columns to OHLCV frame (DatetimeIndex)."""
    cfg = cfg or IndicatorConfig()
    if df.empty:
        return df

    out = df.copy()
    high, low, close, volume = out["high"], out["low"], out["close"], out["volume"]

    st1, d1, st1_fu, st1_fl = supertrend(high, low, close, cfg.st1_atr_period, cfg.st1_factor)
    st2, d2, st2_fu, st2_fl = supertrend(high, low, close, cfg.st2_atr_period, cfg.st2_factor)
    out["supertrend_1"] = st1
    out["supertrend_dir_1"] = d1
    out["supertrend_1_upper_band"] = st1_fu
    out["supertrend_1_lower_band"] = st1_fl
    out["supertrend_2"] = st2
    out["supertrend_dir_2"] = d2
    out["supertrend_2_upper_band"] = st2_fu
    out["supertrend_2_lower_band"] = st2_fl

    if len(out) > 0:
        z = out.index[0]
        for c in (
            "supertrend_1",
            "supertrend_2",
            "supertrend_1_upper_band",
            "supertrend_1_lower_band",
            "supertrend_2_upper_band",
            "supertrend_2_lower_band",
        ):
            out.loc[z, c] = np.nan

    out["ema_30"] = ema(close, cfg.ema_30_period)
    out["ema_50"] = ema(close, cfg.ema_50_period)
    out["ema_100"] = ema(close, cfg.ema_100_period)
    out["RSI"] = ta.rsi(close, length=14)

    adx_data = ta.adx(high, low, close, length=14)
    if adx_data is not None and not adx_data.empty:
        adx_col = next((c for c in adx_data.columns if str(c).upper().startswith("ADX")), None)
        out["ADX"] = adx_data[adx_col].reindex(out.index) if adx_col else float("nan")
    else:
        out["ADX"] = float("nan")

    bb = ta.bbands(close, length=20, std=2)
    if bb is not None and not bb.empty:
        lower_col = next((c for c in bb.columns if "BBL" in c.upper()), None)
        upper_col = next((c for c in bb.columns if "BBU" in c.upper()), None)
        out["BBL"] = bb[lower_col] if lower_col else float("nan")
        out["BBU"] = bb[upper_col] if upper_col else float("nan")
    else:
        out["BBL"] = float("nan")
        out["BBU"] = float("nan")

    vol_sma = volume.rolling(20, min_periods=1).mean()
    out["rel_vol"] = volume / vol_sma.replace(0, np.nan)

    return out


def _pct_distance(close: float, level: float | None) -> float | None:
    if level is None or not math.isfinite(level) or level == 0 or not math.isfinite(close):
        return None
    return round((close / level - 1.0) * 100.0, 2)


def _ema_state(e30: float | None, e50: float | None, e100: float | None) -> str:
    if e30 is None or e50 is None or e100 is None:
        return "Neutral"
    if e30 > e50 > e100:
        return "Bullish"
    if e30 < e50 < e100:
        return "Bearish"
    return "Neutral"


def _safe_float(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(v) or not math.isfinite(v):
        return None
    return v


def _camarilla(prev_high: float, prev_low: float, prev_close: float) -> tuple[float, float]:
    rng = prev_high - prev_low
    r4 = prev_close + rng * 1.1 / 2.0
    s3 = prev_close - rng * 1.1 / 4.0
    return r4, s3


def _crossed_above(
    prev_close: float | None,
    curr_close: float | None,
    prev_level: float | None,
    curr_level: float | None,
) -> bool:
    if None in (prev_close, curr_close, prev_level, curr_level):
        return False
    return prev_close <= prev_level and curr_close > curr_level


def _crossed_below(
    prev_close: float | None,
    curr_close: float | None,
    prev_level: float | None,
    curr_level: float | None,
) -> bool:
    if None in (prev_close, curr_close, prev_level, curr_level):
        return False
    return prev_close >= prev_level and curr_close < curr_level


def _cross_flags(ind: pd.DataFrame) -> dict[str, bool]:
    """Latest-bar price cross vs EMA / SuperTrend lines (prev → last)."""
    if ind is None or len(ind) < 2:
        return {}
    last = ind.iloc[-1]
    prev = ind.iloc[-2]
    pc = _safe_float(prev["close"])
    cc = _safe_float(last["close"])
    out: dict[str, bool] = {}
    for period in (30, 50, 100):
        pe = _safe_float(prev.get(f"ema_{period}"))
        ce = _safe_float(last.get(f"ema_{period}"))
        out[f"cross_above_ema{period}"] = _crossed_above(pc, cc, pe, ce)
        out[f"cross_below_ema{period}"] = _crossed_below(pc, cc, pe, ce)
    for n in (1, 2):
        ps = _safe_float(prev.get(f"supertrend_{n}"))
        cs = _safe_float(last.get(f"supertrend_{n}"))
        out[f"cross_above_st{n}"] = _crossed_above(pc, cc, ps, cs)
        out[f"cross_below_st{n}"] = _crossed_below(pc, cc, ps, cs)
        prev_lg = _last_plotted_on_last_bullish_bar(
            ind.iloc[:-1], f"supertrend_dir_{n}", f"supertrend_{n}"
        )
        curr_lg = _last_plotted_on_last_bullish_bar(
            ind, f"supertrend_dir_{n}", f"supertrend_{n}"
        )
        out[f"cross_above_st{n}_last_green"] = _crossed_above(pc, cc, prev_lg, curr_lg)
        out[f"cross_below_st{n}_last_green"] = _crossed_below(pc, cc, prev_lg, curr_lg)
        if curr_lg is not None and cc is not None:
            out[f"above_st{n}_last_green"] = cc > curr_lg
            out[f"below_st{n}_last_green"] = cc < curr_lg
        else:
            out[f"above_st{n}_last_green"] = False
            out[f"below_st{n}_last_green"] = False
    return out


def row_from_indicator_frame(
    df: pd.DataFrame,
    *,
    symbol: str,
    name: str,
    exchange: str,
) -> dict | None:
    """Build one JSON row from the last bar of an indicator frame."""
    if df is None or len(df) < 2:
        return None

    ind = compute_indicator_frame(df)
    last = ind.iloc[-1]
    prev = ind.iloc[-2]
    close = _safe_float(last["close"])
    if close is None:
        return None

    e30 = _safe_float(last.get("ema_30"))
    e50 = _safe_float(last.get("ema_50"))
    e100 = _safe_float(last.get("ema_100"))

    prev_close = _safe_float(prev["close"])
    change = 0.0
    if prev_close and prev_close > 0:
        change = round((close / prev_close - 1.0) * 100.0, 2)

    ph = _safe_float(prev["high"]) or close
    pl = _safe_float(prev["low"]) or close
    pc = prev_close or close
    r4, s3 = _camarilla(ph, pl, pc)

    st1_lg = _last_plotted_on_last_bullish_bar(ind, "supertrend_dir_1", "supertrend_1")
    st2_lg = _last_plotted_on_last_bullish_bar(ind, "supertrend_dir_2", "supertrend_2")

    d1 = last.get("supertrend_dir_1")
    d2 = last.get("supertrend_dir_2")
    d1_i = int(d1) if d1 is not None and not pd.isna(d1) else None
    d2_i = int(d2) if d2 is not None and not pd.isna(d2) else None

    row = {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "ltp": close,
        "change": change,
        "rsi": _safe_float(last.get("RSI")),
        "adx": _safe_float(last.get("ADX")),
        "ema30": e30,
        "ema50": e50,
        "ema100": e100,
        "emaState": _ema_state(e30, e50, e100),
        "bbl": _safe_float(last.get("BBL")) or close * 0.92,
        "bbu": _safe_float(last.get("BBU")) or close * 1.08,
        "relVol": round(_safe_float(last.get("rel_vol")) or 1.0, 2),
        "r4": round(r4, 2),
        "s3": round(s3, 2),
        "supertrend_1": _safe_float(last.get("supertrend_1")),
        "supertrend_dir_1": d1_i,
        "supertrend_2": _safe_float(last.get("supertrend_2")),
        "supertrend_dir_2": d2_i,
        "st1_last_green": st1_lg,
        "st2_last_green": st2_lg,
        "pct_from_st1_last_green": _pct_distance(close, st1_lg),
        "pct_from_st2_last_green": _pct_distance(close, st2_lg),
        "pct_from_ema30": _pct_distance(close, e30),
    }
    row.update(_cross_flags(ind))
    return row


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (W-FRI) or monthly (ME)."""
    if df.empty:
        return df
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    out = df.resample(rule).agg(agg).dropna(subset=["close"])
    return out
