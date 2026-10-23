"""
AI extraction: one model call per vendor reads everything the vendor sent and
returns a strict structure. Design rules given to the model:

  * Record what the document SAYS, in the vendor's own units and currency.
    No conversions, no arithmetic - code does that (normalize.py), so every
    number on screen can be re-derived and checked.
  * Every value carries its source (file + location + verbatim text) and a
    read confidence. "I can't read this" is a valid, expected answer.
  * Never fill a gap. A line the vendor didn't price stays unpriced.
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .ingest import vendor_content_blocks, vendor_files
from .llm import LLM, tool_input

SOURCE = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "location": {"type": "string", "description": "sheet!cell, 'page 2', '¶7', 'photo, row 16', 'email body' ..."},
        "text": {"type": "string", "description": "verbatim snippet (<= 200 chars) exactly as it appears"},
    },
    "required": ["file", "location", "text"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {
            "type": "object",
            "properties": {
                "name": {"type": "string"}, "contact": {"type": "string"},
                "plant_location": {"type": "string"}, "quote_reference": {"type": "string"},
            },
            "required": ["name"],
        },
        "line_quotes": {
            "type": "array",
            "description": "One entry per RFx line the vendor responded to (including regrets), plus extra entries for alternate offers.",
            "items": {
                "type": "object",
                "properties": {
                    "rfx_line_id": {"type": ["string", "null"], "description": "Matched RFx line id, or null if it cannot be matched"},
                    "match_basis": {"type": "string", "enum": ["item_code", "size_and_spec", "description", "rule_applied_to_group", "previous_contract_reference", "unmatched"]},
                    "match_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "match_note": {"type": "string", "description": "Why this match; mention any dimension/spec difference"},
                    "vendor_description": {"type": "string"},
                    "status": {"type": "string", "enum": ["quoted", "regret", "on_enquiry"]},
                    "price_value": {"type": ["number", "null"], "description": "Number exactly as written. null if not given / unreadable / by reference"},
                    "price_currency": {"type": ["string", "null"], "description": "ISO code as stated or clearly implied (INR, USD...)"},
                    "price_unit": {"type": "string", "enum": ["per_piece", "per_set", "per_n_pieces", "per_kg", "per_box_of_n", "lump_sum", "unknown"]},
                    "units_per_price": {"type": ["number", "null"], "description": "For per_n_pieces / per_box_of_n: how many pieces the price covers (e.g. 100). Else 1. null if the pack size is not stated."},
                    "price_source": {"type": "string", "enum": ["explicit", "rate_rule", "previous_contract_reference"]},
                    "rate_rule_text": {"type": "string", "description": "If price comes from a rule covering several lines (e.g. 'Rs 42/kg for the 5-ply'), the rule verbatim"},
                    "is_alternate_offer": {"type": "boolean"},
                    "deviation_from_spec": {"type": "string", "description": "Any stated deviation (board, BF, size, print). Empty if none."},
                    "read_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "read_issue": {"type": "string", "description": "What makes the reading uncertain (smudge, cropped, ambiguous unit...). Empty if none."},
                    "source": SOURCE,
                },
                "required": ["rfx_line_id", "match_basis", "match_confidence", "status", "price_value", "price_currency",
                             "price_unit", "units_per_price", "price_source", "is_alternate_offer", "read_confidence", "source"],
            },
        },
        "terms": {
            "type": "object",
            "properties": {
                "freight": {"type": "string", "enum": ["included", "extra", "not_stated"]},
                "freight_text": {"type": "string"},
                "taxes_text": {"type": "string"},
                "payment_terms_days": {"type": ["number", "null"]},
                "lead_time_days": {"type": ["number", "null"]},
                "lead_time_text": {"type": "string"},
                "validity_text": {"type": "string"},
                "moq_text": {"type": "string"},
                "price_variation_text": {"type": "string", "description": "Index-linked / paper price variation clauses"},
                "fx_clause_text": {"type": "string", "description": "How foreign-currency prices will be invoiced"},
                "source": SOURCE,
            },
            "required": ["freight", "payment_terms_days", "lead_time_days"],
        },
        "discounts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "pct": {"type": ["number", "null"]},
                    "condition_type": {"type": "string", "enum": ["unconditional", "order_value_threshold", "volume_threshold", "early_payment", "other"]},
                    "threshold_inr": {"type": ["number", "null"]},
                    "early_payment_days": {"type": ["number", "null"], "description": "For early_payment discounts: pay within this many days"},
                    "condition_text": {"type": "string"},
                    "applies_to": {"type": "string", "description": "'all lines' or the line ids / groups"},
                    "source": SOURCE,
                },
                "required": ["description", "pct", "condition_type", "source"],
            },
        },
        "questionnaire": {
            "type": "array",
            "description": "One entry per RFx question, even if unanswered.",
            "items": {
                "type": "object",
                "properties": {
                    "q_id": {"type": "string"},
                    "answered": {"type": "boolean"},
                    "answer_text": {"type": "string"},
                    "answer_bool": {"type": ["boolean", "null"]},
                    "answer_number": {"type": ["number", "null"]},
                    "evidence": {"type": "string", "description": "What supports the answer (attached certificate, report...)"},
                    "source": SOURCE,
                },
                "required": ["q_id", "answered", "answer_bool", "answer_number"],
            },
        },
        "attachments": {
            "type": "array",
            "description": "Every supporting document (certificates, test reports, declarations).",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "doc_type": {"type": "string"},
                    "holder": {"type": "string"},
                    "reference_no": {"type": "string"},
                    "standard_or_scope": {"type": "string"},
                    "valid_from": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "valid_until": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "key_facts": {"type": "string"},
                },
                "required": ["file", "doc_type", "valid_until"],
            },
        },
        "things_a_buyer_should_know": {
            "type": "array", "items": {"type": "string"},
            "description": "Anything unusual, contradictory or easy to miss (footnotes, conditions, claims that don't match evidence).",
        },
    },
    "required": ["vendor", "line_quotes", "terms", "discounts", "questionnaire", "attachments", "things_a_buyer_should_know"],
}

TOOL = {"name": "record_vendor_response", "description": "Record the structured reading of one vendor's response.",
        "input_schema": SCHEMA}

SYSTEM = """You are the document-reading component of a procurement system. A buyer sent an RFx (below) and a vendor replied in whatever format they liked. Read EVERYTHING the vendor sent (email body, spreadsheets, PDFs incl. small print and footnotes, Word documents, photos) and record it with the record_vendor_response tool.

Hard rules:
1. Record values exactly as the vendor states them: their number, their currency, their unit basis. Do NOT convert units, currencies, apply discounts or do any arithmetic. Downstream code does that deterministically.
2. Unit basis matters more than anything. Read section headers, column headers, footnotes and phrases like "per 100 nos", "per box", "/kg", "per set". If a header changes the unit for a block of rows, apply it to those rows. If the pack size behind "per box" is stated, put it in units_per_price; if not stated, units_per_price = null and explain in read_issue.
3. Never invent or fill in. If a line is not priced, do not create a price. If the vendor regrets a line, record status "regret". If ANY digit is unreadable, smudged, partly obscured or ambiguous, read_confidence MUST be "low" (never "medium"): give your best reading, describe the problem and the alternative reading in read_issue; if you truly cannot read it, price_value = null. Use "medium" only for issues that don't put the number itself in doubt (e.g. a skewed but legible photo).
4. Rules that cover several lines (e.g. "Rs 42/kg for the 5-ply") must be expanded into one entry per affected RFx line, price_source "rate_rule", rate_rule_text verbatim, match_basis "rule_applied_to_group". If the rule's scope is ambiguous (does "5-ply" include 7-ply? does it include trays?), only apply it where it clearly fits and use match_confidence "medium" with a note.
5. References to earlier pricing ("rest same as last year", "as per existing contract") -> one entry per affected RFx line with price_source "previous_contract_reference", price_value null, match_basis "previous_contract_reference". The buyer's system holds the old contract; you don't need it.
6. Matching: prefer the buyer's item code. Otherwise match on item type + ply + dimensions + print. If dimensions differ slightly from the RFx (e.g. 225 vs 220 mm), still match but use match_confidence "medium" and say so in match_note. Alternate offers (different spec offered in addition) get their own entry with is_alternate_offer true and deviation_from_spec filled.
7. Questionnaire: one entry per question id. A question not addressed anywhere -> answered false. Use answer_bool/answer_number where the answer is clearly yes/no or numeric.
8. Attachments: for certificates and reports, read reference numbers and validity dates exactly. Do not judge validity yourself; just record the dates.
9. Sources: for every value give file, a precise location and a short verbatim snippet so a human can find it in seconds.
10. Use things_a_buyer_should_know for anything conditional, contradictory or buried (e.g. a discount in a footnote, a claim that an attached document doesn't support, a currency clause)."""


def rfx_brief(rfx):
    lines = [f"{l['line_id']} | {l['description']} | UoM: {l['uom']} | est. weight {l['est_weight_g']} g/unit"
             for l in rfx["lines"]]
    qs = [f"{q['q_id']}: {q['question']}" for q in rfx["questionnaire"]]
    return (f"RFx {rfx['rfx_id']} from {rfx['buyer']['company']} - {rfx['title']}\n"
            f"Requested price basis: {rfx['terms']['price_basis']}\n\nLINES:\n" + "\n".join(lines)
            + "\n\nQUESTIONNAIRE:\n" + "\n".join(qs))


def _fingerprint(vendor_id, rfx):
    h = hashlib.sha256(config.EXTRACTION_VERSION.encode())
    h.update(json.dumps(rfx["lines"], sort_keys=True).encode())
    for p in vendor_files(vendor_id):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def cached(vendor_id, rfx):
    p = config.EXTRACTIONS / f"{vendor_id}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("_fingerprint") != _fingerprint(vendor_id, rfx):
        return None
    return data


def extract_vendor(llm: LLM, vendor_id, rfx, force=False):
    if not force:
        c = cached(vendor_id, rfx)
        if c:
            return c
    content = [{"type": "text", "text": rfx_brief(rfx)},
               {"type": "text", "text": f"\n\nVENDOR RESPONSE ({vendor_id}) - everything they sent:"}]
    content += vendor_content_blocks(vendor_id)
    content.append({"type": "text", "text": "Now record this vendor's response with the tool. Cover every RFx line the vendor addressed."})
    resp = llm.create(system=SYSTEM, messages=[{"role": "user", "content": content}],
                      tools=[TOOL], tool_choice={"type": "tool", "name": "record_vendor_response"},
                      max_tokens=20000, tag=f"extract:{vendor_id}")
    data = tool_input(resp, "record_vendor_response")
    data["_fingerprint"] = _fingerprint(vendor_id, rfx)
    data["_model"] = llm.model
    data["_truncated"] = resp.stop_reason == "max_tokens"
    (config.EXTRACTIONS / f"{vendor_id}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def extract_all(llm: LLM, vendor_ids, rfx, force=False, on_done=None):
    out, errors = {}, {}
    llm._pick_model()  # once, before the threads start
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(extract_vendor, llm, v, rfx, force): v for v in vendor_ids}
        for f in as_completed(futs):
            v = futs[f]
            try:
                out[v] = f.result()
            except Exception as e:  # keep going; show the buyer which one failed
                errors[v] = str(e)
            if on_done:
                on_done(v, errors.get(v))
    return out, errors


def load_all_cached(vendor_ids, rfx):
    return {v: c for v in vendor_ids if (c := cached(v, rfx))}
