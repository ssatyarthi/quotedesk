"""
RFx co-pilot: the buyer describes what they need in plain language; the model
drafts scope, line items, questionnaire and terms by calling tools that edit a
structured draft. Box weights are computed in code (needed later to compare
per-kg quotes), never guessed by the model.
"""
import copy
import json
import re
from datetime import date, datetime, timedelta

import pandas as pd

from . import agent, config

BOARD = {3: ("150/120/150", 480.0, 18), 5: ("180/120/150/120/180", 870.0, 20), 7: ("200/150/150/150/150/150/200", 1375.0, 22)}

VENDOR_LIST = [
    dict(vendor_id="V1", name="Shree Ganesh Corrugators", email="sales@shreeganeshcorr.example", city="Neemrana"),
    dict(vendor_id="V2", name="Rajhans Packaging Industries", email="mk@rajhanspack.example", city="Faridabad"),
    dict(vendor_id="V3", name="GlobePack Solutions India", email="arjun.menon@globepack.example", city="Sriperumbudur"),
    dict(vendor_id="V4", name="Vinayak Packers", email="vinayakpackers.ggn@gmail.example", city="Gurugram"),
    dict(vendor_id="V5", name="Bharat Corrugated (incumbent)", email="vikram@bharatcorrugated.example", city="Sonipat"),
]


def rsc_weight_g(L, W, H, ply):
    if ply not in BOARD:
        return None
    return round((2 * (L + W) + 35) * (W + H) / 1e6 * BOARD[ply][1])


def empty_draft():
    return dict(rfx_id=f"SF-RFQ-{date.today():%Y}-DRAFT", title="", buyer=dict(company="Sunrise Foods Pvt Ltd", name="Priya Nair",
                role="Category Buyer, Packaging", email="priya.nair@sunrisefoods.example"),
                issued_on=None, due_on=None, currency="", terms={}, lines=[], questionnaire=[], invited_vendors=[])


LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "line_id": {"type": "string"}, "item_type": {"type": "string"}, "ply": {"type": "integer"},
        "length_mm": {"type": ["number", "null"]}, "width_mm": {"type": ["number", "null"]}, "height_mm": {"type": ["number", "null"]},
        "board_gsm": {"type": "string"}, "burst_factor_min": {"type": ["number", "null"]}, "print": {"type": "string"},
        "uom": {"type": "string", "enum": ["pcs", "set", "kg"]}, "annual_qty": {"type": "number"},
        "est_weight_g": {"type": ["number", "null"], "description": "Leave null for RSC cartons - the tool computes it"},
        "description": {"type": "string"},
    },
    "required": ["line_id", "item_type", "uom", "annual_qty", "description"],
}

TOOLS = [
    {"name": "get_item_master", "description": "Sunrise's packaging item master with last year's contract prices and FY volumes. Use it to draft lines instead of asking the buyer to type specs.",
     "input_schema": {"type": "object", "properties": {"filter": {"type": "string", "description": "optional text filter, e.g. '5-ply'"}}}},
    {"name": "get_vendor_list", "description": "Approved vendor list for corrugated packaging.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_header", "description": "Set RFx title, due date (YYYY-MM-DD) and commercial terms.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "due_on": {"type": "string"},
         "currency": {"type": "string", "description": "ISO code vendors must quote in, e.g. INR"},
         "terms": {"type": "object", "description": "keys: price_basis, freight (included / extra, and to where), payment, validity, delivery, quality, award"}}}},
    {"name": "upsert_lines", "description": "Add or replace line items (matched by line_id). RSC carton weights are computed automatically.",
     "input_schema": {"type": "object", "properties": {"lines": {"type": "array", "items": LINE_SCHEMA}}, "required": ["lines"]}},
    {"name": "remove_lines", "description": "Remove line items by id.",
     "input_schema": {"type": "object", "properties": {"line_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["line_ids"]}},
    {"name": "set_questionnaire", "description": "Replace the supplier questionnaire.",
     "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "object", "properties": {
         "q_id": {"type": "string"}, "question": {"type": "string"}, "kind": {"type": "string", "enum": ["yes_no", "number", "text"]},
         "unit": {"type": "string"}, "mandatory": {"type": "boolean"},
         "pass_rule": {"type": "string", "description": "e.g. 'Yes', '>= 120', 'info'"}},
         "required": ["q_id", "question", "kind", "mandatory", "pass_rule"]}}}, "required": ["questions"]}},
    {"name": "set_invited_vendors", "description": "Choose which vendors receive the RFx.",
     "input_schema": {"type": "object", "properties": {"vendor_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["vendor_ids"]}},
    {"name": "review_draft", "description": "Run completeness checks on the current draft and get the list of gaps.",
     "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = """You are an RFx co-pilot for {company}'s packaging category buyer. Today is {today}.
Help the buyer turn a plain-language need into a complete, unambiguous RFx by editing the draft with your tools.

Why it matters: most messy vendor replies are caused by vague RFx documents. Every line needs an unambiguous spec (item type, ply, internal dimensions, board GSM, BF, print) and ONE unit of measure; the RFx must fix the currency, and the terms must state the price basis (GST treatment) and freight (included to the plant, or extra). The send button stays locked until these are set. Ask vendors to price per our unit of measure and to give weight per box when they price per kg.

How to work:
- Draft first, ask second. Pull from the item master rather than making the buyer type specs. Make sensible defaults (12-month validity, 60-day payment, delivered Manesar, ex-GST, INR) and tell the buyer what you assumed.
- Ask at most 2 short questions at a time, only for things you cannot reasonably assume (volumes for new items, which vendors to invite, deadline).
- Always include a quality questionnaire with clear pass rules (ISO 9001 with valid certificate, in-house testing + COA, food-safe inks, minimum capacity).
- Run review_draft before telling the buyer it's ready.
- Keep replies short; the buyer sees the draft update live beside the chat."""


class Copilot:
    def __init__(self, llm, draft):
        self.llm = llm
        self.draft = draft
        self.system = SYSTEM.format(company=draft["buyer"]["company"], today=date.today().isoformat())

    def get_item_master(self, filter=""):
        seed = json.loads(config.RFX_SEED.read_text(encoding="utf-8"))
        hist = pd.read_csv(config.HISTORY) if config.HISTORY.exists() else pd.DataFrame()
        df = pd.DataFrame(seed["lines"])
        if not hist.empty:
            df = df.merge(hist[["line_id", "price_per_uom", "basis"]], on="line_id", how="left").rename(
                columns={"price_per_uom": "last_year_price_inr", "basis": "last_year_basis", "annual_qty": "fy_volume"})
        if filter:
            df = df[df.apply(lambda r: filter.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
        return df.to_csv(index=False)

    def get_vendor_list(self):
        return json.dumps(VENDOR_LIST)

    def set_header(self, title=None, due_on=None, terms=None, currency=None):
        if currency:
            self.draft["currency"] = currency
        if title:
            self.draft["title"] = title
        if due_on:
            self.draft["due_on"] = due_on
        if terms:
            self.draft["terms"].update(terms)
        return "Header updated."

    def upsert_lines(self, lines):
        by_id = {l["line_id"]: l for l in self.draft["lines"]}
        for l in lines:
            l = dict(l)
            if "rsc" in l.get("item_type", "").lower() and l.get("length_mm") and l.get("ply") in BOARD:
                l["est_weight_g"] = rsc_weight_g(l["length_mm"], l["width_mm"], l["height_mm"], l["ply"])
                l.setdefault("board_gsm", BOARD[l["ply"]][0])
            by_id[l["line_id"]] = l
        self.draft["lines"] = sorted(by_id.values(), key=lambda x: x["line_id"])
        return f"Draft now has {len(self.draft['lines'])} lines."

    def remove_lines(self, line_ids):
        self.draft["lines"] = [l for l in self.draft["lines"] if l["line_id"] not in set(line_ids)]
        return f"Draft now has {len(self.draft['lines'])} lines."

    def set_questionnaire(self, questions):
        self.draft["questionnaire"] = questions
        return f"{len(questions)} questions set."

    def set_invited_vendors(self, vendor_ids):
        self.draft["invited_vendors"] = vendor_ids
        return f"Invited: {', '.join(vendor_ids)}"

    def review_draft(self):
        return json.dumps(check_draft(self.draft))

    def chat(self, history, message, on_step=None):
        history.append({"role": "user", "content": message})
        handlers = {t["name"]: getattr(self, t["name"]) for t in TOOLS}
        return agent.run(self.llm, self.system, history, TOOLS, handlers, tag="copilot", on_step=on_step)


def check_draft(d):
    gaps = []
    if not d.get("title"):
        gaps.append("No title.")
    if not d.get("due_on"):
        gaps.append("No response due date.")
    terms = d.get("terms", {})
    for k in ("price_basis", "freight", "payment", "validity"):
        if not terms.get(k):
            gaps.append(f"Terms missing '{k}'" + (" (say whether prices include delivery to the plant)." if k == "freight" else "."))
    if not d.get("currency"):
        gaps.append("No currency set (vendors will quote in their own).")
    for l in d["lines"]:
        if l.get("uom") not in ("pcs", "set", "kg"):
            gaps.append(f"{l['line_id']}: unit of measure must be pcs, set or kg.")
    if not d["lines"]:
        gaps.append("No line items.")
    for l in d["lines"]:
        if not l.get("annual_qty"):
            gaps.append(f"{l['line_id']}: no annual quantity.")
        if l.get("uom") != "kg" and not l.get("est_weight_g"):
            gaps.append(f"{l['line_id']}: no estimated weight (needed to compare per-kg quotes).")
        if "rsc" in l.get("item_type", "").lower() and not all(l.get(k) for k in ("length_mm", "width_mm", "height_mm")):
            gaps.append(f"{l['line_id']}: dimensions incomplete.")
    if not d["questionnaire"]:
        gaps.append("No quality questionnaire.")
    if not d["invited_vendors"]:
        gaps.append("No vendors invited.")
    return {"ready": not gaps, "gaps": gaps}


def send_stub(draft):
    """Fake SMTP: writes one .eml per invited vendor into outbox/. Returns file paths."""
    vend = {v["vendor_id"]: v for v in VENDOR_LIST}
    stamp = datetime.now()
    d = copy.deepcopy(draft)
    d["issued_on"] = stamp.date().isoformat()
    if d["rfx_id"].endswith("DRAFT"):
        d["rfx_id"] = d["rfx_id"].replace("DRAFT", stamp.strftime("%m%d%H%M"))
    out = config.OUTBOX / d["rfx_id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "rfx.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
    paths = []
    lines_txt = "\n".join(f"  {l['line_id']}: {l['description']}  |  qty {l['annual_qty']:,} {l['uom']}/yr" for l in d["lines"])
    qs = "\n".join(f"  {q['q_id']}. {q['question']}" for q in d["questionnaire"])
    terms = "\n".join(f"  {k.replace('_',' ').title()}: {v}" for k, v in d["terms"].items())
    for vid in d["invited_vendors"]:
        v = vend.get(vid)
        if not v:
            continue
        body = (f"From: {d['buyer']['email']}\nTo: {v['email']}\nSubject: RFQ {d['rfx_id']} - {d['title']}\n"
                f"Reply-To: rfq+{d['rfx_id'].lower()}-{vid.lower()}@sunrisefoods.example\n\n"
                f"Dear {v['name']} team,\n\nSunrise Foods invites your quotation for the items below. Reply in any format "
                f"you like (your own Excel/PDF, a photo, or plain email) by {d.get('due_on')}. Please price per our unit "
                f"of measure, state currency and freight, and give weight per box if you price per kg.\n\n"
                f"ITEMS\n{lines_txt}\n\nTERMS\n  Currency: {d.get('currency') or 'INR'}\n{terms}\n\nQUESTIONNAIRE (please answer each)\n{qs}\n\n"
                f"Regards,\n{d['buyer']['name']}\n{d['buyer']['role']}, {d['buyer']['company']}\n")
        p = out / f"{vid}_{re.sub('[^A-Za-z]+', '_', v['name'])}.eml"
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return d, paths
