"""Moat, capital allocation, and decision-checklist scoring for build_governance.py."""
from __future__ import annotations


def _tier_from_score(score: int, *, strong: int = 75, review: int = 50) -> str:
    if score >= strong:
        return "strong"
    if score >= review:
        return "review"
    return "weak"


def assess_moat(rec: dict) -> dict:
    """Heuristic moat / competitive quality from fundamentals (not a moat guarantee)."""
    score = 50
    notes: list[str] = []

    roce = rec.get("roce_pct")
    if roce is not None:
        if roce >= 18:
            score += 18
            notes.append(f"ROCE {roce:.1f}% — capital efficiently deployed (moat signal)")
        elif roce >= 12:
            score += 8
        elif roce < 8:
            score -= 18
            notes.append(f"ROCE {roce:.1f}% — weak returns on capital")

    roe = rec.get("roe_pct")
    if roe is not None and roe < 8:
        score -= 8
        notes.append(f"ROE {roe:.1f}% below quality threshold")

    sg = rec.get("sales_growth_3y_pct")
    if sg is not None:
        if sg >= 15:
            score += 12
            notes.append(f"Sales CAGR 3Y {sg:.0f}% — growing franchise")
        elif sg < 0:
            score -= 10
            notes.append(f"Sales declined over 3Y ({sg:.0f}%)")

    pg = rec.get("profit_growth_3y_pct")
    if pg is not None and pg >= 15:
        score += 8
    elif pg is not None and pg < 0:
        score -= 8

    opm = rec.get("opm_pct")
    if opm is not None:
        if opm >= 15:
            score += 10
            notes.append(f"OPM {opm:.1f}% — healthy margins")
        elif opm < 5:
            score -= 10
            notes.append(f"Thin OPM {opm:.1f}% — limited pricing power")

    cfo_np = rec.get("cfo_np_ratio_3y")
    if cfo_np is not None:
        if cfo_np >= 0.9:
            score += 8
        elif cfo_np < 0.5:
            score -= 12
            notes.append(f"CFO/NP {cfo_np:.2f} — earnings quality concern")

    debt = rec.get("total_debt_cr")
    rev = rec.get("revenue_ttm_cr")
    if debt is not None and rev and rev > 0:
        de = debt / rev
        if de > 1.5:
            score -= 12
            notes.append(f"Debt ₹{debt:.0f} Cr vs revenue ₹{rev:.0f} Cr — leveraged")
        elif de < 0.3:
            score += 5

    for flag in rec.get("screener_notes") or rec.get("flags") or []:
        low = str(flag).lower()
        if "debtor" in low or "inventory" in low:
            score -= 5
        if "low return on equity" in low:
            score -= 6

    score = max(0, min(100, score))
    tier = _tier_from_score(score)
    if tier == "strong" and not notes:
        notes.append("Fundamentals support a durable business — verify sector dynamics manually")
    elif tier == "weak" and len(notes) < 2:
        notes.append("No clear moat from numbers alone — commodity / turnaround risk")

    return {"moat_score": score, "moat_tier": tier, "moat_notes": notes[:8]}


def assess_capital_allocation(rec: dict) -> dict:
    """How well management converts profit to cash and uses balance sheet."""
    score = 55
    notes: list[str] = []

    cfo_np = rec.get("cfo_np_ratio_3y")
    if cfo_np is not None:
        if cfo_np >= 1.0:
            score += 20
            notes.append(f"CFO exceeds NP (3Y avg ratio {cfo_np:.2f}) — strong cash conversion")
        elif cfo_np >= 0.75:
            score += 10
        elif cfo_np < 0.5:
            score -= 25
            notes.append(f"CFO/NP {cfo_np:.2f} — profits not backed by cash")
        elif cfo_np < 0:
            score -= 30
            notes.append("Negative CFO vs profit — red flag for capital allocation")

    fcf = rec.get("fcf_latest_cr")
    if fcf is not None:
        if fcf > 0:
            score += 8
            notes.append(f"Latest FCF ₹{fcf:.1f} Cr positive")
        else:
            score -= 10
            notes.append(f"Latest FCF ₹{fcf:.1f} Cr — cash burn / heavy capex")

    pledge = rec.get("pledge_pct")
    if pledge is not None and pledge > 25:
        score -= 20
        notes.append(f"Promoter pledge {pledge:.1f}% — distress / liquidity risk")

    prom_chg = rec.get("promoter_change_4q_pp")
    if prom_chg is not None:
        if prom_chg <= -2:
            score -= 12
            notes.append(f"Promoter holding down {abs(prom_chg):.1f}pp over ~4Q")
        elif prom_chg >= 2:
            score += 5
            notes.append(f"Promoter holding up {prom_chg:.1f}pp — skin in the game")

    div = rec.get("dividend_yield_pct")
    np = rec.get("profit_ttm_cr")
    if div is not None and div > 0.5 and np and np > 0:
        score += 5
        notes.append(f"Dividend yield {div:.2f}% while profitable")
    elif np and np > 0 and (div is None or div < 0.1):
        for note in rec.get("screener_notes") or []:
            if "not paying out dividend" in note.lower():
                score -= 5
                notes.append("Profitable but no dividend — check reinvestment vs hoarding")
                break

    debt_chg = rec.get("debt_change_3y_pct")
    if debt_chg is not None:
        if debt_chg > 40:
            score -= 10
            notes.append(f"Debt up ~{debt_chg:.0f}% over 3Y")
        elif debt_chg < -15:
            score += 8
            notes.append("De-leveraging balance sheet")

    score = max(0, min(100, score))
    tier = _tier_from_score(score)
    return {"capital_score": score, "capital_tier": tier, "capital_notes": notes[:8]}


def build_decision_checklist(rec: dict, *, val: dict | None = None) -> list[dict]:
    """Structured pass/fail/review items for the UI decision panel."""
    items: list[dict] = []

    def add(status: str, label: str, detail: str) -> None:
        items.append({"status": status, "label": label, "detail": detail})

    tier = rec.get("integrity_tier")
    if tier == "strong":
        add("pass", "Integrity screen", f"Score {rec.get('integrity_score')} — no major automated flags")
    elif tier == "weak":
        add("fail", "Integrity screen", f"Score {rec.get('integrity_score')} — review flags before sizing")
    else:
        add("review", "Integrity screen", f"Score {rec.get('integrity_score')} — mixed signals")

    moat_t = rec.get("moat_tier")
    if moat_t == "strong":
        add("pass", "Business quality / moat", f"Moat score {rec.get('moat_score')} from ROCE, growth, margins")
    elif moat_t == "weak":
        add("fail", "Business quality / moat", "Weak returns or shrinking sales — likely no moat")
    else:
        add("review", "Business quality / moat", "Average franchise — sector and competition matter")

    cap_t = rec.get("capital_tier")
    if cap_t == "strong":
        add("pass", "Cash & capital allocation", f"Capital score {rec.get('capital_score')} — cash backs earnings")
    elif cap_t == "weak":
        add("fail", "Cash & capital allocation", "Cash conversion or balance sheet concerns")
    else:
        add("review", "Cash & capital allocation", "Verify capex, working capital, and debt schedule")

    pledge = rec.get("pledge_pct")
    if pledge is None or pledge < 10:
        add("pass", "Promoter pledge", "No significant pledge reported")
    elif pledge < 25:
        add("review", "Promoter pledge", f"{pledge:.1f}% pledged — monitor")
    else:
        add("fail", "Promoter pledge", f"{pledge:.1f}% pledged — high risk")

    add("review", "Related parties (RPT)", "Manual: Annual report → Ind AS 24 related-party note")

    if val:
        gap = val.get("val_gap")
        peg = val.get("peg_fwd") or val.get("peg_trailing")
        if gap == "cheap":
            add("pass", "Valuation vs growth", f"Tagged cheap · PEG {peg if peg else '—'}")
        elif gap == "rich":
            add("review", "Valuation vs growth", f"Tagged rich — need exceptional execution")
        else:
            add("review", "Valuation vs growth", val.get("growth_trend_display") or "See valuation tab")

    return items


LEADER_KEYWORDS = (
    "market leader",
    "largest",
    "leading ",
    " sole ",
    "dominant",
    "#1",
    "only manufacturer",
    "only player",
    "number one",
    "no.1",
    "top player",
)


def _detect_market_leader(about_text: str | None, screener_notes: list | None) -> bool:
    blob = " ".join([about_text or "", *(screener_notes or [])]).lower()
    return any(k in blob for k in LEADER_KEYWORDS)


def _infer_moat_tier(rec: dict) -> str:
    if rec.get("moat_tier"):
        return str(rec["moat_tier"])
    return assess_moat(rec).get("moat_tier", "review")


def _infer_capital_tier(rec: dict) -> str:
    if rec.get("capital_tier"):
        return str(rec["capital_tier"])
    return assess_capital_allocation(rec).get("capital_tier", "review")


def assess_track_verdict(
    rec: dict,
    *,
    val: dict | None = None,
    sector_ctx: dict | None = None,
) -> dict:
    """
    track  — worth deep research / tracking
    review — borderline; needs manual call
    skip   — weak fundamentals with no moat / sector / leader rescue — don't waste time
    """
    skip_reasons: list[str] = []
    rescue_reasons: list[str] = []

    integrity_score = int(rec.get("integrity_score") or 0)
    integrity_tier = rec.get("integrity_tier") or "review"
    moat_tier = _infer_moat_tier(rec)
    capital_tier = _infer_capital_tier(rec)
    moat_score = int(rec.get("moat_score") or assess_moat(rec).get("moat_score") or 0)

    pledge = rec.get("pledge_pct")
    cfo_np = rec.get("cfo_np_ratio_3y")
    roce = rec.get("roce_pct")
    profit = rec.get("profit_ttm_cr")

    if pledge is not None and float(pledge) > 50:
        skip_reasons.append(f"Promoter pledge {float(pledge):.0f}% — distress risk")
    if pledge is not None and float(pledge) > 25:
        skip_reasons.append(f"Elevated pledge {float(pledge):.0f}%")

    if cfo_np is not None and float(cfo_np) < 0.3:
        skip_reasons.append(f"CFO/NP {float(cfo_np):.2f} — earnings not cash-backed")
    elif cfo_np is not None and float(cfo_np) < 0.5:
        skip_reasons.append(f"Weak cash conversion (CFO/NP {float(cfo_np):.2f})")

    if integrity_tier == "weak" or integrity_score < 45:
        skip_reasons.append(f"Integrity weak (score {integrity_score})")

    if moat_tier == "weak" and (roce is None or float(roce) < 10):
        skip_reasons.append("No moat signal — low ROCE / weak franchise")

    if capital_tier == "weak":
        skip_reasons.append("Poor capital allocation / cash quality")

    if profit is not None and float(profit) < 0:
        skip_reasons.append("Loss-making (TTM NP negative)")

    for flag in rec.get("flags") or []:
        low = str(flag).lower()
        if any(k in low for k in ("fraud", "default", "penalty", "negative net worth")):
            skip_reasons.append(str(flag)[:120])

    # Rescue: moat, growing sector, market leader
    about = rec.get("about_text") or ""
    notes = rec.get("screener_notes") or rec.get("flags") or []
    market_leader = bool(rec.get("market_leader")) or _detect_market_leader(about, notes)
    if market_leader:
        rescue_reasons.append("Market leader / dominant player (from Screener about)")

    if moat_tier == "strong" or moat_score >= 75:
        rescue_reasons.append(f"Moat score {moat_score} — durable business signals")

    sec = sector_ctx or rec.get("sector") or {}
    sg_tier = sec.get("sector_growth_tier")
    med_sg = sec.get("median_sales_growth_3y_pct")
    if sg_tier == "growing" or (med_sg is not None and float(med_sg) >= 10):
        rescue_reasons.append(
            f"Sector tailwind ({sec.get('sub_tag') or sec.get('industry') or 'peer group'}"
            f"{f', median sales +{med_sg:.0f}% 3Y' if med_sg is not None else ''})"
        )
    elif sg_tier == "stable" and med_sg is not None and float(med_sg) >= 5:
        rescue_reasons.append(f"Stable sector ({sec.get('sub_tag') or 'industry'})")

    if val:
        if val.get("growth_trend") == "accelerating":
            rescue_reasons.append("Accelerating profit growth (quarterly)")
        if val.get("val_gap") == "cheap" and (val.get("growth_3y_pct") or 0) >= 10:
            rescue_reasons.append("Cheap vs growth (valuation screen)")

    fundamental_strong = (
        integrity_score >= 70
        and integrity_tier != "weak"
        and capital_tier != "weak"
        and (cfo_np is None or float(cfo_np) >= 0.6)
        and (pledge is None or float(pledge) < 25)
    )

    fundamental_ok = (
        integrity_score >= 58
        and integrity_tier != "weak"
        and moat_tier != "weak"
        and capital_tier != "weak"
    )

    has_rescue = bool(rescue_reasons)
    hard_skip = (
        integrity_score < 40
        or (pledge is not None and float(pledge) > 50)
        or (cfo_np is not None and float(cfo_np) < 0 and integrity_tier == "weak")
    )

    if fundamental_strong:
        verdict = "track"
        tag = "Fundamentally strong"
    elif fundamental_ok:
        verdict = "track"
        tag = "Good fundamentals"
    elif has_rescue and not hard_skip:
        if integrity_tier == "weak" or moat_tier == "weak":
            verdict = "review"
            tag = "Rescue only — moat / sector / leader"
        else:
            verdict = "track"
            tag = "Moat or sector rescue"
    elif hard_skip or (len(skip_reasons) >= 3 and not has_rescue):
        verdict = "skip"
        tag = "Don't track — waste of time"
    elif skip_reasons and not has_rescue:
        verdict = "skip"
        tag = "Weak — no moat or sector support"
    else:
        verdict = "review"
        tag = "Borderline — manual review"

    return {
        "track_verdict": verdict,
        "track_tag": tag,
        "market_leader": market_leader,
        "skip_reasons": skip_reasons[:8],
        "rescue_reasons": rescue_reasons[:6],
    }


def _valuation_score(val: dict | None) -> float:
    """0–100 sub-score from valuation.json (PEG, gap, quarterly trend)."""
    if not val:
        return 45.0
    score = 50.0
    gap = val.get("val_gap")
    if gap == "cheap":
        score += 22
    elif gap == "fair":
        score += 5
    elif gap == "rich":
        score -= 18
    elif gap == "unknown":
        score -= 5

    peg = val.get("peg_forward") or val.get("peg_trailing")
    if peg is not None:
        p = float(peg)
        if p < 0.8:
            score += 12
        elif p < 1.2:
            score += 8
        elif p < 1.8:
            score += 2
        elif p > 3:
            score -= 12
        elif p > 2.2:
            score -= 6

    gt = val.get("growth_trend")
    if gt == "accelerating":
        score += 10
    elif gt == "decelerating":
        score -= 10
    elif gt == "stable":
        score += 3
    if val.get("yoy_off_peak"):
        score -= 6

    yoys = [
        float(h["yoy_pct"])
        for h in (val.get("quarterly_yoy_history") or [])
        if h.get("yoy_pct") is not None
    ]
    if len(yoys) >= 3:
        neg = sum(1 for y in yoys if y < 0)
        span = max(yoys) - min(yoys)
        if neg == 0 and span <= 18:
            score += 10
        elif neg >= 2:
            score -= 14
        elif neg == 1:
            score -= 5

    g3 = val.get("growth_3y_pct")
    if g3 is not None and float(g3) >= 15:
        score += 4
    elif g3 is not None and float(g3) < 0:
        score -= 8

    return max(0.0, min(100.0, score))


def _research_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 68:
        return "B"
    if score >= 55:
        return "C"
    if score >= 42:
        return "D"
    return "F"


def compute_research_score(rec: dict, *, val: dict | None = None) -> dict:
    """
    Composite 0–100 score to rank the full ₹100–1,000 Cr band best → worst.
    Higher = better candidate for deep research (not a buy recommendation).
    """
    integrity = float(rec.get("integrity_score") or 50)
    moat = float(rec.get("moat_score") or assess_moat(rec).get("moat_score") or 50)
    capital = float(rec.get("capital_score") or assess_capital_allocation(rec).get("capital_score") or 50)
    val_sub = _valuation_score(val)

    data_penalty = 0.0
    if rec.get("cfo_np_ratio_3y") is None:
        data_penalty += 8.0
    if rec.get("roce_pct") is None:
        data_penalty += 4.0

    raw = integrity * 0.28 + moat * 0.28 + capital * 0.22 + val_sub * 0.22
    raw -= data_penalty

    if rec.get("market_leader"):
        raw += 3.0
    sec = rec.get("sector") or {}
    if sec.get("sector_growth_tier") == "growing":
        raw += 2.0

    pledge = rec.get("pledge_pct")
    if pledge is not None:
        p = float(pledge)
        if p > 50:
            raw -= 25.0
        elif p > 25:
            raw -= 12.0
        elif p > 10:
            raw -= 4.0

    skip_n = len(rec.get("skip_reasons") or [])
    raw -= min(skip_n * 4.0, 20.0)

    verdict = rec.get("track_verdict")
    if verdict == "skip":
        raw = min(raw, 42.0)
    elif verdict == "review":
        raw = min(raw, 78.0)

    score = round(max(0.0, min(100.0, raw)), 1)
    grade = _research_grade(score)

    return {
        "research_score": score,
        "research_grade": grade,
        "research_score_parts": {
            "integrity": round(integrity, 1),
            "moat": round(moat, 1),
            "capital": round(capital, 1),
            "valuation": round(val_sub, 1),
            "data_penalty": round(data_penalty, 1),
        },
    }


def assign_research_ranks(companies: dict) -> int:
    """Rank all non-error rows by research_score descending (#1 = best)."""
    rows: list[tuple[str, dict, float]] = []
    for sym, rec in companies.items():
        if rec.get("error"):
            continue
        s = float(rec.get("research_score") or 0)
        rows.append((str(sym).upper(), rec, s))

    rows.sort(key=lambda x: (-x[2], x[0]))
    total = len(rows)
    for rank, (_sym, rec, _score) in enumerate(rows, start=1):
        rec["research_rank"] = rank
        rec["research_rank_total"] = total
        if total <= 1:
            rec["research_percentile"] = 100.0
        else:
            rec["research_percentile"] = round(100.0 * (total - rank) / (total - 1), 1)
    return total


def enrich_governance_record(
    rec: dict,
    *,
    val: dict | None = None,
    sector_tag: dict | None = None,
    sector_ctx: dict | None = None,
) -> dict:
    """Attach moat, capital, sector, and track verdict to a governance row."""
    if rec.get("error"):
        return rec
    rec.update(assess_moat(rec))
    rec.update(assess_capital_allocation(rec))
    if sector_ctx:
        rec["sector"] = sector_ctx
    elif sector_tag:
        rec["sector"] = {
            "broad_sector": sector_tag.get("broad_sector"),
            "sector": sector_tag.get("sector"),
            "industry": sector_tag.get("industry"),
            "sub_tag": sector_tag.get("sub_tag"),
            "group_path": sector_tag.get("group_path"),
        }
    rec.update(assess_track_verdict(rec, val=val, sector_ctx=rec.get("sector")))
    rec.update(compute_research_score(rec, val=val))
    rec["decision_checklist"] = build_decision_checklist(rec, val=val)
    return rec
