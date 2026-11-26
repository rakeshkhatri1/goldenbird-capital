"""Aggregate peer growth/quality stats by Screener industry tag."""
from __future__ import annotations

from statistics import median


def _num(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build_sector_peer_index(
    sector_tags: dict[str, dict],
    *,
    valuation: dict[str, dict] | None = None,
    governance: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """
    Key = sub_tag (fallback industry). Values = peer medians for sector growth context.
    """
    groups: dict[str, list[str]] = {}
    for sym, tag in sector_tags.items():
        key = (tag.get("sub_tag") or tag.get("industry") or tag.get("sector") or "").strip()
        if not key:
            continue
        groups.setdefault(key, []).append(str(sym).upper())

    val = valuation or {}
    gov = governance or {}
    out: dict[str, dict] = {}

    for key, syms in groups.items():
        sales_g: list[float] = []
        profit_g: list[float] = []
        roces: list[float] = []
        for sym in syms:
            v = val.get(sym) or {}
            g = gov.get(sym) or {}
            sg = _num(v.get("growth_3y_pct")) or _num(g.get("sales_growth_3y_pct"))
            pg = _num(v.get("growth_3y_pct")) if v.get("growth_3y_pct") is not None else _num(g.get("profit_growth_3y_pct"))
            rc = _num(g.get("roce_pct")) or _num(v.get("roce_pct"))
            if sg is not None:
                sales_g.append(sg)
            if pg is not None:
                profit_g.append(pg)
            if rc is not None:
                roces.append(rc)

        med_sales = round(median(sales_g), 1) if sales_g else None
        med_profit = round(median(profit_g), 1) if profit_g else None
        med_roce = round(median(roces), 1) if roces else None

        if med_sales is not None:
            if med_sales >= 12:
                growth_tier = "growing"
            elif med_sales >= 3:
                growth_tier = "stable"
            elif med_sales >= 0:
                growth_tier = "slow"
            else:
                growth_tier = "shrinking"
        else:
            growth_tier = "unknown"

        wind_fields = _sector_wind_fields(growth_tier, med_sales)

        out[key] = {
            "sub_tag": key,
            "peer_count": len(syms),
            "median_sales_growth_3y_pct": med_sales,
            "median_profit_growth_3y_pct": med_profit,
            "median_roce_pct": med_roce,
            "sector_growth_tier": growth_tier,
            **wind_fields,
        }
    return out


def _sector_wind_fields(growth_tier: str | None, med_sales: float | None) -> dict:
    """Map peer median sales growth to tailwind / headwind labels for UI."""
    tier = growth_tier or "unknown"
    med = med_sales
    if tier == "growing" or (med is not None and med >= 12):
        return {"sector_wind": "tailwind", "sector_wind_label": "Tailwind"}
    if tier == "shrinking" or (med is not None and med < 0):
        return {"sector_wind": "headwind", "sector_wind_label": "Headwind"}
    if tier == "slow" or (med is not None and 0 <= med < 3):
        return {"sector_wind": "headwind", "sector_wind_label": "Mild headwind"}
    if tier == "stable" or (med is not None and 3 <= med < 12):
        return {"sector_wind": "neutral", "sector_wind_label": "Neutral"}
    return {"sector_wind": "unknown", "sector_wind_label": "Unknown"}


def attach_sector_context(
    row: dict,
    sector_tag: dict | None,
    peer_index: dict[str, dict],
) -> None:
    if not sector_tag:
        return
    key = (sector_tag.get("sub_tag") or sector_tag.get("industry") or "").strip()
    peers = peer_index.get(key) or {}
    row["sector"] = {
        "broad_sector": sector_tag.get("broad_sector"),
        "sector": sector_tag.get("sector"),
        "broad_industry": sector_tag.get("broad_industry"),
        "industry": sector_tag.get("industry"),
        "sub_tag": sector_tag.get("sub_tag"),
        "group_path": sector_tag.get("group_path"),
        "peer_count": peers.get("peer_count"),
        "median_sales_growth_3y_pct": peers.get("median_sales_growth_3y_pct"),
        "median_profit_growth_3y_pct": peers.get("median_profit_growth_3y_pct"),
        "median_roce_pct": peers.get("median_roce_pct"),
        "sector_growth_tier": peers.get("sector_growth_tier"),
        "sector_wind": peers.get("sector_wind"),
        "sector_wind_label": peers.get("sector_wind_label"),
    }
