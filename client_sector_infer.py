"""Infer end-market / client-sector exposure from Screener about text and revenue segments."""
from __future__ import annotations

import re

# (keywords, end_market_label) — order matters; first match wins per chunk
END_MARKET_RULES: list[tuple[tuple[str, ...], str]] = [
    (("defence", "defense", "aerospace", "drdo", "military"), "Defense & Aerospace"),
    (("banking", "nbfc", "bfsi", "financial institution", "insurance", "mutual fund"), "BFSI"),
    (("automobile", " automotive", "auto oem", " tyre", "two-wheeler", "ev "), "Automotive"),
    (("real estate", "housing", "construction finance", "affordable housing", "builder"), "Real Estate & Construction"),
    (("infrastructure", " epc", "road", "bridge", "metro", "railway", "power transmission"), "Infrastructure & EPC"),
    (("power ", "renewable", "solar", "wind energy", "utility"), "Power & Utilities"),
    (("oil ", "gas ", "refinery", "petrochemical", "upstream", "downstream"), "Oil & Gas"),
    (("pharma", "healthcare", "hospital", "api ", "formulation"), "Pharma & Healthcare"),
    (("fmcg", "consumer goods", "retail", "e-commerce", "quick commerce"), "FMCG & Retail"),
    (("telecom", "5g", "network equipment", "tower"), "Telecom"),
    (("it services", "software", "digital transformation", "cloud", "saas"), "IT & Software"),
    (("msme", "sme ", "small business"), "MSME"),
    (("government", "psu", "public sector"), "Government / PSU"),
    (("export", "overseas", "international market", "global client"), "Exports / Global"),
    (("steel", "metal", "mining", "cement", "mineral"), "Metals, Mining & Cement"),
    (("agri", "fertilizer", "crop", "rural"), "Agriculture & Rural"),
    (("textile", "garment", "apparel"), "Textiles"),
    (("hotel", "hospitality", "travel", "airline"), "Travel & Hospitality"),
    (("education", "edtech"), "Education"),
    (("media", "entertainment", "broadcast"), "Media & Entertainment"),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def infer_end_markets(
    about_text: str,
    *,
    industry: str | None = None,
    revenue_segments: list[dict] | None = None,
) -> list[dict]:
    """
    Return ranked end-market exposures: {end_market, confidence, source, evidence}.
    confidence: high (segment %), medium (explicit mention), low (keyword).
    """
    hits: dict[str, dict] = {}
    about = _normalize(about_text)
    industry_n = _normalize(industry or "")

    for keywords, label in END_MARKET_RULES:
        for kw in keywords:
            if kw.strip() in about or kw.strip() in industry_n:
                prev = hits.get(label)
                if not prev or prev["confidence"] == "low":
                    hits[label] = {
                        "end_market": label,
                        "confidence": "medium" if kw.strip() in about else "low",
                        "source": "about_text" if kw.strip() in about else "industry",
                        "evidence": kw.strip(),
                    }
                break

    for seg in revenue_segments or []:
        name = _normalize(str(seg.get("name") or seg.get("segment") or ""))
        pct = seg.get("pct")
        if not name:
            continue
        matched_label = None
        for keywords, label in END_MARKET_RULES:
            if any(kw.strip() in name for kw in keywords):
                matched_label = label
                break
        if not matched_label:
            matched_label = seg.get("name") or seg.get("segment") or "Other"
        hits[str(matched_label)] = {
            "end_market": str(matched_label),
            "weight_pct": pct,
            "confidence": "high" if pct is not None else "medium",
            "source": "revenue_segment",
            "evidence": seg.get("name") or seg.get("segment"),
        }

    out = list(hits.values())
    out.sort(
        key=lambda x: (
            {"high": 0, "medium": 1, "low": 2}.get(str(x.get("confidence")), 3),
            -(x.get("weight_pct") or 0),
        ),
    )
    return out[:10]


def summarize_client_dependency(
    end_markets: list[dict],
    about_text: str,
) -> dict:
    """Short summary for UI: concentration and dependency narrative."""
    about = _normalize(about_text)
    concentrated = any(
        kw in about
        for kw in (
            "single customer",
            "top customer",
            "major customer",
            "customer concentration",
            "one client",
        )
    )
    export_heavy = any(e.get("end_market") == "Exports / Global" for e in end_markets)
    n = len(end_markets)
    if n == 0:
        tier = "unknown"
        summary = "End-market exposure not parsed — read About / concall / annual report segment note."
    elif n == 1:
        tier = "concentrated"
        summary = f"Revenue appears tied mainly to {end_markets[0]['end_market']}."
    elif n <= 3:
        tier = "moderate"
        labels = ", ".join(e["end_market"] for e in end_markets[:3])
        summary = f"Diversified across ~{n} end-markets: {labels}."
    else:
        tier = "diversified"
        summary = f"Broad end-market mix ({n} segments inferred from business description)."

    if concentrated:
        summary += " ⚠ Text suggests customer concentration — verify top-client % in AR."
    if export_heavy:
        summary += " Significant export/global exposure."

    return {
        "dependency_tier": tier,
        "dependency_summary": summary,
        "customer_concentration_flag": concentrated,
    }
