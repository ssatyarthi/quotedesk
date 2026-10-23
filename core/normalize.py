"""
Deterministic normalization: everything after the model has read the documents.

Model output (vendor's own units/currency) -> Rs per RFx unit, ex-works and
landed, with every step written out in plain words, plus flags for anything a
buyer should look at before trusting a number.

Nothing here calls a model, so the same inputs always give the same numbers.
"""
import json
import re
from dataclasses import asdict
from datetime import date

import pandas as pd

from . import config
from .config import Assumptions

SEV_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def load_history():
    if not config.HISTORY.exists():
        return {}
    df = pd.read_csv(config.HISTORY)
    return {r.line_id: r._asdict() for r in df.itertuples(index=False)}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _fmt(x, d=2):
    return f"{x:,.{d}f}"


class Flags:
    def __init__(self, decisions):
        self.rows = []
        self.decisions = decisions

    def add(self, vendor_id, line_id, severity, category, message, source=None):
        # High-severity issues are decided line by line; the rest are decided once per vendor+category
        # (e.g. "all 20 per-kg conversions for Bharat") so the review queue stays short.
        key = f"{vendor_id}|{line_id or '-'}|{category}" if severity == "high" else f"{vendor_id}|*|{category}"
        dec = self.decisions.get(key, {})
        self.rows.append(dict(flag_key=key, vendor_id=vendor_id, line_id=line_id, severity=severity,
                              category=category, message=message,
                              source_file=(source or {}).get("file", ""),
                              source_location=(source or {}).get("location", ""),
                              source_text=(source or {}).get("text", ""),
                              status=dec.get("status", "open"), resolution_note=dec.get("note", "")))


def _cmp(op, a, b):
    return {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b, "=": a == b}[op]


def evaluate_questionnaire(rfx, vid, ext, assumptions, flags):
    award = date.fromisoformat(assumptions.award_date)
    answers = {q.get("q_id"): q for q in ext.get("questionnaire", [])}
    atts = ext.get("attachments", [])
    rows = []
    for q in rfx["questionnaire"]:
        a = answers.get(q["q_id"], {"answered": False})
        result, reason = "", ""
        if not a.get("answered"):
            result, reason = "NOT_ANSWERED", "Vendor did not address this question"
        elif q["kind"] == "yes_no":
            b = a.get("answer_bool")
            if b is False:
                result, reason = "FAIL", a.get("answer_text", "")
            elif b is None:
                result, reason = "UNCLEAR", a.get("answer_text", "")
            else:
                result, reason = "PASS", a.get("answer_text", "")
                if "certif" in q["question"].lower():
                    std = re.findall(r"ISO\s*\d+", q["question"])
                    key = std[0].replace(" ", "").lower() if std else "certif"
                    certs = [t for t in atts if key in (str(t.get("doc_type", "")) + str(t.get("standard_or_scope", ""))).replace(" ", "").lower()]
                    if not certs:
                        result, reason = "CLAIMED", f"Says yes but no certificate attached ({a.get('answer_text','')})"
                    else:
                        valid = [c for c in certs if c.get("valid_until") and date.fromisoformat(c["valid_until"][:10]) >= award]
                        if valid:
                            reason = f"Certificate {valid[0].get('reference_no','')} valid until {valid[0]['valid_until']}"
                        else:
                            exp = certs[0].get("valid_until") or "unknown date"
                            result, reason = "CONTRADICTED", f"Says certified, but attached certificate expired {exp}"
        elif q["kind"] == "number":
            n = a.get("answer_number")
            m = re.match(r"\s*(>=|<=|>|<|=)\s*([\d.]+)", q.get("pass_rule", ""))
            if n is None:
                result, reason = "UNCLEAR", a.get("answer_text", "")
            elif m:
                ok = _cmp(m.group(1), float(n), float(m.group(2)))
                result = "PASS" if ok else ("FAIL" if q["mandatory"] else "BELOW_PREFERENCE")
                reason = f"{n:g} {q.get('unit','')} (rule {q['pass_rule']})"
            else:
                result, reason = "INFO", f"{n:g} {q.get('unit','')}"
        else:
            result, reason = "INFO", a.get("answer_text", "")
        if not q["mandatory"] and result in ("FAIL",):
            result = "BELOW_PREFERENCE"
        rows.append(dict(vendor_id=vid, q_id=q["q_id"], question=q["question"], mandatory=q["mandatory"],
                         pass_rule=q.get("pass_rule", ""), answer=a.get("answer_text", ""), evidence=a.get("evidence", ""),
                         result=result, reason=reason, source=json.dumps(a.get("source", {}))))
        if q["mandatory"] and result in ("FAIL", "CONTRADICTED", "NOT_ANSWERED", "UNCLEAR"):
            sev = "high" if result in ("FAIL", "CONTRADICTED") else "medium"
            flags.add(vid, None, sev, f"quality:{q['q_id']}", f"{q['q_id']} {result.replace('_',' ').lower()}: {reason}", a.get("source"))
    mand = [r for r in rows if r["mandatory"]]
    if any(r["result"] == "FAIL" for r in mand):
        status = "FAILED"
    elif any(r["result"] in ("CONTRADICTED", "NOT_ANSWERED", "UNCLEAR") for r in mand):
        status = "UNRESOLVED"
    else:
        status = "QUALIFIED"
    return rows, status


def build(rfx, extractions: dict, manifest: list, assumptions: Assumptions | None = None):
    A = assumptions or Assumptions()
    history = load_history()
    overrides = load_json(config.OVERRIDES, {})
    flags = Flags(load_json(config.DECISIONS, {}))
    L = {l["line_id"]: l for l in rfx["lines"]}
    names = {m["vendor_id"]: m["vendor_name"] for m in manifest}

    lines_df = pd.DataFrame(rfx["lines"])
    lines_df["last_year_price_inr"] = lines_df["line_id"].map(lambda x: history.get(x, {}).get("price_per_uom"))
    lines_df["last_year_basis"] = lines_df["line_id"].map(lambda x: history.get(x, {}).get("basis"))

    q_rows, vendor_rows, att_rows, disc_rows, quote_rows = [], [], [], [], []

    for m in manifest:
        vid = m["vendor_id"]
        ext = extractions.get(vid)
        if not ext:
            vendor_rows.append(dict(vendor_id=vid, vendor_name=names[vid], channel=m["channel"], received_on=m["received_on"],
                                    read_status="not read yet"))
            continue
        if ext.get("_truncated"):
            flags.add(vid, None, "high", "extraction_truncated", "The model's output was cut off; some lines may be missing. Re-read this vendor.")
        terms = ext.get("terms", {}) or {}
        qrows, qstatus = evaluate_questionnaire(rfx, vid, ext, A, flags)
        q_rows += qrows

        # attachments
        for t in ext.get("attachments", []):
            vu = t.get("valid_until")
            st = ""
            if vu:
                try:
                    st = "valid" if date.fromisoformat(vu[:10]) >= date.fromisoformat(A.award_date) else "EXPIRED"
                except ValueError:
                    st = "date unreadable"
            att_rows.append(dict(vendor_id=vid, file=t.get("file"), doc_type=t.get("doc_type"), holder=t.get("holder", ""),
                                 reference_no=t.get("reference_no", ""), valid_until=vu, validity=st, key_facts=t.get("key_facts", "")))

        # discounts
        uncond_pct = 0.0
        cond = None
        early = None
        for dsc in ext.get("discounts", []):
            disc_rows.append(dict(vendor_id=vid, **{k: dsc.get(k) for k in ("description", "pct", "condition_type", "threshold_inr", "condition_text", "applies_to")},
                                  source_text=(dsc.get("source") or {}).get("text", "")))
            if dsc.get("pct") is None:
                flags.add(vid, None, "medium", "discount_unquantified", f"Discount mentioned without a % : {dsc.get('description')}", dsc.get("source"))
            elif dsc.get("condition_type") == "unconditional":
                uncond_pct += float(dsc["pct"])
            elif dsc.get("condition_type") == "early_payment":
                early = dsc
                flags.add(vid, None, "low", "early_payment_discount",
                          f"{dsc['pct']}% off for paying within {dsc.get('early_payment_days') or '?'} days. "
                          f"Compared with the value of holding cash at {A.cost_of_money_pct}%/yr; the cheaper option is used.", dsc.get("source"))
            else:
                cond = dsc
                flags.add(vid, None, "medium", "conditional_discount",
                          f"{dsc['pct']}% discount only if: {dsc.get('condition_text') or dsc.get('condition_type')}. "
                          f"Not applied by default (toggle in settings).", dsc.get("source"))

        freight = terms.get("freight", "not_stated")
        if freight == "extra":
            flags.add(vid, None, "medium", "freight_extra",
                      f"Freight extra. Landed price adds the buyer's assumption of Rs {A.freight_inr_per_kg}/kg x RFx weight.", terms.get("source"))
        elif freight == "not_stated":
            flags.add(vid, None, "medium", "freight_not_stated",
                      "Freight not stated. Treated as delivered (the RFx basis) - confirm with vendor.", terms.get("source"))
        foreign = sorted({(q.get("price_currency") or "INR").upper() for q in ext.get("line_quotes", [])
                          if q.get("price_value") is not None} - {"INR"})
        if foreign:  # only a real foreign-currency quote is an FX exposure, not a sentence saying "all prices in INR"
            flags.add(vid, None, "medium", "fx_exposure",
                      f"Quoted in {', '.join(foreign)}. FX clause: {terms.get('fx_clause_text') or 'not stated'}", terms.get("source"))
        pdays = terms.get("payment_terms_days")
        ref = A.reference_payment_days
        if pdays is None:
            flags.add(vid, None, "medium", "payment_terms_unstated",
                      f"Payment terms not stated. Assumed the RFx's {ref} days - confirm with vendor.", terms.get("source"))
        elif pdays < ref:
            flags.add(vid, None, "low", "payment_terms",
                      f"Payment {pdays:g} days vs {ref} asked: paying {ref - pdays:g} days earlier costs about "
                      f"{A.cost_of_money_pct * (ref - pdays) / 365:.2f}% of value at {A.cost_of_money_pct}%/yr cost of money.", terms.get("source"))
        pay = dict(days=pdays if pdays is not None else ref,
                   early_pct=(early or {}).get("pct"), early_days=(early or {}).get("early_payment_days"))

        vendor_rows.append(dict(
            vendor_id=vid, vendor_name=names[vid], channel=m["channel"], received_on=m["received_on"], read_status="read",
            model=ext.get("_model", ""), quality_status=qstatus, freight=freight, payment_terms_days=terms.get("payment_terms_days"),
            lead_time_days=terms.get("lead_time_days"), lead_time_text=terms.get("lead_time_text", ""),
            moq_text=terms.get("moq_text", ""), validity_text=terms.get("validity_text", ""),
            price_variation_text=terms.get("price_variation_text", ""), fx_clause_text=terms.get("fx_clause_text", ""),
            unconditional_discount_pct=uncond_pct,
            early_payment_discount_pct=(early or {}).get("pct"), early_payment_days=(early or {}).get("early_payment_days"),
            conditional_discount_pct=(cond or {}).get("pct"), conditional_discount_condition=(cond or {}).get("condition_text", ""),
            conditional_discount_threshold_inr=(cond or {}).get("threshold_inr"),
            notes=" | ".join(ext.get("things_a_buyer_should_know", [])),
        ))

        seen = {}
        for q in ext.get("line_quotes", []):
            lid = q.get("rfx_line_id")
            src = q.get("source") or {}
            if not lid or lid not in L:
                flags.add(vid, None, "medium", "unmatched_item",
                          f"Could not match vendor item '{q.get('vendor_description','?')}' to an RFx line.", src)
                continue
            key = (lid, bool(q.get("is_alternate_offer")))
            if key in seen:
                flags.add(vid, lid, "medium", "duplicate_quote", "Vendor gave two prices for this line; the first one is used.", src)
                continue
            seen[key] = True
            quote_rows.append(_normalize_quote(vid, q, L[lid], history, terms, uncond_pct, cond, A, overrides, flags, pay))

        # coverage: lines never mentioned
        mentioned = {k[0] for k in seen}
        for lid in L:
            if lid not in mentioned:
                quote_rows.append(dict(vendor_id=vid, line_id=lid, status="not_quoted", is_alternate=False, confidence="n/a"))
                flags.add(vid, lid, "medium", "silently_missing", "Not quoted and not mentioned anywhere in the response.")

    quotes = pd.DataFrame(quote_rows)
    if not quotes.empty:
        quotes["is_alternate"] = quotes["is_alternate"].fillna(False).astype(bool)
        for c in ("unit_price_inr", "freight_adder_inr", "landed_unit_price_inr", "landed_if_cond_discount",
                  "payment_adj_inr", "effective_unit_price_inr"):
            if c not in quotes:
                quotes[c] = None
            quotes[c] = pd.to_numeric(quotes[c], errors="coerce")
    vendors = pd.DataFrame(vendor_rows)

    # cross-vendor sanity checks: a unit error shows up as a wild ratio to peers
    if not quotes.empty and "landed_unit_price_inr" in quotes:
        main = quotes[(quotes["status"] == "quoted") & (~quotes["is_alternate"]) & quotes["landed_unit_price_inr"].notna()]
        for idx, r in main.iterrows():
            peers = main[(main["line_id"] == r["line_id"]) & (main["vendor_id"] != r["vendor_id"])]["landed_unit_price_inr"]
            if len(peers) >= 2:
                med = peers.median()
                ratio = r["landed_unit_price_inr"] / med if med else None
                quotes.loc[idx, "ratio_to_peer_median"] = round(ratio, 2) if ratio else None
                if ratio and (ratio > A.sanity_ratio_high or ratio < A.sanity_ratio_low):
                    flags.add(r["vendor_id"], r["line_id"], "high", "outlier_price",
                              f"Rs {_fmt(r['landed_unit_price_inr'])} is {ratio:.2f}x the other vendors' median (Rs {_fmt(med)}). "
                              "Likely a unit or pack-size mismatch - check before using.")
                    quotes.loc[idx, "confidence"] = "low"
        ly = quotes["line_id"].map(lambda x: history.get(x, {}).get("price_per_uom"))
        quotes["vs_last_year_pct"] = ((quotes["landed_unit_price_inr"] / ly - 1) * 100).round(1)
        quotes = quotes.merge(lines_df[["line_id", "annual_qty", "uom", "description"]], on="line_id", how="left")
        quotes["annual_value_inr"] = (quotes["landed_unit_price_inr"] * quotes["annual_qty"]).round(0)
        quotes["effective_annual_value_inr"] = (quotes["effective_unit_price_inr"] * quotes["annual_qty"]).round(0)
        quotes["vendor_name"] = quotes["vendor_id"].map(names)
        if not vendors.empty and "quality_status" in vendors:
            quotes = quotes.merge(vendors[["vendor_id", "quality_status"]], on="vendor_id", how="left")

    fl = pd.DataFrame(flags.rows)
    if not fl.empty:
        fl["_s"] = fl["severity"].map(SEV_ORDER)
        fl = fl.sort_values(["status", "_s", "vendor_id", "line_id"], na_position="first").drop(columns="_s").reset_index(drop=True)
        open_ct = fl[fl["status"] == "open"].groupby(["vendor_id", "line_id"]).size()
        if not quotes.empty:
            quotes["open_flags"] = [int(open_ct.get((v, l), 0)) for v, l in zip(quotes["vendor_id"], quotes["line_id"])]

    return dict(lines=lines_df, vendors=vendors, quotes=quotes, questionnaire=pd.DataFrame(q_rows),
                attachments=pd.DataFrame(att_rows), discounts=pd.DataFrame(disc_rows), flags=fl,
                assumptions=asdict(A))


def _normalize_quote(vid, q, line, history, terms, uncond_pct, cond, A, overrides, flags, pay=None):
    lid = line["line_id"]
    src = q.get("source") or {}
    row = dict(vendor_id=vid, line_id=lid, status=q.get("status", "quoted"), is_alternate=bool(q.get("is_alternate_offer")),
               vendor_description=q.get("vendor_description", ""), deviation=q.get("deviation_from_spec", ""),
               raw_price=q.get("price_value"), raw_currency=q.get("price_currency"), raw_unit=q.get("price_unit"),
               units_per_price=q.get("units_per_price"), price_source=q.get("price_source"),
               rate_rule=q.get("rate_rule_text", ""), match_basis=q.get("match_basis"), match_confidence=q.get("match_confidence"),
               match_note=q.get("match_note", ""), read_confidence=q.get("read_confidence"), read_issue=q.get("read_issue", ""),
               source_file=src.get("file", ""), source_location=src.get("location", ""), source_text=src.get("text", ""),
               unit_price_inr=None, freight_adder_inr=0.0, landed_unit_price_inr=None, landed_if_cond_discount=None,
               payment_adj_inr=0.0, effective_unit_price_inr=None, payment_note="", alt_reading_inr=None,
               confidence="high", steps="", overridden=False)
    steps = []
    conf = ["high"]

    if row["status"] in ("regret", "on_enquiry"):
        row["confidence"] = "n/a"
        flags.add(vid, lid, "info", "regret", f"Vendor declined this line ({row['status'].replace('_',' ')}).", src)
        return row
    if row["is_alternate"]:
        flags.add(vid, lid, "low", "alternate_offer",
                  f"Alternate offer, deviates from spec: {row['deviation'] or 'see source'}. Excluded from comparison unless enabled.", src)
    elif row["deviation"]:
        flags.add(vid, lid, "medium", "spec_deviation", f"Quoted with a deviation: {row['deviation']}", src)
        conf.append("medium")

    uom = line["uom"]
    wt_kg = (line.get("est_weight_g") or 0) / 1000

    def _convert():
        """Vendor's value -> Rs per RFx unit. None (with a flag) when it can't be done safely."""
        value, cur, unit, n = q.get("price_value"), (q.get("price_currency") or "INR").upper(), q.get("price_unit"), q.get("units_per_price")

        if q.get("price_source") == "previous_contract_reference":
            h = history.get(lid)
            if not h:
                flags.add(vid, lid, "high", "reference_unresolved", "Vendor said 'same as last year' but no contract price exists for this line.", src)
                row["confidence"] = "low"
                return None
            value, cur, unit, n = float(h["price_per_uom"]), "INR", "contract", 1
            steps.append(f"Vendor wrote '{src.get('text','same as last year')}'. Taken from FY25-26 contract: Rs {_fmt(value)} per {line['uom']} (basis: {h['basis']}).")
            flags.add(vid, lid, "medium", "inherited_price",
                      "Price not restated - inherited from last year's contract. Get written confirmation before award.", src)
            conf.append("medium")

        if value is None:
            flags.add(vid, lid, "high", "unreadable_price",
                      f"No usable price ({row['read_issue'] or 'value missing'}).", src)
            row["confidence"] = "low"
            return None

        steps.insert(0, f"Read: {value:g} {cur} {unit.replace('_',' ')}{f' (n={n:g})' if n and unit in ('per_n_pieces','per_box_of_n') else ''}"
                        f" from {src.get('file','?')} @ {src.get('location','?')}.") if unit != "contract" else None

        # 1) unit basis -> per RFx unit of measure
        if unit in ("per_piece", "per_set", "contract"):
            if uom == "kg" and unit != "contract":
                flags.add(vid, lid, "high", "unit_mismatch", f"Priced {unit.replace('_',' ')} but RFx unit is kg.", src)
                row["confidence"] = "low"
                return None
            per_uom = value
        elif unit in ("per_n_pieces", "per_box_of_n"):
            if not n:
                flags.add(vid, lid, "high", "pack_size_unknown", "Priced 'per box/pack' but the pack size is not stated.", src)
                row["confidence"] = "low"
                return None
            per_uom = value / n
            steps.append(f"Per {n:g} -> per {uom}: {value:g} / {n:g} = {per_uom:.4f}.")
        elif unit == "per_kg":
            if uom == "kg":
                per_uom = value
            else:
                per_uom = value * wt_kg
                steps.append(f"Per kg -> per {uom}: {value:g} x {line['est_weight_g']} g (RFx estimated weight) = {per_uom:.4f}.")
                flags.add(vid, lid, "medium", "weight_based_conversion",
                          f"Priced per kg; converted with RFx estimated weight {line['est_weight_g']} g. Vendor's real box weight may differ - ask for weight per box.", src)
                conf.append("medium")
        else:
            flags.add(vid, lid, "high", "unit_unknown", f"Unit basis unclear ('{unit}').", src)
            row["confidence"] = "low"
            return None

        # 2) currency
        if cur == "USD":
            steps.append(f"USD -> INR at {A.usd_inr} ({A.usd_inr_asof}): {per_uom:.4f} x {A.usd_inr} = {per_uom * A.usd_inr:.2f}.")
            per_uom *= A.usd_inr
        elif cur != "INR":
            flags.add(vid, lid, "high", "currency_unsupported", f"Currency {cur} has no rate configured.", src)
            row["confidence"] = "low"
            return None

        # 3) unconditional discount
        if uncond_pct:
            steps.append(f"Unconditional discount {uncond_pct:g}%: {per_uom:.2f} -> {per_uom * (1 - uncond_pct / 100):.2f}.")
            per_uom *= 1 - uncond_pct / 100

        return per_uom

    per_uom = _convert()

    # buyer override replaces the computed ex-works price (kept visible)
    # (also fills a price the system couldn't compute, e.g. pack size confirmed by the vendor on a call)
    ov = overrides.get(f"{vid}|{lid}") if not row["is_alternate"] else None
    if ov:
        before = f"Rs {per_uom:.2f}" if per_uom is not None else "no usable price"
        steps.append(f"BUYER OVERRIDE: {before} -> Rs {float(ov['unit_price_inr']):.2f} ({ov.get('note','')}, {ov.get('at','')}).")
        per_uom = float(ov["unit_price_inr"])
        row["overridden"] = True
    if per_uom is None:
        row["confidence"] = "low"
        row["steps"] = "\n".join(x for x in steps if x)
        return row

    row["unit_price_inr"] = round(per_uom, 2)

    # 4) freight -> landed
    if terms.get("freight") == "extra":
        add = A.freight_inr_per_kg * (wt_kg if uom != "kg" else 1)
        row["freight_adder_inr"] = round(add, 2)
        steps.append(f"Freight extra: + Rs {A.freight_inr_per_kg}/kg x {wt_kg if uom != 'kg' else 1:g} kg = + {add:.2f} (buyer assumption).")
        conf.append("medium")
    landed = per_uom + row["freight_adder_inr"]
    row["landed_unit_price_inr"] = round(landed, 2)
    if cond and cond.get("pct"):
        row["landed_if_cond_discount"] = round(per_uom * (1 - float(cond["pct"]) / 100) + row["freight_adder_inr"], 2)

    # 4b) payment terms (Decision 4): later is better, unless an early-payment discount beats holding the cash
    adj, note = 0.0, ""
    if pay and A.value_payment_terms:
        r, ref = A.cost_of_money_pct / 100, A.reference_payment_days
        days = float(pay["days"])
        normal = landed * r * (ref - days) / 365  # + = costs the buyer (pays earlier than asked)
        adj, note = normal, f"Payment {days:g} days vs {ref} asked at {A.cost_of_money_pct:g}%/yr: {normal:+.2f}"
        if pay.get("early_pct") and pay.get("early_days") is not None:
            early_cost = landed * (-float(pay["early_pct"]) / 100 + r * (ref - float(pay["early_days"])) / 365)
            if early_cost < normal:
                adj = early_cost
                note = (f"Early-payment discount {pay['early_pct']:g}% for {pay['early_days']:g} days beats holding cash: {early_cost:+.2f}")
            else:
                note += f" (early-payment discount {pay['early_pct']:g}% not worth it)"
        if abs(adj) >= 0.005:
            steps.append(note + " (buyer's cost-of-money assumption).")
    row["payment_adj_inr"] = round(adj, 2)
    row["payment_note"] = note
    row["effective_unit_price_inr"] = round(landed + adj, 2)

    # 5) reading / matching confidence
    for k, label in (("read_confidence", "read"), ("match_confidence", "match")):
        v = q.get(k)
        if v in ("medium", "low") and not row["overridden"]:
            conf.append(v)
            detail = row["read_issue"] if label == "read" else row["match_note"]
            flags.add(vid, lid, "high" if v == "low" else "medium", f"{label}_confidence",
                      f"{label.title()} confidence {v}: {detail or 'see source'}", src)

    # 6) a vendor's group rule ("38 for the 3-ply") applied where its scope is unclear must not decide an award
    if q.get("price_source") == "rate_rule" and q.get("match_confidence") in ("medium", "low") and not row["overridden"]:
        h = history.get(lid)
        alt = ""
        if h is not None:
            alt_landed = float(h["price_per_uom"]) + row["freight_adder_inr"]
            alt = f" Other reading ('same as last year'): Rs {alt_landed:.2f}."
            row["alt_reading_inr"] = round(alt_landed, 2)
        flags.add(vid, lid, "high", "rule_scope_ambiguous",
                  f"Unclear whether the vendor's rule '{row['rate_rule']}' covers this item. This reading: Rs {landed:.2f}.{alt} "
                  "Can't win until confirmed.", src)
        conf.append("low")
        steps.append("Rule scope unclear: held back from winning until the vendor confirms.")

    steps.append(f"= Rs {landed:.2f} per {uom} landed.")
    row["steps"] = "\n".join(s for s in steps if s)
    row["confidence"] = "low" if "low" in conf else ("medium" if "medium" in conf else "high")
    if row["overridden"]:
        row["confidence"] = "buyer-confirmed"
    return row
