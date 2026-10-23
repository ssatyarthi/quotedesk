"""
Award optimizer (PRD R5.6, Decisions 1 and 3).

Deterministic: given the normalized quotes, pick a split that covers as many
lines as possible at the lowest total, using at most `max_vendors` vendors.
With 5 vendors every combination can be tried exactly (at most 31), so the
answer is the true optimum, not a heuristic.

The buyer controls the rules; the code never decides silently:
  * eligibility  - which quality statuses may win (FAILED never does by default;
                   UNRESOLVED may, carrying a warning)
  * price basis  - landed price, or landed + value of payment terms
  * vendor cap   - default 3 (see PRD decision 2)
  * conditional discounts - applied only when the vendor's own threshold is met
                   by the award itself
Low-confidence prices never win until the buyer confirms them.
"""
from itertools import combinations

import pandas as pd

ELIGIBILITY = {
    "qualified": ("QUALIFIED",),
    "qualified_plus_unresolved": ("QUALIFIED", "UNRESOLVED"),
    "all": ("QUALIFIED", "UNRESOLVED", "FAILED"),
}
ELIGIBILITY_LABEL = {
    "qualified": "Qualified vendors only",
    "qualified_plus_unresolved": "Qualified + unresolved (with warning)",
    "all": "All vendors, including failed quality",
}
PRICE_BASIS = {"effective": "effective_unit_price_inr", "landed": "landed_unit_price_inr"}


def _eligible(tables, eligibility, price_basis):
    q = tables["quotes"]
    if q.empty:
        return q
    col = PRICE_BASIS[price_basis]
    if col not in q:
        col = "landed_unit_price_inr"
    d = q[(q["status"] == "quoted") & (~q["is_alternate"]) & q[col].notna() & (q["confidence"] != "low")].copy()
    d = d[d["quality_status"].isin(ELIGIBILITY[eligibility])]
    d["price"] = d[col]
    # what a conditional discount would do to this vendor's price (same reduction applied on the chosen basis)
    if "landed_if_cond_discount" in d:
        cut = (d["landed_unit_price_inr"] - d["landed_if_cond_discount"]).fillna(0)
        d["price_if_cond"] = d["price"] - cut
    else:
        d["price_if_cond"] = d["price"]
    return d


def _assign(d, combo, discounted):
    sub = d[d["vendor_id"].isin(combo)].copy()
    sub["p"] = [r.price_if_cond if r.vendor_id in discounted else r.price for r in sub.itertuples()]
    if sub.empty:
        return sub
    return sub.loc[sub.groupby("line_id")["p"].idxmin()]


def _evaluate(d, combo, vendors, apply_cond):
    cond = {}
    if apply_cond and not vendors.empty and "conditional_discount_pct" in vendors:
        for r in vendors.itertuples():
            if r.vendor_id in combo and pd.notna(getattr(r, "conditional_discount_pct", None)):
                cond[r.vendor_id] = getattr(r, "conditional_discount_threshold_inr", None)
    discounted = set(cond)
    # Apply a vendor's conditional discount only if the award it receives (at discounted prices) meets its threshold.
    for _ in range(3):
        pick = _assign(d, combo, discounted)
        if pick.empty:
            return pick, set()
        won = (pick["p"] * pick["annual_qty"]).groupby(pick["vendor_id"]).sum()
        still = {v for v in discounted if (cond[v] is None or pd.isna(cond[v])) or won.get(v, 0) >= float(cond[v])}
        if still == discounted:
            return pick, discounted
        discounted = still
    return _assign(d, combo, discounted), discounted


def optimize(tables, max_vendors=3, eligibility="qualified_plus_unresolved", price_basis="effective",
             apply_conditional_discounts=False):
    d = _eligible(tables, eligibility, price_basis)
    lines = tables["lines"]
    all_lines = list(lines["line_id"])
    vendors = tables["vendors"]
    names = dict(zip(vendors["vendor_id"], vendors["vendor_name"])) if not vendors.empty else {}
    status = dict(zip(vendors["vendor_id"], vendors.get("quality_status", pd.Series(dtype=str)))) if not vendors.empty else {}
    pool = sorted(d["vendor_id"].unique()) if not d.empty else []
    cap = len(pool) if not max_vendors else min(int(max_vendors), len(pool))
    best = None
    for k in range(1, cap + 1):
        for combo in combinations(pool, k):
            pick, disc = _evaluate(d, combo, vendors, apply_conditional_discounts)
            if pick.empty:
                continue
            covered = pick["line_id"].nunique()
            total = float((pick["p"] * pick["annual_qty"]).sum())
            used = pick["vendor_id"].nunique()
            key = (-covered, round(total, 0), used)
            if best is None or key < best[0]:
                best = (key, pick, disc)
    if best is None:
        return dict(ok=False, message="No eligible prices under these rules.", uncovered=all_lines)
    _, pick, disc = best
    pick = pick.copy()
    pick["annual_inr"] = (pick["p"] * pick["annual_qty"]).round(0)
    ly = lines.set_index("line_id")
    ly_total_covered = float((ly.loc[pick["line_id"], "last_year_price_inr"] * ly.loc[pick["line_id"], "annual_qty"]).sum())
    uncovered = [l for l in all_lines if l not in set(pick["line_id"])]
    split = (pick.groupby("vendor_id").agg(lines=("line_id", "count"), annual_inr=("annual_inr", "sum"))
             .reset_index().sort_values("annual_inr", ascending=False))
    split["vendor_name"] = split["vendor_id"].map(names)
    split["quality_status"] = split["vendor_id"].map(status)
    split["conditional_discount_applied"] = split["vendor_id"].isin(disc)
    total = float(pick["annual_inr"].sum())
    warnings = []
    for v in split.itertuples():
        if v.quality_status == "UNRESOLVED":
            warnings.append(f"{v.vendor_name} is UNRESOLVED on the quality questionnaire; resolve before award (clarification email).")
        if v.quality_status == "FAILED":
            warnings.append(f"{v.vendor_name} FAILED a mandatory quality question.")
    med = pick[pick["confidence"].isin(["medium"])]
    if not med.empty:
        warnings.append(f"{len(med)} awarded prices are medium confidence (e.g. per-kg conversions, inherited prices); see Review queue.")
    if uncovered:
        warnings.append(f"{len(uncovered)} lines have no eligible price under these rules: {', '.join(uncovered)}.")
    lines_out = pick[["line_id", "vendor_id", "vendor_name", "p", "annual_qty", "annual_inr", "confidence"]].rename(
        columns={"p": "unit_price_inr"}).sort_values("line_id")
    return dict(ok=True, max_vendors=max_vendors, eligibility=eligibility, price_basis=price_basis,
                apply_conditional_discounts=apply_conditional_discounts,
                total_inr=round(total, 0), last_year_same_lines_inr=round(ly_total_covered, 0),
                vs_last_year_pct=round((total / ly_total_covered - 1) * 100, 2) if ly_total_covered else None,
                lines_covered=len(all_lines) - len(uncovered), lines_total=len(all_lines), uncovered=uncovered,
                split=split, lines=lines_out, warnings=warnings, vendors_considered=pool)


def scenarios(tables, price_basis="effective", apply_conditional_discounts=False):
    rows = []
    for elig in ("qualified", "qualified_plus_unresolved"):
        for cap in (1, 2, 3, None):
            r = optimize(tables, cap, elig, price_basis, apply_conditional_discounts)
            if not r["ok"]:
                rows.append(dict(eligibility=ELIGIBILITY_LABEL[elig], max_vendors=str(cap or "no cap"), lines_covered=0))
                continue
            rows.append(dict(eligibility=ELIGIBILITY_LABEL[elig], max_vendors=str(cap or "no cap"),
                             vendors_used=", ".join(r["split"]["vendor_name"]),
                             lines_covered=f"{r['lines_covered']}/{r['lines_total']}",
                             total_inr=r["total_inr"], vs_last_year_pct=r["vs_last_year_pct"]))
    return pd.DataFrame(rows)
