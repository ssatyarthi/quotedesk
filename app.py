"""
QuoteDesk - draft an RFx, read whatever vendors send back, interrogate it.
Run:  streamlit run app.py
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import award, config, ingest, normalize, store
from core.config import Assumptions
from core.extract import extract_all, extract_vendor, load_all_cached
from core.llm import LLM, LLMError, usage_summary

st.set_page_config(page_title="QuoteDesk", page_icon="📦", layout="wide")

st.markdown("""
<style>
  .block-container {padding-top: 1.6rem; max-width: 1500px;}
  .qd-pill {display:inline-block;padding:2px 9px;border-radius:999px;font-size:0.78rem;font-weight:600;margin-right:4px}
  .qd-high {background:#fde2e1;color:#9b1c1c} .qd-medium {background:#fff1d6;color:#8a5300}
  .qd-low {background:#e6f0ff;color:#1e429f} .qd-info {background:#eef0f3;color:#444}
  .qd-ok {background:#def7ec;color:#03543f} .qd-bad {background:#fde2e1;color:#9b1c1c} .qd-warn {background:#fff1d6;color:#8a5300}
  .qd-src {font-family: ui-monospace, Menlo, monospace; font-size:0.82rem; background:rgba(127,127,127,.10);
           padding:6px 10px; border-radius:6px; border-left:3px solid #d97706;}
  div[data-testid="stMetricValue"] {font-size: 1.5rem;}
</style>""", unsafe_allow_html=True)



def _secret(name):
    import os
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, "")


API_KEY = config.API_KEY or _secret("ANTHROPIC_API_KEY")
APP_PASSWORD = _secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.markdown("### 📦 QuoteDesk")
    pw = st.text_input("Password", type="password")
    if pw:
        if pw == APP_PASSWORD:
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()

QUALITY_PILL = {"QUALIFIED": ("qd-ok", "Qualified"), "UNRESOLVED": ("qd-warn", "Unresolved"), "FAILED": ("qd-bad", "Failed quality")}
RESULT_ICON = {"PASS": "✅ Pass", "CLAIMED": "☑️ Claimed", "FAIL": "❌ Fail", "CONTRADICTED": "⚠️ Contradicted",
               "NOT_ANSWERED": "❔ Not answered", "UNCLEAR": "❔ Unclear", "BELOW_PREFERENCE": "➖ Below pref.", "INFO": "ℹ️"}


def pill(text, cls):
    return f'<span class="qd-pill {cls}">{text}</span>'


def inr(x, short=True):
    if x is None or pd.isna(x):
        return "—"
    if short and abs(x) >= 1e7:
        return f"₹{x/1e7:,.2f} Cr"
    if short and abs(x) >= 1e5:
        return f"₹{x/1e5:,.1f} L"
    return f"₹{x:,.2f}"


# --------------------------------------------------------------------------- state
ss = st.session_state
if "rfx" not in ss:
    ss.rfx = json.loads(config.RFX_SEED.read_text(encoding="utf-8"))
    ss.manifest = ingest.load_manifest()
    ss.assumptions = Assumptions(award_date="2026-10-22")
    ss.ask_display, ss.ask_history = [], []
    ss.cp_display, ss.cp_history = [], []
    from core.copilot import empty_draft
    ss.draft = empty_draft()
    ss.sent = None

VIDS = [m["vendor_id"] for m in ss.manifest]
VNAME = {m["vendor_id"]: m["vendor_name"] for m in ss.manifest}


@st.cache_resource(show_spinner=False)
def get_llm(key):
    return LLM(key)


def llm_or_none():
    try:
        return get_llm(API_KEY)
    except LLMError:
        return None


def tables():
    ext = load_all_cached(VIDS, ss.rfx)
    return normalize.build(ss.rfx, ext, ss.manifest, ss.assumptions), ext


def trust_meter(T):
    q = T["quotes"]
    if q.empty or "annual_value_inr" not in q:
        return None
    p = q[(q.status == "quoted") & (~q.is_alternate) & q.landed_unit_price_inr.notna()]
    if p.empty:
        return None
    ok = p[p.confidence.isin(["high", "buyer-confirmed"])]["annual_value_inr"].sum()
    f = T["flags"]
    high = int(f[(f.status == "open") & (f.severity == "high")]["flag_key"].nunique()) if not f.empty else 0
    return dict(pct=ok / p["annual_value_inr"].sum() * 100, high=high, low=int((p.confidence == "low").sum()))


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 📦 QuoteDesk")
    st.caption("Kill the quote spreadsheet · Sunrise Foods · Corrugated packaging")
    T, EXT = tables()
    open_flags = T["flags"][T["flags"]["status"] == "open"] if not T["flags"].empty else pd.DataFrame()
    n_groups = open_flags["flag_key"].nunique() if not open_flags.empty else 0
    NAV = ["1 · Draft RFx", "2 · Inbox", "3 · Compare", "4 · Review queue", "5 · Ask", "6 · Award"]
    page = st.radio("Go to", NAV, key="nav", label_visibility="collapsed",
                    format_func=lambda x: f"{x} ({n_groups})" if x.startswith("4") else x)
    tm = trust_meter(T)
    if tm:
        st.markdown(f"**Award-ready:** {tm['pct']:.0f}% of quoted spend confirmed")
        st.progress(min(tm["pct"] / 100, 1.0))
        st.caption(f"{tm['high']} high-severity issues open · {tm['low']} price{'s' if tm['low'] != 1 else ''} can't be used until confirmed")
    st.divider()
    with st.expander("Buyer assumptions", expanded=False):
        st.caption("Things the system can't know. Every number that depends on them says so.")
        A = ss.assumptions
        A.usd_inr = st.number_input("USD → INR rate", value=float(A.usd_inr), step=0.25, format="%.2f")
        A.usd_inr_asof = st.text_input("Rate source / date", A.usd_inr_asof)
        A.freight_inr_per_kg = st.number_input("Freight for 'freight extra' vendors (₹/kg)", value=float(A.freight_inr_per_kg), step=0.25)
        A.award_date = st.text_input("Award date (for certificate validity)", A.award_date)
        A.apply_conditional_discounts = st.toggle("Apply conditional discounts", A.apply_conditional_discounts)
        A.value_payment_terms = st.toggle("Value payment terms (later is better)", A.value_payment_terms,
                                          help="Paying earlier than the RFx's 60 days costs the buyer; an early-payment discount is used only if it beats holding the cash.")
        A.cost_of_money_pct = st.number_input("Cost of money (% per year)", value=float(A.cost_of_money_pct), step=0.5)
        A.include_alternates = st.toggle("Show alternate (off-spec) offers", A.include_alternates)
    n_calls, usd = usage_summary()
    llm = llm_or_none()
    if llm:
        st.caption(f"🟢 AI connected · {n_calls} calls · ~${usd:.2f} estimated (exact spend: Anthropic Console)")
    else:
        st.error("No API key. Add ANTHROPIC_API_KEY to .env (local) or the app's Secrets (hosted). See README.")

T, EXT = tables()  # rebuild with any changed assumptions


# --------------------------------------------------------------------------- helpers
def file_preview(path: Path, key):
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg", ".png"):
        st.image(str(path), width=760)
    elif ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for i, pg in enumerate(pdf.pages[:3], 1):
                    st.image(pg.to_image(resolution=110).original, caption=f"{path.name} · page {i}", width=760)
        except Exception:
            st.code(ingest.preview_text(path)[:6000])
    elif ext == ".xlsx":
        for sheet, df in pd.read_excel(path, sheet_name=None, header=None).items():
            df = df.astype(str).replace("nan", "")
            st.caption(f"sheet: {sheet}")
            st.dataframe(df.fillna(""), width="stretch", hide_index=True)
    else:
        st.code(ingest.preview_text(path)[:8000], language=None)
    st.download_button("Download original", path.read_bytes(), file_name=path.name, key=f"dl_{key}")


def flag_pills(df):
    if df.empty:
        return ""
    return " ".join(pill(f"{n} {s}", f"qd-{s}") for s, n in df["severity"].value_counts().items())


def render_clarifications(T, llm, only=None):
    """One AI-drafted email per vendor with open issues (PRD R4.6). Sending is stubbed."""
    from core import clarify
    from core.copilot import VENDOR_LIST
    emails = {v["vendor_id"]: v["email"] for v in VENDOR_LIST}
    ss.setdefault("clar", {})
    vids = [v for v in VIDS if clarify.open_issues(T, v) and (only is None or v in only)]
    if not vids:
        return
    with st.expander(f"✉️ Clarification emails: {len(vids)} vendors have open issues", expanded=only is not None):
        st.caption("One email per vendor asking for exactly what's missing, so a single reply resolves their flags. Edit before sending; sending is stubbed (written to outbox/).")
        for vid in vids:
            n = len(clarify.open_issues(T, vid))
            c1, c2 = st.columns([6, 2])
            c1.markdown(f"**{VNAME[vid]}** · {n} open issues")
            if c2.button("Draft email", key=f"cl_{vid}_{only}", disabled=not llm):
                with st.spinner("Drafting…"):
                    try:
                        ss.clar[vid] = clarify.draft(llm, T, ss.rfx, vid, VNAME[vid], reply_by=None)
                    except Exception as e:
                        st.error(str(e))
            dr = ss.clar.get(vid)
            if dr and dr.get("body"):
                subj = st.text_input("Subject", dr["subject"], key=f"cs_{vid}_{only}")
                body = st.text_area("Body", dr["body"], height=260, key=f"cb_{vid}_{only}")
                if st.button("Send (stubbed)", key=f"csend_{vid}_{only}", type="primary"):
                    p_ = clarify.send_stub(ss.rfx, vid, emails.get(vid, "vendor@example.com"), subj, body)
                    st.success(f"Written to {p_.relative_to(config.ROOT)}. In production this goes out by email and the reply is read back in automatically.")


# =========================================================================== 1. RFx
if page.startswith("1"):
    from core import copilot as cp
    st.markdown("### Draft an RFx with the co-pilot")
    tab_new, tab_sent = st.tabs(["✍️ Co-pilot (new RFx)", f"📨 Sent: {ss.rfx['rfx_id']}"])

    with tab_new:
        left, right = st.columns([5, 6], gap="large")
        with left:
            st.caption("Describe what you need. The co-pilot drafts lines from the item master, sets terms and a questionnaire, and asks only what it can't assume.")
            starters = ["Re-source all our corrugated boxes, trays, partitions, pads and the roll for the next 12 months. Same items as last year. Invite all approved vendors, replies due in 9 days.",
                        "Only the 5-ply range this time, and add a 600x400x250 5-ply box, 2-colour, about 40,000 a year."]
            for i, s in enumerate(starters):
                if st.button(s, key=f"st{i}", width="stretch"):
                    ss.cp_pending = s
            for m in ss.cp_display:
                with st.chat_message(m["role"]):
                    st.markdown(m["text"])
                    if m.get("trace"):
                        st.caption("Edited draft: " + ", ".join(t["tool"] for t in m["trace"]))
            msg = st.chat_input("e.g. add 7-ply bins for the Bawal plant…", key="cp_in") or ss.pop("cp_pending", None)
            if msg:
                if not llm:
                    st.error("Add an API key first.")
                else:
                    ss.cp_display.append(dict(role="user", text=msg))
                    co = cp.Copilot(llm, ss.draft)
                    n_before = len(ss.cp_history)
                    with st.spinner("Drafting…"):
                        try:
                            text, trace = co.chat(ss.cp_history, msg)
                        except Exception as e:
                            del ss.cp_history[n_before:]
                            text, trace = f"⚠️ {e}", []
                    ss.cp_display.append(dict(role="assistant", text=text, trace=trace))
                    st.rerun()
        with right:
            d = ss.draft
            chk = cp.check_draft(d)
            st.markdown(f"**{d.get('title') or 'Untitled draft'}**  ·  due {d.get('due_on') or '—'}")
            if d["lines"]:
                st.dataframe(pd.DataFrame(d["lines"])[[c for c in ["line_id", "description", "uom", "annual_qty", "est_weight_g"] if c in pd.DataFrame(d["lines"])]],
                             hide_index=True, width="stretch", height=300)
            if d["terms"]:
                st.markdown("**Terms** · " + " · ".join(f"*{k.replace('_',' ')}*: {v}" for k, v in d["terms"].items()))
            if d["questionnaire"]:
                st.markdown("**Questionnaire**")
                st.dataframe(pd.DataFrame(d["questionnaire"])[["q_id", "question", "mandatory", "pass_rule"]], hide_index=True, width="stretch")
            if d["invited_vendors"]:
                st.markdown("**Invited:** " + ", ".join(VNAME.get(v, v) for v in d["invited_vendors"]))
            if chk["gaps"] and (d["lines"] or d["title"]):
                st.warning("Not ready yet:\n- " + "\n- ".join(chk["gaps"][:8]))
            if st.button("Send to vendors (stubbed email)", type="primary", disabled=not chk["ready"]):
                sent, paths = cp.send_stub(d)
                ss.sent = (sent, [str(p) for p in paths])
            if ss.sent:
                sent, paths = ss.sent
                st.success(f"{sent['rfx_id']} written to outbox/ as {len(paths)} emails (SMTP is stubbed).")
                for p in paths:
                    with st.expander(Path(p).name):
                        st.code(Path(p).read_text(encoding="utf-8"), language=None)

    with tab_sent:
        r = ss.rfx
        st.markdown(f"**{r['rfx_id']}** · {r['title']}")
        st.caption(f"Issued {r['issued_on']} · due {r['due_on']} · buyer {r['buyer']['name']} · this is the RFx the 5 replies in the Inbox answer.")
        c1, c2 = st.columns([3, 2])
        with c1:
            st.dataframe(pd.DataFrame(r["lines"])[["line_id", "description", "uom", "annual_qty", "est_weight_g"]],
                         hide_index=True, width="stretch", height=420)
        with c2:
            st.markdown("**Terms**")
            for k, v in r["terms"].items():
                st.markdown(f"- *{k.replace('_',' ')}*: {v}")
            st.markdown("**Questionnaire**")
            st.dataframe(pd.DataFrame(r["questionnaire"])[["q_id", "question", "mandatory", "pass_rule"]], hide_index=True, width="stretch")

# =========================================================================== 2. Inbox
elif page.startswith("2"):
    st.markdown(f"### Inbox · replies to {ss.rfx['rfx_id']}")
    st.caption("Five vendors, five formats, nobody used the template. The AI reads each response as-is; code then converts units and currency.")
    v = T["vendors"]
    c1, c2, c3 = st.columns([2, 2, 6])
    force = c2.checkbox("Re-read even if cached", value=False)
    if c1.button("🤖 Read all responses", type="primary", disabled=not llm):
        prog = st.status("Reading vendor responses…", expanded=True)
        try:
            llm._pick_model()
            prog.write(f"Model: {llm.model}")
            _, errs = extract_all(llm, VIDS, ss.rfx, force=force,
                                  on_done=lambda vid, err: prog.write(f"{'❌' if err else '✅'} {VNAME[vid]}{(': ' + err) if err else ''}"))
            prog.update(label="Done" if not errs else f"Done with {len(errs)} error(s)", state="complete" if not errs else "error")
        except Exception as e:
            prog.update(label=f"Failed: {e}", state="error")
        st.rerun()

    with st.expander("➕ Add a vendor reply (any format: Excel, PDF, Word, photo, or pasted email)"):
        with st.form("add_vendor", clear_on_submit=True):
            nm = st.text_input("Vendor name")
            body = st.text_area("Email body (optional)", height=100)
            ups = st.file_uploader("Attachments", accept_multiple_files=True,
                                   type=["xlsx", "pdf", "docx", "jpg", "jpeg", "png", "txt"])
            if st.form_submit_button("Add to inbox") and nm and (body or ups):
                nxt = f"V{max(int(m['vendor_id'][1:]) for m in ss.manifest) + 1}"
                d_ = config.RESPONSES / nxt
                d_.mkdir(parents=True, exist_ok=True)
                if body:
                    (d_ / "email.txt").write_text(f"From: {nm}\nSubject: Re: {ss.rfx['rfx_id']}\n\n{body}", encoding="utf-8")
                for u in ups or []:
                    (d_ / Path(u.name).name).write_bytes(u.getvalue())
                ss.manifest.append(dict(vendor_id=nxt, vendor_name=nm, channel="added in demo",
                                        received_on=datetime.now().date().isoformat(), files=[p.name for p in d_.iterdir()], added=True))
                (config.RESPONSES / "manifest.json").write_text(json.dumps(ss.manifest, indent=2), encoding="utf-8")
                st.rerun()

    for m in ss.manifest:
        vid = m["vendor_id"]
        ext = EXT.get(vid)
        vr = v[v.vendor_id == vid].iloc[0] if not v.empty and vid in set(v.vendor_id) else None
        status = pill("read", "qd-ok") if ext else pill("not read yet", "qd-info")
        q = ""
        if vr is not None and isinstance(vr.get("quality_status"), str):
            cls, lab = QUALITY_PILL[vr["quality_status"]]
            q = pill(lab, cls)
        with st.container(border=True):
            a, b = st.columns([7, 2])
            a.markdown(f"**{m['vendor_name']}** · {m['channel']} · received {m['received_on']}  {status}{q}", unsafe_allow_html=True)
            if m.get("added") and b.button("Remove", key=f"rm_{vid}"):
                import shutil
                shutil.rmtree(config.RESPONSES / vid, ignore_errors=True)
                (config.EXTRACTIONS / f"{vid}.json").unlink(missing_ok=True)
                ss.manifest = [x for x in ss.manifest if x["vendor_id"] != vid]
                (config.RESPONSES / "manifest.json").write_text(json.dumps(ss.manifest, indent=2), encoding="utf-8")
                st.rerun()
            if b.button("Re-read with AI", key=f"rr_{vid}", disabled=not llm):
                with st.spinner(f"Reading {m['vendor_name']}…"):
                    try:
                        extract_vendor(llm, vid, ss.rfx, force=True)
                    except Exception as e:
                        st.error(str(e))
                st.rerun()
            files = sorted(ingest.vendor_files(vid), key=lambda f: (f.name != "email.txt",
                           any(k in f.name.lower() for k in ("iso", "certif", "test_report", "lab_")), f.name))
            tabs = st.tabs([f.name for f in files] + (["🤖 What the AI read"] if ext else []))
            for t, f in zip(tabs, files):
                with t:
                    file_preview(f, f"{vid}_{f.name}")
            if ext:
                with tabs[-1]:
                    qd = T["quotes"]
                    vq = qd[qd.vendor_id == vid] if not qd.empty else qd
                    cnt = vq["status"].value_counts().to_dict() if not vq.empty else {}
                    st.markdown(f"Lines priced: **{int((vq['status']=='quoted').sum() - vq['is_alternate'].sum()) if not vq.empty else 0}/30** · "
                                f"regret/on enquiry: {cnt.get('regret',0)+cnt.get('on_enquiry',0)} · not mentioned: {cnt.get('not_quoted',0)} · "
                                f"alternates: {int(vq['is_alternate'].sum()) if not vq.empty else 0}")
                    for note in ext.get("things_a_buyer_should_know", []):
                        st.markdown(f"- {note}")
                    with st.expander("Raw structured output (JSON)"):
                        st.json({k: val for k, val in ext.items() if not k.startswith("_")}, expanded=False)

# =========================================================================== 3. Compare
elif page.startswith("3"):
    st.markdown("### Side-by-side comparison")
    q = T["quotes"]
    if q.empty:
        st.info("Nothing read yet. Go to **2 · Inbox** and click **Read all responses**.")
        st.stop()
    A = ss.assumptions
    lines = T["lines"]
    vend = T["vendors"]

    # KPI row
    vc = q[(q.status == "quoted") & (~q.is_alternate) & q.landed_unit_price_inr.notna() & (q.confidence != "low")]
    best_all = vc.loc[vc.groupby("line_id")["landed_unit_price_inr"].idxmin()]
    qual = vc[vc.quality_status == "QUALIFIED"]
    best_q = qual.loc[qual.groupby("line_id")["landed_unit_price_inr"].idxmin()] if not qual.empty else qual
    ly_total = (lines["last_year_price_inr"] * lines["annual_qty"]).sum()
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Last year's prices × volume", inr(ly_total))
    k2.metric("Cheapest per line, any vendor", inr(best_all["annual_value_inr"].sum()),
              f"{(best_all['annual_value_inr'].sum()/ly_total-1)*100:+.1f}% vs LY", delta_color="inverse")
    rec = award.optimize(T, 3, "qualified_plus_unresolved", "effective" if A.value_payment_terms else "landed", A.apply_conditional_discounts)
    k3.metric("Recommended award (max 3 vendors)", inr(rec["total_inr"]) if rec["ok"] else "—",
              f"{rec['vs_last_year_pct']:+.1f}% vs LY · details in 6 · Award" if rec["ok"] else "", delta_color="inverse")
    hi = T["flags"][(T["flags"].status == "open") & (T["flags"].severity == "high")] if not T["flags"].empty else pd.DataFrame()
    k4.metric("Open high-severity issues", hi["flag_key"].nunique() if not hi.empty else 0)
    allp = q[(q.status == "quoted") & (~q.is_alternate) & q.landed_unit_price_inr.notna()]
    tm = trust_meter(T)
    k5.metric("Spend on confirmed prices", f"{tm['pct']:.0f}%" if tm else "—", f"{len(allp)} prices read", delta_color="off")
    st.caption(f"Landed ₹ per RFx unit, ex-GST. USD at {A.usd_inr} ({A.usd_inr_asof}); freight-extra vendors + ₹{A.freight_inr_per_kg}/kg; "
               f"conditional discounts {'applied' if A.apply_conditional_discounts else 'not applied'}.")

    c1, c2 = st.columns([3, 3])
    show = c1.radio("Show", ["Landed ₹ per unit", "Annual value"], horizontal=True)
    elig = c2.selectbox("Who can be 'best' on a line", list(award.ELIGIBILITY), index=1,
                        format_func=award.ELIGIBILITY_LABEL.get,
                        help="Unresolved vendors (e.g. expired certificate, unanswered questionnaire) are included with a warning by default; failed vendors are not.")

    d = q[q.status.isin(["quoted", "regret", "on_enquiry", "not_quoted"])].copy()
    if not A.include_alternates:
        d = d[~d.is_alternate]
    qs_map = dict(zip(vend.vendor_id, vend.get("quality_status", pd.Series(dtype=str))))
    badge = {"UNRESOLVED": " ⚠", "FAILED": " ✕"}
    d["vcol"] = (d.vendor_id.map(lambda x: f"{x} · {VNAME[x].split(' (')[0].split(' ')[0]}{badge.get(qs_map.get(x), '')}")
                 + d.is_alternate.map({True: " (alt)", False: ""}))
    priced = d.landed_unit_price_inr.copy()
    if A.apply_conditional_discounts:
        priced = d.landed_if_cond_discount.fillna(d.landed_unit_price_inr)
    d["val"] = priced if show.startswith("Landed") else priced * d.annual_qty
    cols = sorted(d.vcol.unique())
    disp = pd.DataFrame(index=lines.line_id, columns=cols, dtype=object)
    css = pd.DataFrame("", index=lines.line_id, columns=cols)
    for r in d.itertuples():
        if r.status != "quoted":
            disp.loc[r.line_id, r.vcol] = {"regret": "regret", "on_enquiry": "on enquiry", "not_quoted": "— missing"}[r.status]
            css.loc[r.line_id, r.vcol] = "color:#999;font-style:italic"
            continue
        if pd.isna(r.val):
            disp.loc[r.line_id, r.vcol] = "⚠ unreadable"
            css.loc[r.line_id, r.vcol] = "background-color:#fde2e1"
            continue
        mark = {"high": "", "medium": " *", "low": " ⚠", "buyer-confirmed": " ✔"}.get(r.confidence, "")
        disp.loc[r.line_id, r.vcol] = (f"{r.val:,.2f}" if show.startswith("Landed") else inr(r.val)) + mark
        if r.confidence == "low":
            css.loc[r.line_id, r.vcol] = "background-color:#fde2e1"
        elif r.quality_status == "FAILED":
            css.loc[r.line_id, r.vcol] = "color:#9b9b9b"
    # best per line
    pool = d[(d.status == "quoted") & d.val.notna() & (~d.is_alternate) & (d.confidence != "low")]  # unconfirmed low-confidence prices never "win"
    pool = pool[pool.quality_status.isin(award.ELIGIBILITY[elig])]
    for lid, g in pool.groupby("line_id"):
        b = g.loc[g.val.idxmin()]
        css.loc[lid, b.vcol] += ";background-color:#d1fadf;font-weight:700"
    def short(r):
        if r.length_mm and not pd.isna(r.length_mm):
            pr = {"Plain": "plain", "1 colour flexo": "1C", "2 colour flexo": "2C", "4 colour flexo": "4C"}.get(r.print, r.print)
            kind = "tray" if "tray" in r.item_type.lower() else ("bin" if "bin" in r.item_type.lower() else "RSC")
            return f"{r.ply}-ply {kind} {int(r.length_mm)}×{int(r.width_mm)}×{int(r.height_mm)} {pr}"
        return r.item_type
    base = pd.DataFrame({"Item": lines.apply(short, axis=1).values, "UoM": lines.uom.values,
                         "Qty/yr": lines.annual_qty.map(lambda x: f"{x:,.0f}").values,
                         "LY ₹/unit": lines.last_year_price_inr.map(lambda x: f"{x:,.2f}" if pd.notna(x) else "—").values},
                        index=lines.line_id)
    full = base.join(disp)
    full_css = pd.DataFrame("", index=base.index, columns=base.columns).join(css)
    styled = full.style.apply(lambda _: full_css, axis=None)
    st.caption("🟩 best on the line (low-confidence prices can't win until you confirm them in the Review queue)  ·  * medium confidence  ·  ⚠ low confidence / check before use  ·  ✔ buyer-confirmed  ·  ⚠ in a vendor header = unresolved quality item (included with a warning) · ✕ / grey = failed quality. "
               "**Select a row** to see where each number came from.")
    ev = st.dataframe(styled, width="stretch", height=560, on_select="rerun", selection_mode="single-row",
                      column_config={"Item": st.column_config.TextColumn(width="medium")})

    sel = ev.selection.rows if ev and hasattr(ev, "selection") else []
    if sel:
        lid = full.index[sel[0]]
        line = lines[lines.line_id == lid].iloc[0]
        st.markdown(f"#### {lid} · {line.description}")
        st.caption(f"{line.annual_qty:,} {line.uom}/yr · est. weight {line.est_weight_g} g · last year {inr(line.last_year_price_inr, False)} ({line.last_year_basis})")
        lq = q[q.line_id == lid]
        cols_ = st.columns(len(VIDS))
        fl = T["flags"]
        for col, vid in zip(cols_, VIDS):
            with col:
                rows = lq[lq.vendor_id == vid]
                st.markdown(f"**{VNAME[vid]}**")
                if rows.empty:
                    st.caption("not read")
                    continue
                for r in rows.itertuples():
                    if r.is_alternate:
                        st.markdown(pill("ALTERNATE", "qd-info"), unsafe_allow_html=True)
                    if r.status != "quoted":
                        st.caption(r.status.replace("_", " "))
                        continue
                    st.markdown(f"### {inr(r.landed_unit_price_inr, False)}")
                    st.markdown(pill(f"confidence: {r.confidence}", {"high": "qd-ok", "medium": "qd-warn", "low": "qd-bad"}.get(r.confidence, "qd-info")),
                                unsafe_allow_html=True)
                    if r.source_text:
                        st.markdown(f'<div class="qd-src">“{r.source_text}”<br><small>{r.source_file} · {r.source_location}</small></div>', unsafe_allow_html=True)
                    if r.steps:
                        st.code(r.steps, language=None, wrap_lines=True)
                    if A.value_payment_terms and pd.notna(getattr(r, "effective_unit_price_inr", None)) and abs(r.payment_adj_inr or 0) >= 0.005:
                        st.caption(f"With payment terms: {inr(r.effective_unit_price_inr, False)} ({r.payment_note})")
                    my = fl[(fl.vendor_id == vid) & ((fl.line_id == lid))] if not fl.empty else fl
                    for f in my.itertuples():
                        st.markdown(pill(f.severity, f"qd-{f.severity}") + f" {f.message}", unsafe_allow_html=True)

    st.markdown("#### Vendors: quality questionnaire and terms")
    qn = T["questionnaire"]
    if not qn.empty:
        qm = qn.pivot_table(index=["q_id", "question", "mandatory"], columns="vendor_id", values="result", aggfunc="first").map(lambda x: RESULT_ICON.get(x, x))
        qm.columns = [VNAME[c] for c in qm.columns]
        overall = {VNAME[r.vendor_id]: QUALITY_PILL.get(r.quality_status, ("", r.quality_status))[1] for r in vend.itertuples() if isinstance(getattr(r, "quality_status", None), str)}
        st.dataframe(qm.reset_index().drop(columns="mandatory").set_index("q_id"), width="stretch")
        st.markdown("**Overall:** " + " · ".join(f"{k}: **{v}**" for k, v in overall.items()))
        with st.expander("Why each answer passed or failed"):
            st.dataframe(qn[["vendor_id", "q_id", "result", "reason", "answer"]], hide_index=True, width="stretch")
    tcols = ["vendor_name", "quality_status", "freight", "payment_terms_days", "early_payment_discount_pct", "lead_time_text", "moq_text", "validity_text", "price_variation_text",
             "fx_clause_text", "conditional_discount_pct", "conditional_discount_condition"]
    st.dataframe(vend[[c for c in tcols if c in vend]], hide_index=True, width="stretch")
    if not T["attachments"].empty:
        st.markdown("**Attached documents**")
        st.dataframe(T["attachments"], hide_index=True, width="stretch")

# =========================================================================== 4. Review
elif page.startswith("4"):
    st.markdown("### Review queue")
    st.caption("Everything the system wasn't sure about, worst first. Decide once per issue; line-level price problems can be overridden after you check with the vendor.")
    f = T["flags"]
    if f.empty:
        st.info("No flags yet. Read the responses first.")
        st.stop()
    render_clarifications(T, llm)
    show_all = st.toggle("Show resolved too", value=False)
    view = f if show_all else f[f.status == "open"]
    decisions = normalize.load_json(config.DECISIONS, {})
    overrides = normalize.load_json(config.OVERRIDES, {})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    groups = sorted(view.groupby("flag_key"), key=lambda kv: (order[kv[1].severity.iloc[0]], kv[0]))
    st.markdown(f"**{len(groups)}** issues · " + flag_pills(view.drop_duplicates("flag_key")), unsafe_allow_html=True)
    for key, g in groups:
        r0 = g.iloc[0]
        vid = r0.vendor_id
        lines_ = [x for x in g.line_id.dropna().unique()]
        title = f"{VNAME.get(vid, vid)} · {r0.category.replace('_', ' ')}" + (f" · {', '.join(lines_[:6])}{'…' if len(lines_) > 6 else ''}" if lines_ else "")
        with st.container(border=True):
            st.markdown(pill(r0.severity, f"qd-{r0.severity}") + f" **{title}**" + (f"  {pill(r0.status, 'qd-info')}" if r0.status != "open" else ""),
                        unsafe_allow_html=True)
            msgs = g.message.unique()
            for m_ in msgs[:4]:
                st.markdown(f"- {m_}")
            if len(msgs) > 4:
                st.caption(f"+ {len(msgs) - 4} similar")
            if r0.source_text:
                st.markdown(f'<div class="qd-src">“{r0.source_text}”<br><small>{r0.source_file} · {r0.source_location}</small></div>', unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns([3, 2, 2, 3])
            note = c1.text_input("Note", key=f"n_{key}", placeholder="e.g. confirmed on call with vendor", label_visibility="collapsed")
            if c2.button("Accept as is", key=f"a_{key}"):
                decisions[key] = dict(status="accepted", note=note, at=datetime.now().isoformat(timespec="minutes"))
                config.DECISIONS.write_text(json.dumps(decisions, indent=2))
                st.rerun()
            if c3.button("Dismiss", key=f"d_{key}"):
                decisions[key] = dict(status="dismissed", note=note, at=datetime.now().isoformat(timespec="minutes"))
                config.DECISIONS.write_text(json.dumps(decisions, indent=2))
                st.rerun()
            if len(lines_) == 1 and r0.severity == "high" and r0.category not in ("silently_missing",):
                lid = lines_[0]
                qrow = T["quotes"][(T["quotes"].vendor_id == vid) & (T["quotes"].line_id == lid) & (~T["quotes"].is_alternate)]
                cur = float(qrow.unit_price_inr.iloc[0]) if not qrow.empty and pd.notna(qrow.unit_price_inr.iloc[0]) else 0.0
                with c4.popover("Override price"):
                    val = st.number_input(f"Confirmed ₹ per unit (ex-freight) for {lid}", value=cur, step=0.01, key=f"ov_{key}")
                    if st.button("Save override", key=f"ovs_{key}"):
                        overrides[f"{vid}|{lid}"] = dict(unit_price_inr=val, note=note or "confirmed by buyer", at=datetime.now().isoformat(timespec="minutes"))
                        config.OVERRIDES.write_text(json.dumps(overrides, indent=2))
                        decisions[key] = dict(status="accepted", note=f"overridden to ₹{val:.2f}. {note}", at=datetime.now().isoformat(timespec="minutes"))
                        config.DECISIONS.write_text(json.dumps(decisions, indent=2))
                        st.rerun()
    if decisions or overrides:
        with st.expander("Audit trail (decisions and overrides)"):
            st.json(dict(decisions=decisions, overrides=overrides))
            if st.button("Reset all decisions"):
                config.DECISIONS.unlink(missing_ok=True)
                config.OVERRIDES.unlink(missing_ok=True)
                st.rerun()

# =========================================================================== 5. Ask
elif page.startswith("5"):
    from core.analyst import Analyst
    from core.memo import md_to_docx
    st.markdown("### Ask the analyst")
    if T["quotes"].empty:
        st.info("Read the responses first (2 · Inbox).")
        st.stop()
    st.caption("Answers come from SQL over the extracted data; every query is shown under the answer. Assumptions in the sidebar apply.")
    sugg = ["What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire? Total vs last year?",
            "Which numbers on this comparison should I not trust yet, and why?",
            "If Rajhans sends a renewed ISO certificate and earns its 5% discount, what does the split look like then?",
            "That's 3 vendors to manage. What does it cost to give everything to just two?",
            "Chart landed price vs last year for the 5-ply range, by vendor.",
            "Draft a one-page award recommendation for my VP."]
    cols = st.columns(3)
    for i, s in enumerate(sugg):
        if cols[i % 3].button(s, key=f"sg{i}", width="stretch"):
            ss.ask_pending = s

    def render_artifacts(arts, k):
        for j, a in enumerate(arts):
            if a["kind"] == "table":
                st.markdown(f"**{a['title']}**")
                st.dataframe(a["df"], hide_index=True, width="stretch")
            elif a["kind"] == "chart":
                st.plotly_chart(a["fig"], width="stretch", key=f"ch_{k}_{j}")
            elif a["kind"] == "file":
                st.download_button(f"⬇️ {a['title']}", a["data"], file_name=a["title"], mime=a["mime"], key=f"f_{k}_{j}")
            elif a["kind"] == "memo":
                with st.container(border=True):
                    st.markdown(a["markdown"])
                c1, c2 = st.columns(2)
                c1.download_button("⬇️ Memo (.docx)", md_to_docx(a["title"], a["markdown"]), file_name=f"{a['title'][:60]}.docx", key=f"md_{k}_{j}")
                c2.download_button("⬇️ Memo (.md)", a["markdown"], file_name=f"{a['title'][:60]}.md", key=f"mm_{k}_{j}")

    for i, m in enumerate(ss.ask_display):
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            render_artifacts(m.get("artifacts", []), i)
            if m.get("trace"):
                with st.expander(f"How I got this · {len(m['trace'])} steps"):
                    for t in m["trace"]:
                        st.markdown(f"**{t['tool']}** {('— ' + t['input'].get('purpose', '')) if isinstance(t['input'], dict) else ''}")
                        if isinstance(t["input"], dict) and t["input"].get("sql"):
                            st.code(t["input"]["sql"], language="sql")
                        elif t["tool"] == "get_evidence":
                            st.code(json.dumps(t["input"]), language="json")

    qtext = st.chat_input("Ask anything about the quotes…") or ss.pop("ask_pending", None)
    if qtext:
        if not llm:
            st.error("Add an API key first.")
        else:
            ss.ask_display.append(dict(role="user", text=qtext))
            an = Analyst(llm, T, ss.rfx)
            n_before = len(ss.ask_history)
            with st.chat_message("user"):
                st.markdown(qtext)
            with st.chat_message("assistant"):
                with st.status("Thinking…", expanded=False) as s_:
                    try:
                        text, trace, arts = an.ask(ss.ask_history, qtext, on_step=lambda name, inp: s_.write(f"{name}: {inp.get('purpose') or inp.get('title') or ''}"))
                    except Exception as e:
                        del ss.ask_history[n_before:]
                        text, trace, arts = f"⚠️ {e}", [], []
            ss.ask_display.append(dict(role="assistant", text=text, trace=trace, artifacts=arts))
            st.rerun()
    if ss.ask_display and st.button("Clear conversation"):
        ss.ask_display, ss.ask_history = [], []
        st.rerun()

# =========================================================================== 6. Award
else:
    st.markdown("### Award")
    if T["quotes"].empty:
        st.info("Read the responses first (2 · Inbox).")
        st.stop()
    A = ss.assumptions
    st.caption("The split is computed in code: every combination of vendors is tried, so this is the true lowest cost under your rules. "
               "You decide the rules; low-confidence prices never win until confirmed.")
    c1, c2, c3, c4 = st.columns(4)
    cap = c1.selectbox("Max vendors", [1, 2, 3, 4, 5, None], index=2, format_func=lambda x: "No cap" if x is None else str(x),
                       help="Default 3: 1 has no backup; with 2 the survivor must absorb 100% if one fails; 4+ falls below discount and MOQ thresholds and adds overhead.")
    elig = c2.selectbox("Who can win", list(award.ELIGIBILITY), index=1, format_func=award.ELIGIBILITY_LABEL.get)
    basis = c3.selectbox("Price basis", ["effective", "landed"], index=0 if A.value_payment_terms else 1,
                         format_func={"effective": "Landed + payment terms", "landed": "Landed (price + freight)"}.get)
    cond = c4.toggle("Apply conditional discounts if earned", A.apply_conditional_discounts,
                     help="A discount like 5% above Rs 25 lakh is applied only if this award actually crosses the threshold.")
    r = award.optimize(T, cap, elig, basis, cond)
    if not r["ok"]:
        st.warning(r["message"])
        st.stop()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Award total", inr(r["total_inr"]), f"{r['vs_last_year_pct']:+.1f}% vs last year", delta_color="inverse")
    k2.metric("Last year, same lines", inr(r["last_year_same_lines_inr"]))
    k3.metric("Lines covered", f"{r['lines_covered']}/{r['lines_total']}")
    k4.metric("Vendors used", len(r["split"]))
    for w in r["warnings"]:
        st.warning(w)
    left, right = st.columns([5, 6], gap="large")
    with left:
        st.markdown("**Split by vendor**")
        sp = r["split"].assign(annual=r["split"]["annual_inr"].map(inr))
        st.dataframe(sp[["vendor_name", "quality_status", "lines", "annual", "conditional_discount_applied"]], hide_index=True, width="stretch")
    with right:
        st.markdown("**Line by line**")
        ln = r["lines"].assign(unit=r["lines"]["unit_price_inr"].map(lambda x: f"{x:,.2f}"), annual=r["lines"]["annual_inr"].map(inr))
        st.dataframe(ln[["line_id", "vendor_name", "unit", "annual_qty", "annual", "confidence"]], hide_index=True, width="stretch", height=300)
    st.markdown("**What the rules cost**: same price basis, different caps and eligibility")
    sc = award.scenarios(T, basis, cond)
    sc = sc.assign(total=sc["total_inr"].map(inr), vs_last_year=sc["vs_last_year_pct"].map(lambda x: f"{x:+.1f}%" if pd.notna(x) else "—"))
    st.dataframe(sc[["eligibility", "max_vendors", "vendors_used", "lines_covered", "total", "vs_last_year"]], hide_index=True, width="stretch",
                 column_config={"vendors_used": st.column_config.TextColumn(width="large")})
    unresolved = list(r["split"][r["split"]["quality_status"] == "UNRESOLVED"]["vendor_id"])
    if unresolved:
        render_clarifications(T, llm, only=unresolved)

    def _to_memo():
        ss.ask_pending = (f"Draft a one-page award recommendation for my VP. Use optimize_award with max_vendors={cap if cap else 'null'}, "
                          f"eligibility={elig}, price_basis={basis}, apply_conditional_discounts={str(cond).lower()}, "
                          "and compare it with cap 2 and no cap. Name excluded vendors, assumptions and open risks.")
        ss.nav = "5 · Ask"
    st.button("📝 Draft the award memo for this split", type="primary", on_click=_to_memo, disabled=not llm)
