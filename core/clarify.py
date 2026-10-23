"""
Clarification emails (PRD R4.6, Decision 3).

For a vendor with open issues, the AI drafts ONE short, specific email that
asks for exactly what is needed to resolve them (a renewed certificate, a
smudged price, restated lines, freight, weight per box...). The buyer edits
and "sends" it; sending is stubbed (an .eml file in outbox/clarifications/).
"""
import json
import re
from datetime import datetime

from . import config
from .llm import tool_input

TOOL = {
    "name": "draft_email",
    "description": "Return the clarification email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "asks": {"type": "array", "items": {"type": "string"},
                     "description": "One line per thing requested, in the order it appears in the body"},
        },
        "required": ["subject", "body", "asks"],
    },
}

SYSTEM = """You write clarification emails from a procurement buyer to a vendor who replied to an RFQ.
Goal: get every open issue resolved in ONE reply from the vendor.
Rules:
- One email, short and polite, plain business English used in Indian procurement. No marketing tone.
- A numbered list of specific asks, each tied to an item code or document, each answerable in one line or one attachment.
- Quote the vendor's own words where it helps them find it (e.g. "your rate card shows CB-206 as 58.20 or 53.20; please confirm").
- Never reveal other vendors' prices or names, internal scoring, or that the vendor might lose the award.
- Ask for anything missing in a form that removes ambiguity next time (price per piece, weight per box, freight included or not).
- Give the reply-by date if provided. Sign off with the buyer's name and company."""


def open_issues(tables, vendor_id):
    f = tables["flags"]
    if f.empty:
        return []
    g = f[(f["vendor_id"] == vendor_id) & (f["status"] == "open") & (f["severity"].isin(["high", "medium"]))]
    out = []
    for r in g.itertuples():
        out.append(dict(severity=r.severity, category=r.category, line=r.line_id if isinstance(r.line_id, str) else None,
                        issue=r.message, vendor_text=r.source_text, where=f"{r.source_file} {r.source_location}".strip()))
    return out


def draft(llm, tables, rfx, vendor_id, vendor_name, reply_by=None):
    issues = open_issues(tables, vendor_id)
    if not issues:
        return dict(subject="", body="", asks=[], issues=[])
    q = tables["questionnaire"]
    qv = q[(q["vendor_id"] == vendor_id) & (q["result"].isin(["CONTRADICTED", "NOT_ANSWERED", "UNCLEAR", "FAIL"]))] if not q.empty else q
    ctx = dict(rfx_id=rfx["rfx_id"], rfx_title=rfx["title"], buyer=rfx["buyer"], vendor=vendor_name, reply_by=reply_by,
               open_issues=issues,
               questionnaire_gaps=qv[["q_id", "question", "result", "reason"]].to_dict("records") if not qv.empty else [])
    resp = llm.create(system=SYSTEM, messages=[{"role": "user", "content": json.dumps(ctx, default=str)}],
                      tools=[TOOL], tool_choice={"type": "tool", "name": "draft_email"}, max_tokens=2500,
                      tag=f"clarify:{vendor_id}")
    out = tool_input(resp, "draft_email")
    out["issues"] = issues
    return out


def send_stub(rfx, vendor_id, vendor_email, subject, body):
    out = config.OUTBOX / "clarifications"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    p = out / f"{stamp:%Y%m%d-%H%M%S}_{vendor_id}.eml"
    p.write_text(f"From: {rfx['buyer']['email']}\nTo: {vendor_email}\nDate: {stamp:%a, %d %b %Y %H:%M}\n"
                 f"Subject: {subject}\nReply-To: rfq+{re.sub('[^a-z0-9-]', '', rfx['rfx_id'].lower())}-{vendor_id.lower()}@sunrisefoods.example\n\n{body}\n",
                 encoding="utf-8")
    return p
