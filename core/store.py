"""DuckDB in-memory store over the normalized tables, used by the analyst agent and the UI."""
import duckdb
import pandas as pd

SCHEMA_DOC = """
TABLES (DuckDB SQL). All money in INR, ex-GST.

rfx_lines(line_id, item_type, ply, length_mm, width_mm, height_mm, board_gsm, burst_factor_min, print, uom, annual_qty,
          est_weight_g, description, last_year_price_inr, last_year_basis)
    -- the 30 items the buyer asked for. annual_qty is in uom (pcs / set / kg).

vendors(vendor_id, vendor_name, channel, received_on, quality_status, freight, payment_terms_days,
        early_payment_discount_pct, early_payment_days, lead_time_days,
        lead_time_text, moq_text, validity_text, price_variation_text, fx_clause_text, unconditional_discount_pct,
        conditional_discount_pct, conditional_discount_condition, conditional_discount_threshold_inr, notes)
    -- quality_status: QUALIFIED | UNRESOLVED (claim contradicted / question unanswered) | FAILED (failed a mandatory question)

quotes(vendor_id, vendor_name, line_id, description, uom, annual_qty, status, is_alternate, deviation,
       raw_price, raw_currency, raw_unit, units_per_price, price_source, rate_rule,
       unit_price_inr,            -- per RFx uom, after unit + currency conversion, before freight
       freight_adder_inr,         -- buyer's freight assumption for 'freight extra' vendors
       landed_unit_price_inr,     -- = unit_price_inr + freight_adder_inr   <-- USE THIS to compare
       landed_if_cond_discount,   -- landed price if the vendor's conditional discount is earned
       payment_adj_inr,           -- value of the vendor's payment terms vs the RFx's 60 days at the buyer's cost of money (+ = costs buyer)
       effective_unit_price_inr,  -- = landed_unit_price_inr + payment_adj_inr   <-- use when payment terms matter
       payment_note,
       annual_value_inr,          -- landed_unit_price_inr * annual_qty
       effective_annual_value_inr,
       vs_last_year_pct, ratio_to_peer_median, confidence, open_flags, overridden, quality_status,
       match_confidence, match_note, read_confidence, read_issue, source_file, source_location, source_text, steps)
    -- status: quoted | regret | on_enquiry | not_quoted.  confidence: high | medium | low | buyer-confirmed | n/a
    -- one row per vendor x line (plus extra rows where is_alternate = true)

questionnaire(vendor_id, q_id, question, mandatory, pass_rule, answer, evidence, result, reason)
    -- result: PASS | CLAIMED | FAIL | CONTRADICTED | NOT_ANSWERED | UNCLEAR | BELOW_PREFERENCE | INFO

attachments(vendor_id, file, doc_type, holder, reference_no, valid_until, validity, key_facts)

discounts(vendor_id, description, pct, condition_type, threshold_inr, condition_text, applies_to, source_text)

flags(flag_key, vendor_id, line_id, severity, category, message, status, resolution_note, source_file, source_location, source_text)
    -- open issues the buyer should know about. severity: high | medium | low | info. status: open | accepted | dismissed

VIEW v_compare = quotes WHERE status='quoted' AND NOT is_alternate AND landed_unit_price_inr IS NOT NULL
"""


def connect(tables: dict):
    con = duckdb.connect(":memory:")
    for name in ("lines", "vendors", "quotes", "questionnaire", "attachments", "discounts", "flags"):
        df = tables.get(name)
        if df is None or (isinstance(df, pd.DataFrame) and df.empty and len(df.columns) == 0):
            df = pd.DataFrame({"_empty": []})
        tname = "rfx_lines" if name == "lines" else name
        con.register(f"_{tname}", df)
        con.execute(f"CREATE TABLE {tname} AS SELECT * FROM _{tname}")
        con.unregister(f"_{tname}")
    try:
        con.execute("""CREATE VIEW v_compare AS SELECT * FROM quotes
                       WHERE status='quoted' AND NOT is_alternate AND landed_unit_price_inr IS NOT NULL""")
    except Exception:
        pass
    # Security: the analyst writes SQL, so after loading the tables, cut DuckDB off from the file system and
    # network (no read_text/read_csv/glob of server files such as secrets) and lock that setting.
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def run_readonly(con, sql: str, limit=500):
    s = sql.strip().rstrip(";")
    head = s.lstrip("(").split(None, 1)[0].lower() if s else ""
    if head not in ("select", "with", "describe", "show", "summarize"):
        raise ValueError("Only read-only queries (SELECT / WITH) are allowed.")
    if ";" in s:
        raise ValueError("One statement at a time.")
    return con.execute(s).fetchdf().head(limit)


def comparison_matrix(tables, value="landed_unit_price_inr", include_alternates=False, use_cond_discount=False):
    q = tables["quotes"]
    if q.empty:
        return pd.DataFrame()
    d = q[q["status"] == "quoted"]
    if not include_alternates:
        d = d[~d["is_alternate"]]
    col = "landed_if_cond_discount" if use_cond_discount else value
    d = d.assign(_v=d[col].fillna(d[value]) if use_cond_discount else d[value])
    d = d.assign(_vendor=d["vendor_id"] + d["is_alternate"].map({True: " (alt)", False: ""}))
    m = d.pivot_table(index="line_id", columns="_vendor", values="_v", aggfunc="first")
    lines = tables["lines"].set_index("line_id")
    m = lines[["description", "uom", "annual_qty", "last_year_price_inr"]].join(m, how="left")
    return m
