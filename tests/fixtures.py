"""
Hand-built stand-ins for model output, used ONLY to test the deterministic
code (normalization, rules, SQL, UI) without an API key. They mimic what the
extraction model returns: vendor's own units and currency, sources, confidence.
The app never loads these.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = json.loads((ROOT / "data/_answer_key/ground_truth.json").read_text())
RFX = json.loads((ROOT / "data/rfx/rfx_seed.json").read_text())
T = KEY["prices_inr_per_rfx_uom"]
L = {l["line_id"]: l for l in RFX["lines"]}


def src(f, loc, t):
    return {"file": f, "location": loc, "text": t}


def q(lid, value, unit="per_piece", cur="INR", n=1, ps="explicit", rc="high", mc="high", alt=False, dev="", status="quoted",
      rule="", issue="", note="", s=None):
    return dict(rfx_line_id=lid, match_basis="item_code", match_confidence=mc, match_note=note, vendor_description=lid,
                status=status, price_value=value, price_currency=cur, price_unit=unit, units_per_price=n, price_source=ps,
                rate_rule_text=rule, is_alternate_offer=alt, deviation_from_spec=dev, read_confidence=rc, read_issue=issue,
                source=s or src("x", "y", str(value)))


def qa(qid, ans, b=None, n=None, ev=""):
    return dict(q_id=qid, answered=ans is not None, answer_text=ans or "", answer_bool=b, answer_number=n, evidence=ev,
                source=src("x", "y", ans or ""))


def v1():
    lq = []
    for lid, p in T["V1"].items():
        if lid.startswith(("PT", "LP")):
            lq.append(q(lid, round(p * 100), "per_n_pieces", n=100, s=src("SGC_Quotation_2611.xlsx", "Quotation!G30", f"{round(p*100)}")))
        elif lid == "CR-701":
            lq.append(q(lid, p, "per_kg"))
        else:
            lq.append(q(lid, p, s=src("SGC_Quotation_2611.xlsx", "Quotation!G9", f"{p}")))
    lq.append(q("CB-107", KEY["v1_alternate"]["CB-107"], alt=True, dev="BF 16 instead of BF 18"))
    return dict(vendor={"name": "Shree Ganesh Corrugators"}, line_quotes=lq,
                terms=dict(freight="included", payment_terms_days=60, lead_time_days=7, moq_text="1,000 pcs per size",
                           price_variation_text="+/-5% on kraft index", source=src("SGC_Quotation_2611.xlsx", "Terms!B1", "FOR Manesar")),
                discounts=[],
                questionnaire=[qa("Q1", "YES - Cert No. TQ-91877, TUV, valid till 14-Feb-2028", True), qa("Q2", "YES", True),
                               qa("Q3", "YES", True), qa("Q4", "450 MT", n=450), qa("Q5", "7 days", n=7), qa("Q6", "Accepted", True),
                               qa("Q7", "70%", n=70), qa("Q8", "Neemrana")],
                attachments=[dict(file="SGC_Lab_Test_Report_Sept26.pdf", doc_type="Lab test report", valid_until=None)],
                things_a_buyer_should_know=["Alternate offer on CB-107 at BF16", "Pads & partitions per 100 nos"])


def v2():
    lq = [q(lid, p, s=src("Rajhans_Quotation_RPI-1187.pdf", "page 1", f"{p}")) for lid, p in T["V2"].items()]
    lq.append(q("CR-701", None, status="regret"))
    return dict(vendor={"name": "Rajhans"}, line_quotes=lq,
                terms=dict(freight="included", payment_terms_days=45, lead_time_days=5, source=src("x", "page 1", "Payment: 45 days")),
                discounts=[dict(description="Special trade discount", pct=5, condition_type="order_value_threshold",
                                threshold_inr=2500000, condition_text="annual order value > Rs 25,00,000", applies_to="all lines",
                                source=src("Rajhans_Quotation_RPI-1187.pdf", "page 1 footnote", "special trade discount of 5%"))],
                questionnaire=[qa("Q1", "Yes, certified", True), qa("Q2", "Yes", True), qa("Q3", "Yes", True), qa("Q4", "300", n=300),
                               qa("Q5", "5", n=5), qa("Q6", "45 days", False), qa("Q7", "80", n=80), qa("Q8", "Faridabad")],
                attachments=[dict(file="RPI_ISO9001_Certificate.pdf", doc_type="ISO 9001:2015 certificate", reference_no="IQC/QMS/22/4410",
                                  standard_or_scope="ISO 9001:2015", valid_until="2026-04-30")],
                things_a_buyer_should_know=["5% discount in footnote", "ISO certificate expired"])


def v3():
    fx = KEY["usd_inr_used_by_vendor"]
    lq = []
    for lid, p in T["V3"].items():
        if lid.startswith("LP"):
            lq.append(q(lid, round(p * 100 / fx, 2), "per_box_of_n", "USD", n=100))
        elif lid == "CR-701":
            lq.append(q(lid, round(p / fx, 3), "per_kg", "USD"))
        elif lid.startswith("PT"):
            lq.append(q(lid, round(p / fx, 4), "per_set", "USD"))
        else:
            lq.append(q(lid, round(p / fx, 4), "per_piece", "USD", mc="high"))
    return dict(vendor={"name": "GlobePack"}, line_quotes=lq,
                terms=dict(freight="included", payment_terms_days=60, lead_time_days=21, fx_clause_text="Invoiced in INR at RBI reference rate on invoice date",
                           moq_text="One full truckload (~4 MT) per call-off", source=src("x", "¶9", "RBI reference rate")),
                discounts=[],
                questionnaire=[qa("Q1", "ISO 9001:2015", True), qa("Q2", "Yes NABL lab", True), qa("Q3", "Yes", True), qa("Q4", "1800", n=1800),
                               qa("Q5", "21 days initially", n=21), qa("Q6", "Accepted", True), qa("Q7", "60", n=60), qa("Q8", "Sriperumbudur")],
                attachments=[dict(file="GlobePack_ISO_Certificate.pdf", doc_type="ISO 9001:2015 certificate", reference_no="BV-IND-QMS-77120",
                                  standard_or_scope="ISO 9001:2015", valid_until="2028-03-09")],
                things_a_buyer_should_know=["USD pricing", "pads per box of 100"])


def v4():
    lq = []
    for lid, p in T["V4"].items():
        unit = "per_kg" if lid == "CR-701" else "per_piece"
        if lid == KEY["v4_smudged_line"]:
            lq.append(q(lid, p, unit, rc="low", issue="first digit obscured by a smudge; reads 58.20 or 53.20"))
        elif lid == "CB-101":
            lq.append(q(lid, p, unit, mc="medium", note="rate card size 380x280x225 vs RFx 220 mm height"))
        else:
            lq.append(q(lid, p, unit, rc="medium", issue="photo at an angle"))
    for lid in ("CB-301", "CB-302", "CB-303", "DT-401", "DT-402", "PT-501", "PT-502"):
        lq.append(q(lid, None, status="on_enquiry"))
    return dict(vendor={"name": "Vinayak"}, line_quotes=lq,
                terms=dict(freight="not_stated", payment_terms_days=30, lead_time_days=3, source=src("x", "photo", "Delivery 3 days")),
                discounts=[],
                questionnaire=[qa("Q1", "not yet, applied", False), qa("Q2", "outside lab on request", False), qa("Q3", "yes", True),
                               qa("Q4", "around 45 ton", n=45), qa("Q5", "3", n=3), qa("Q6", "30 days", False), qa("Q7", None), qa("Q8", "Daulatabad")],
                attachments=[], things_a_buyer_should_know=["Fails ISO and capacity"])


def v5():
    lq = []
    for l in RFX["lines"]:
        lid = l["line_id"]
        if l["item_type"] == "RSC carton":
            rate = 38 if l["ply"] == 3 else 42
            lq.append(q(lid, rate, "per_kg", ps="rate_rule", rule=f"Rs {rate}/kg for the {l['ply']}-ply"))
        else:
            lq.append(q(lid, None, "per_piece", ps="previous_contract_reference", mc="medium", rule="rest same as last year",
                        s=src("email.txt", "email body", "rest same as last year")))
    return dict(vendor={"name": "Bharat"}, line_quotes=lq,
                terms=dict(freight="extra", payment_terms_days=None, lead_time_days=None, source=src("email.txt", "body", "freight extra")),
                discounts=[], questionnaire=[qa(f"Q{i}", None) for i in range(1, 9)], attachments=[],
                things_a_buyer_should_know=["One-line email"])


def all_fixtures():
    return {"V1": v1(), "V2": v2(), "V3": v3(), "V4": v4(), "V5": v5()}
