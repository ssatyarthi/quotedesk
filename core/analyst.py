"""
The analyst: natural-language questions over the comparison. It answers only by
running SQL on the extracted data (DuckDB), so every number is reproducible -
the queries are shown to the buyer under each answer.
"""
import io
import json
from datetime import datetime

import pandas as pd
import plotly.express as px

from . import agent, award, store

TOOLS = [
    {"name": "run_sql", "description": "Run a read-only DuckDB SQL query over the procurement tables. Returns up to 80 rows as CSV. Use for every number you state.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "purpose": {"type": "string", "description": "one line: what this query checks"}},
                      "required": ["sql", "purpose"]}},
    {"name": "show_table", "description": "Show the buyer a table (result of a SQL query). Use for any result with more than ~5 rows. Give columns readable aliases.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "title": {"type": "string"}},
                      "required": ["sql", "title"]}},
    {"name": "show_chart", "description": "Show a chart built from a SQL query result.",
     "input_schema": {"type": "object", "properties": {
         "sql": {"type": "string"}, "kind": {"type": "string", "enum": ["bar", "grouped_bar", "line", "scatter"]},
         "x": {"type": "string"}, "y": {"type": "string"}, "color": {"type": "string", "description": "optional column for series"},
         "title": {"type": "string"}}, "required": ["sql", "kind", "x", "y", "title"]}},
    {"name": "export_file", "description": "Give the buyer a downloadable file of a query result.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}, "filename": {"type": "string"},
                                                        "format": {"type": "string", "enum": ["xlsx", "csv"]}},
                      "required": ["sql", "filename", "format"]}},
    {"name": "optimize_award", "description": "Compute the best award split in code (exact search over vendor combinations). Use for ANY split / award / 'cheapest per line' / 'how many vendors' question instead of hand-written SQL. Shows the split to the buyer.",
     "input_schema": {"type": "object", "properties": {
         "max_vendors": {"type": ["integer", "null"], "description": "Vendor cap. Default 3 (buyer policy). null = no cap."},
         "eligibility": {"type": "string", "enum": ["qualified", "qualified_plus_unresolved", "all"],
                         "description": "Which quality statuses may win. Default qualified_plus_unresolved (unresolved carry a warning). FAILED only if the buyer explicitly asks."},
         "price_basis": {"type": "string", "enum": ["effective", "landed"],
                         "description": "effective = landed + value of payment terms (default); landed = price + freight only"},
         "apply_conditional_discounts": {"type": "boolean", "description": "Apply a vendor's conditional discount only if this award meets its threshold. Default false."}},
         "required": ["max_vendors", "eligibility", "price_basis", "apply_conditional_discounts"]}},
    {"name": "write_memo", "description": "Produce the award recommendation memo for the approver. Every number must come from tool results in this conversation. All sections are required so the approver can defend the decision.",
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "recommendation": {"type": "string", "description": "2-3 sentences: what to award to whom and why"},
         "split": {"type": "array", "items": {"type": "object", "properties": {
             "vendor": {"type": "string"}, "lines": {"type": "string", "description": "line ids or count"},
             "annual_value_inr": {"type": "number"}, "quality_status": {"type": "string"}},
             "required": ["vendor", "lines", "annual_value_inr"]}},
         "total_inr": {"type": "number"}, "last_year_inr": {"type": "number"},
         "alternatives_considered": {"type": "array", "items": {"type": "string"}, "description": "e.g. cap 2 vs 3 vs no cap, qualified-only, with/without discount, each with its total"},
         "assumptions": {"type": "array", "items": {"type": "string"}, "description": "FX rate, freight per kg, cost of money, award date, discount treatment"},
         "excluded_vendors": {"type": "array", "items": {"type": "object", "properties": {"vendor": {"type": "string"}, "reason": {"type": "string"}}, "required": ["vendor", "reason"]}},
         "open_risks": {"type": "array", "items": {"type": "string"}, "description": "unconfirmed prices, unresolved quality items, inherited prices, FX exposure..."},
         "next_steps": {"type": "array", "items": {"type": "string"}}},
         "required": ["title", "recommendation", "split", "total_inr", "last_year_inr", "alternatives_considered", "assumptions", "excluded_vendors", "open_risks", "next_steps"]}},
    {"name": "get_evidence", "description": "Where a vendor's price for a line came from: source file/location/verbatim text, conversion steps, confidence and open flags.",
     "input_schema": {"type": "object", "properties": {"vendor_id": {"type": "string"}, "line_id": {"type": "string"}},
                      "required": ["vendor_id", "line_id"]}},
]

SYSTEM = """You are the procurement analyst inside a sourcing tool. The buyer is deciding a line-wise award on RFx {rfx_id} ({title}); roughly Rs 4 crore a year is at stake, so being right matters more than being fast.

Data (DuckDB):
{schema}

Vendors: {vendors}

Buyer-set assumptions currently in force: {assumptions}

How to work:
- Every number you state must come from a tool result in this conversation. Do arithmetic in SQL, not in your head.
- Compare vendors on landed_unit_price_inr (per RFx unit, INR, ex-GST, incl. assumed freight), or effective_unit_price_inr when payment terms matter. Use v_compare for priced, on-spec quotes. Annual value = landed price x annual_qty.
- A price with confidence = 'low' is not usable for an award until the buyer confirms it (it may be a misread or a unit error); exclude it from "cheapest" answers by default and say you did. 'buyer-confirmed' prices are fine.
- Before giving a recommendation or a "cheapest" answer, check the flags table and quotes.confidence for the rows you rely on. Name any open HIGH flag or low-confidence price that affects the answer, and say what would change if it were wrong.
- Be explicit about filters. If the buyer says "vendors who cleared the questionnaire", use quality_status = 'QUALIFIED', and say who was excluded and why (FAILED vs UNRESOLVED are different: UNRESOLVED might clear with one phone call). Offer the sensitivity if including an UNRESOLVED vendor changes the result.
- Coverage: if a line has no eligible quote under the buyer's filter, say so plainly instead of dropping it silently. Split awards have practical costs (MOQs, number of vendors to manage, lead times); mention them when relevant.
- Assumptions (FX rate, freight per kg, cost of money, conditional discounts) are the buyer's, not facts. Mention them when they move the answer.

Award policy (the buyer's decisions, apply them unless the buyer says otherwise):
- The award is the buyer's call based on their requirement. You recommend; say what the recommendation optimises for.
- Default cap: at most 3 vendors (1 = no backup, 2 = the survivor must absorb 100% if one fails, 4+ = volume falls below discount/MOQ thresholds and adds overhead). When recommending, also show the cost at cap 2 and no cap so the trade-off is visible in rupees.
- UNRESOLVED vendors are included with a warning, never silently dropped; say what would resolve them (a clarification email is available in the Review queue). FAILED vendors are excluded unless asked.
- Payment terms: later payment is better unless an early-payment discount beats holding the cash. effective_unit_price_inr already values this at the buyer's cost of money; landed_unit_price_inr does not.
- Use optimize_award for any split or award question; it searches every vendor combination exactly. Use SQL for everything else.
- Write for a busy category manager: lead with the answer in 1-3 sentences, then the evidence. Use show_table for anything longer than 5 rows rather than a markdown table. Format money in Indian style (lakh / crore) in prose.
- If the question can't be answered from this data, say exactly what is missing."""


def _fmt_df(df, n=80):
    if df.empty:
        return "(0 rows)"
    return f"{len(df)} rows" + (" (showing first 80)" if len(df) > n else "") + "\n" + df.head(n).to_csv(index=False)


class Analyst:
    def __init__(self, llm, tables, rfx):
        self.llm, self.tables, self.rfx = llm, tables, rfx
        self.con = store.connect(tables)
        self.artifacts = []
        v = tables["vendors"]
        vendors = "; ".join(f"{r.vendor_id}={r.vendor_name} [{getattr(r, 'quality_status', '?')}]" for r in v.itertuples())
        self.system = SYSTEM.format(rfx_id=rfx["rfx_id"], title=rfx["title"], schema=store.SCHEMA_DOC, vendors=vendors,
                                    assumptions=json.dumps(tables["assumptions"]))

    # --- tool handlers -------------------------------------------------
    def run_sql(self, sql, purpose=""):
        return _fmt_df(store.run_readonly(self.con, sql))

    def show_table(self, sql, title):
        df = store.run_readonly(self.con, sql, limit=1000)
        self.artifacts.append(dict(kind="table", title=title, df=df, sql=sql))
        return f"Shown to buyer. {_fmt_df(df, 30)}"

    def show_chart(self, sql, kind, x, y, title, color=None):
        df = store.run_readonly(self.con, sql, limit=2000)
        if color and color not in df:
            color = None
        if kind in ("bar", "grouped_bar"):
            fig = px.bar(df, x=x, y=y, color=color, barmode="group" if kind == "grouped_bar" else "relative", title=title)
        elif kind == "line":
            fig = px.line(df, x=x, y=y, color=color, markers=True, title=title)
        else:
            fig = px.scatter(df, x=x, y=y, color=color, title=title)
        self.artifacts.append(dict(kind="chart", title=title, fig=fig, sql=sql))
        return f"Chart shown ({len(df)} points)."

    def export_file(self, sql, filename, format="xlsx"):
        df = store.run_readonly(self.con, sql, limit=100000)
        buf = io.BytesIO()
        if format == "csv":
            buf.write(df.to_csv(index=False).encode())
            mime = "text/csv"
        else:
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="data")
                pd.DataFrame({"query": [sql], "generated": [datetime.now().isoformat(timespec="seconds")],
                              "assumptions": [json.dumps(self.tables["assumptions"])]}).to_excel(w, index=False, sheet_name="how this was made")
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = filename if filename.endswith(f".{format}") else f"{filename}.{format}"
        self.artifacts.append(dict(kind="file", title=name, data=buf.getvalue(), mime=mime, sql=sql))
        return f"File {name} ready for download ({len(df)} rows)."

    def optimize_award(self, max_vendors=3, eligibility="qualified_plus_unresolved", price_basis="effective",
                       apply_conditional_discounts=False):
        r = award.optimize(self.tables, max_vendors, eligibility, price_basis, apply_conditional_discounts)
        if not r["ok"]:
            return r["message"]
        title = (f"Award: cap {max_vendors or 'none'}, {award.ELIGIBILITY_LABEL[eligibility].lower()}, "
                 f"{'payment-adjusted' if price_basis == 'effective' else 'landed'} prices"
                 f"{', conditional discounts applied' if apply_conditional_discounts else ''}")
        show = r["split"][["vendor_name", "quality_status", "lines", "annual_inr", "conditional_discount_applied"]]
        self.artifacts.append(dict(kind="table", title=title, df=show, sql=f"optimize_award({max_vendors}, {eligibility}, {price_basis}, {apply_conditional_discounts})"))
        summary = {k: r[k] for k in ("total_inr", "last_year_same_lines_inr", "vs_last_year_pct", "lines_covered", "lines_total",
                                      "uncovered", "warnings", "vendors_considered")}
        return json.dumps(dict(summary=summary, split=show.to_dict("records"),
                               lines=r["lines"].to_dict("records")), default=str)

    def write_memo(self, title, recommendation, split, total_inr, last_year_inr, alternatives_considered,
                   assumptions, excluded_vendors, open_risks, next_steps):
        def inr(x):
            return f"Rs {x/1e7:,.2f} crore" if abs(x) >= 1e7 else f"Rs {x/1e5:,.1f} lakh"
        delta = (total_inr / last_year_inr - 1) * 100 if last_year_inr else None
        md = [f"**Recommendation.** {recommendation}", "",
              f"**Total:** {inr(total_inr)} a year vs {inr(last_year_inr)} at last year's prices"
              + (f" ({delta:+.1f}%)." if delta is not None else "."), "",
              "## Recommended split", "", "| Vendor | Lines | Annual value | Quality status |", "| --- | --- | --- | --- |"]
        md += [f"| {s_['vendor']} | {s_['lines']} | {inr(s_['annual_value_inr'])} | {s_.get('quality_status', '')} |" for s_ in split]
        for head, items in (("Alternatives considered", alternatives_considered), ("Assumptions", assumptions)):
            md += ["", f"## {head}", ""] + [f"- {x}" for x in items]
        md += ["", "## Excluded vendors", ""] + ([f"- **{e['vendor']}**: {e['reason']}" for e in excluded_vendors] or ["- None"])
        md += ["", "## Open risks before award", ""] + ([f"- {x}" for x in open_risks] or ["- None"])
        md += ["", "## Next steps", ""] + [f"1. {x}" for x in next_steps]
        markdown = "\n".join(md)
        self.artifacts.append(dict(kind="memo", title=title, markdown=markdown))
        return "Memo shown with Word and Markdown download buttons."

    def get_evidence(self, vendor_id, line_id):
        q = self.tables["quotes"]
        r = q[(q.vendor_id == vendor_id) & (q.line_id == line_id)]
        f = self.tables["flags"]
        fl = f[(f.vendor_id == vendor_id) & ((f.line_id == line_id) | f.line_id.isna())] if not f.empty else f
        cols = ["status", "raw_price", "raw_currency", "raw_unit", "units_per_price", "price_source", "rate_rule",
                "unit_price_inr", "freight_adder_inr", "landed_unit_price_inr", "confidence", "source_file",
                "source_location", "source_text", "steps", "match_note", "read_issue", "overridden"]
        rec = r[[c for c in cols if c in r]].to_dict("records")
        return json.dumps(dict(quote=rec, flags=fl[["severity", "category", "message", "status"]].to_dict("records") if not fl.empty else []),
                          default=str)

    # --- turn ------------------------------------------------------------
    def ask(self, history: list, question: str, on_step=None):
        self.artifacts = []
        history.append({"role": "user", "content": question})
        handlers = {t["name"]: getattr(self, t["name"]) for t in TOOLS}
        text, trace = agent.run(self.llm, self.system, history, TOOLS, handlers, tag="analyst", on_step=on_step)
        return text, trace, self.artifacts
