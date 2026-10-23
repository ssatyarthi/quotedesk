"""
Generates the demo dataset: one RFx, last year's contract, and five vendor
responses in five deliberately messy formats.

Scenario
--------
Sunrise Foods Pvt Ltd (fictional biscuits & snacks maker, plant at Manesar,
Haryana) is re-sourcing its annual corrugated packaging: 30 line items,
~Rs 4 crore a year. Category buyer: Priya Nair.

Five vendors reply, none of them in our template:
  V1 Shree Ganesh Corrugators  - Excel in their own layout; pads/partitions
                                  priced "per 100 nos"; one line offered at an
                                  alternate (lower) spec; BCT test report attached.
  V2 Rajhans Packaging          - PDF on letterhead; 27 of 30 lines (two missing
                                  silently, one "regret"); conditional 5% discount
                                  buried in a footnote; ISO certificate attached
                                  that has EXPIRED.
  V3 GlobePack Solutions        - Word doc, all commercials in prose, priced in
                                  USD; layer pads priced "per box" (= 100 pads).
  V4 Vinayak Packers            - phone photo of a printed rate card, taken at an
                                  angle, one price smudged, sizes don't exactly
                                  match ours; quality questionnaire answered in
                                  the email body (fails capacity + ISO).
  V5 Bharat Corrugated          - incumbent; one-line email: "Rs 42/kg for 5-ply,
                                  38 for 3-ply, rest same as last year, freight
                                  extra". Questionnaire not answered.

Ground truth for every planted value is written to data/_answer_key/ so the
extraction can be *evaluated* (tests/eval_extraction.py). The app itself never
reads the answer key.

Run:  python data/generate_dataset.py
"""
import json
import math
import random
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESP = ROOT / "responses"
KEY = ROOT / "_answer_key"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

random.seed(7)

# ---------------------------------------------------------------------------
# 1. The RFx: 30 lines of corrugated packaging
# ---------------------------------------------------------------------------
BOARD = {  # ply -> (GSM construction, effective grammage g/m2 incl. 1.5 flute take-up, BF)
    3: ("150/120/150", 150 + 120 * 1.5 + 150, 18),
    5: ("180/120/150/120/180", 180 + 150 + 180 + 2 * 120 * 1.5, 20),
    7: ("200/150/150/150/150/150/200", 200 + 150 + 150 + 200 + 3 * 150 * 1.5, 22),
}
PRINT_ADDER = {"Plain": 0.0, "1 colour flexo": 0.6, "2 colour flexo": 1.1, "4 colour flexo": 2.4}
RATE_PER_KG = {3: 37.0, 5: 40.5, 7: 44.0}  # "true" market conversion rate, Rs/kg


def rsc_weight_g(L, W, H, ply):
    """Regular slotted carton blank area x board grammage."""
    blank_len = 2 * (L + W) + 35  # + glue flap
    blank_wid = W + H  # two half-flaps = W
    return blank_len * blank_wid / 1e6 * BOARD[ply][1]


raw_lines = [
    # code, type, ply, L, W, H, print, base annual qty
    ("CB-101", "RSC carton", 3, 380, 280, 220, "1 colour flexo", 420000),
    ("CB-102", "RSC carton", 3, 300, 200, 150, "1 colour flexo", 600000),
    ("CB-103", "RSC carton", 3, 450, 300, 250, "2 colour flexo", 250000),
    ("CB-104", "RSC carton", 3, 250, 180, 120, "1 colour flexo", 500000),
    ("CB-105", "RSC carton", 3, 400, 300, 300, "1 colour flexo", 200000),
    ("CB-106", "RSC carton", 3, 350, 250, 200, "2 colour flexo", 300000),
    ("CB-107", "RSC carton", 3, 500, 350, 250, "1 colour flexo", 150000),
    ("CB-108", "RSC carton", 3, 320, 240, 180, "1 colour flexo", 350000),
    ("CB-109", "RSC carton", 3, 280, 200, 200, "Plain", 400000),
    ("CB-110", "RSC carton", 3, 420, 320, 260, "2 colour flexo", 180000),
    ("CB-201", "RSC carton", 5, 500, 400, 350, "2 colour flexo", 120000),
    ("CB-202", "RSC carton", 5, 600, 400, 400, "1 colour flexo", 90000),
    ("CB-203", "RSC carton", 5, 450, 350, 300, "1 colour flexo", 140000),
    ("CB-204", "RSC carton", 5, 550, 450, 350, "2 colour flexo", 80000),
    ("CB-205", "RSC carton", 5, 400, 400, 400, "Plain", 100000),
    ("CB-206", "RSC carton", 5, 600, 500, 300, "1 colour flexo", 60000),
    ("CB-207", "RSC carton", 5, 480, 360, 320, "2 colour flexo", 110000),
    ("CB-208", "RSC carton", 5, 520, 420, 380, "1 colour flexo", 70000),
    ("CB-209", "RSC carton", 5, 450, 450, 450, "Plain", 50000),
    ("CB-210", "RSC carton", 5, 650, 450, 400, "1 colour flexo", 45000),
    ("CB-301", "Heavy-duty RSC", 7, 800, 600, 500, "Plain", 15000),
    ("CB-302", "Heavy-duty RSC", 7, 1000, 800, 600, "Plain", 8000),
    ("CB-303", "Bulk bin RSC", 7, 1200, 1000, 800, "Plain", 4000),
]

lines = []
for code, typ, ply, L, W, H, prt, qty in raw_lines:
    wt = rsc_weight_g(L, W, H, ply)
    base = wt / 1000 * RATE_PER_KG[ply] + PRINT_ADDER[prt]
    lines.append(dict(
        line_id=code, item_type=typ, ply=ply, length_mm=L, width_mm=W, height_mm=H,
        board_gsm=BOARD[ply][0], burst_factor_min=BOARD[ply][2], print=prt,
        uom="pcs", annual_qty=qty, est_weight_g=round(wt), _base=base,
        description=f"{ply}-ply {typ}, {L}x{W}x{H} mm (ID), {BOARD[ply][0]} GSM, BF {BOARD[ply][2]}, {prt.lower()}",
    ))

# non-RSC items: weight given directly
extra = [
    ("DT-401", "Die-cut display tray", 3, (400, 300, 80), "4 colour flexo", 300000, 180, 1.5),
    ("DT-402", "Die-cut display tray", 3, (300, 200, 60), "1 colour flexo", 400000, 110, 1.2),
    ("PT-501", "Partition set, 12-cell (fits CB-101)", 3, None, "Plain", 120000, 140, 1.2),
    ("PT-502", "Partition set, 24-cell (fits CB-201)", 3, None, "Plain", 50000, 260, 1.6),
    ("LP-601", "Layer pad 1200x1000 mm", 3, None, "Plain", 80000, 576, 0.5),
    ("LP-602", "Layer pad 1000x800 mm", 3, None, "Plain", 100000, 384, 0.5),
]
for code, typ, ply, dims, prt, qty, wt, conv in extra:
    base = wt / 1000 * RATE_PER_KG[ply] + PRINT_ADDER[prt] + conv
    L, W, H = dims if dims else (None, None, None)
    desc = f"{typ}, {ply}-ply" + (f", {L}x{W}x{H} mm" if dims else "") + f", {BOARD[ply][0]} GSM, {prt.lower()}"
    if code.startswith("PT"):
        desc += ", price per complete set"
    lines.append(dict(
        line_id=code, item_type=typ, ply=ply, length_mm=L, width_mm=W, height_mm=H,
        board_gsm=BOARD[ply][0], burst_factor_min=BOARD[ply][2], print=prt,
        uom="set" if code.startswith("PT") else "pcs", annual_qty=qty, est_weight_g=wt, _base=base,
        description=desc,
    ))

lines.append(dict(
    line_id="CR-701", item_type="Corrugated roll, 2-ply", ply=2, length_mm=None, width_mm=1200,
    height_mm=None, board_gsm="150/120", burst_factor_min=16, print="Plain", uom="kg",
    annual_qty=30000, est_weight_g=1000, _base=46.0,
    description="2-ply corrugated roll, 1200 mm width x 50 m, 150/120 GSM, priced per kg",
))

# Calibrate quantities so annual spend at market price is ~Rs 4.0 crore
spend = sum(l["_base"] * l["annual_qty"] for l in lines)
scale = 4.0e7 / spend
for l in lines:
    if l["uom"] != "kg":
        q = l["annual_qty"] * scale
        l["annual_qty"] = int(round(q / 5000) * 5000) if q > 20000 else int(round(q / 500) * 500)

LINE = {l["line_id"]: l for l in lines}
assert len(lines) == 30

QUESTIONNAIRE = [
    dict(q_id="Q1", question="Is your manufacturing plant ISO 9001:2015 certified? Attach a valid certificate.",
         kind="yes_no", mandatory=True, pass_rule="Yes, with a certificate valid on the award date"),
    dict(q_id="Q2", question="Do you test BCT, bursting strength and ECT in-house and issue a COA with every lot?",
         kind="yes_no", mandatory=True, pass_rule="Yes (in-house)"),
    dict(q_id="Q3", question="Are all printing inks and adhesives food-safe / mineral-oil-free, with a declaration?",
         kind="yes_no", mandatory=True, pass_rule="Yes"),
    dict(q_id="Q4", question="Monthly corrugation capacity (metric tonnes)?",
         kind="number", unit="MT/month", mandatory=True, pass_rule=">= 120"),
    dict(q_id="Q5", question="Standard lead time from PO to delivery at Manesar (days)?",
         kind="number", unit="days", mandatory=False, pass_rule="<= 10 preferred"),
    dict(q_id="Q6", question="Do you accept 60-day payment terms from date of GRN?",
         kind="yes_no", mandatory=False, pass_rule="Yes preferred"),
    dict(q_id="Q7", question="Recycled fibre content of liner/fluting (%)?",
         kind="number", unit="%", mandatory=False, pass_rule="info"),
    dict(q_id="Q8", question="Location of the supplying plant?",
         kind="text", mandatory=False, pass_rule="info"),
]

RFX = dict(
    rfx_id="SF-RFQ-2026-118",
    title="Annual rate contract: corrugated packaging, Manesar plant (FY 2026-27 H2 + FY 2027-28 H1)",
    buyer=dict(company="Sunrise Foods Pvt Ltd", name="Priya Nair", role="Category Buyer, Packaging",
               email="priya.nair@sunrisefoods.example"),
    issued_on="2026-10-12",
    due_on="2026-10-21",
    currency="INR",
    terms=dict(
        price_basis="Firm price per unit, delivered (FOR) Sunrise Foods, Plot 42, Sector 8, IMT Manesar, Haryana. Ex-GST.",
        validity="Prices firm for 12 months from award",
        payment="60 days from GRN",
        delivery="Weekly call-offs, max 10-day lead time",
        quality="Lot-wise COA; BCT/burst/ECT per Sunrise spec SF-PK-07",
        award="Line-wise award possible; Sunrise may split volumes between vendors",
    ),
    lines=[{k: v for k, v in l.items() if not k.startswith("_")} for l in lines],
    questionnaire=QUESTIONNAIRE,
    invited_vendors=["V1", "V2", "V3", "V4", "V5"],
)

# ---------------------------------------------------------------------------
# 2. Last year's contract with the incumbent (Bharat Corrugated)
# ---------------------------------------------------------------------------
last_year = []
for l in lines:
    if l["item_type"] == "RSC carton":
        rate = {3: 36.5, 5: 40.0}[l["ply"]]
        last_year.append(dict(line_id=l["line_id"], vendor="Bharat Corrugated", basis="per kg (print incl.)",
                              rate=rate, unit="INR/kg", price_per_uom=round(rate * l["est_weight_g"] / 1000, 2)))
    elif l["uom"] == "kg":
        last_year.append(dict(line_id=l["line_id"], vendor="Bharat Corrugated", basis="per kg",
                              rate=44.5, unit="INR/kg", price_per_uom=44.5))
    else:
        p = round(l["_base"] * 0.95, 2)
        last_year.append(dict(line_id=l["line_id"], vendor="Bharat Corrugated", basis=f"per {l['uom']}",
                              rate=p, unit=f"INR/{l['uom']}", price_per_uom=p))
LAST = {r["line_id"]: r for r in last_year}

# ---------------------------------------------------------------------------
# 3. Vendor prices (ground truth, Rs per RFx UoM, before any adjustment)
# ---------------------------------------------------------------------------
def grp(l):
    if l["line_id"].startswith("CB-1"): return "3ply"
    if l["line_id"].startswith("CB-2"): return "5ply"
    if l["line_id"].startswith("CB-3"): return "7ply"
    if l["line_id"].startswith("DT"): return "tray"
    if l["line_id"].startswith("PT"): return "part"
    if l["line_id"].startswith("LP"): return "pad"
    return "roll"

FACTORS = {
    "V1": dict(**{"3ply": 1.03, "5ply": 1.00, "7ply": 0.98, "tray": 0.96, "part": 0.95, "pad": 0.97, "roll": 1.02}),
    "V2": dict(**{"3ply": 0.965, "5ply": 1.02, "7ply": 1.00, "tray": 1.01, "part": 1.03, "pad": 1.00, "roll": None}),
    "V3": dict(**{"3ply": 1.04, "5ply": 0.95, "7ply": 0.97, "tray": 1.05, "part": 1.00, "pad": 0.99, "roll": 0.98}),
    "V4": dict(**{"3ply": 0.90, "5ply": 0.91, "7ply": None, "tray": None, "part": None, "pad": 0.92, "roll": 0.93}),
}
USD_INR = 88.0  # the rate GlobePack's prices were built at; app uses its own configurable rate

truth = {v: {} for v in ["V1", "V2", "V3", "V4", "V5"]}
for l in lines:
    g = grp(l)
    for v, f in FACTORS.items():
        if f[g] is None:
            continue
        p = l["_base"] * f[g] * random.uniform(0.97, 1.03)
        truth[v][l["line_id"]] = round(p, 2)

# V2 silently skips two 7-ply lines and regrets the roll
for lid in ["CB-302", "CB-303"]:
    truth["V2"].pop(lid, None)

# V5: per-kg for 3/5-ply RSC, last year's price for everything else
for l in lines:
    if l["item_type"] == "RSC carton":
        rate = 38.0 if l["ply"] == 3 else 42.0
        truth["V5"][l["line_id"]] = round(rate * l["est_weight_g"] / 1000, 2)
    else:
        truth["V5"][l["line_id"]] = LAST[l["line_id"]]["price_per_uom"]

# V1 offers CB-107 at alternate spec (BF 16) - cheaper, but non-compliant
V1_ALT = {"CB-107": round(truth["V1"]["CB-107"] * 0.93, 2)}
# V4 smudged price
V4_SMUDGED = "CB-206"


def money(x):
    return f"{x:,.2f}"


# ---------------------------------------------------------------------------
# helpers to write files
# ---------------------------------------------------------------------------
def write_email(path, frm, to, dt, subject, body, attachments=()):
    att = "".join(f"\nAttachment: {a}" for a in attachments)
    path.write_text(
        f"From: {frm}\nTo: {to}\nDate: {dt}\nSubject: {subject}{att}\n\n{body.strip()}\n",
        encoding="utf-8",
    )


def fmt_dims(l, sep="x"):
    return f"{l['length_mm']}{sep}{l['width_mm']}{sep}{l['height_mm']}"


# ---------------------------------------------------------------------------
# V1 - Excel in their own layout
# ---------------------------------------------------------------------------
def make_v1(d: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"
    ws["A1"] = "SHREE GANESH CORRUGATORS PVT. LTD."
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Plot 118-119, RIICO Industrial Area, Bawal Road, Neemrana (Raj.)  |  GSTIN 08AAJCS7731L1ZK"
    ws["A4"] = "Quotation No: SGC/Q/26-27/2611        Date: 16.10.2026"
    ws["A5"] = "To: Sunrise Foods Pvt Ltd, Manesar   Kind Attn: Ms. Priya Nair   Ref: your enquiry SF-RFQ-2026-118"
    ws.merge_cells("A1:H1")
    hdr = ["S.No", "Description of Goods", "Your Ref", "Size (mm)", "Board", "Printing", "Rate", "Remarks"]
    row = 7
    fill = PatternFill("solid", fgColor="FFF2CC")
    for i, h in enumerate(hdr, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(bold=True)
        c.fill = fill

    def section(title):
        nonlocal row
        row += 1
        ws.cell(row=row, column=1, value=title).font = Font(bold=True, italic=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)

    sn = 0

    def item(l, rate, remark=""):
        nonlocal row, sn
        row += 1
        sn += 1
        desc = {"RSC carton": "Corrugated Box", "Heavy-duty RSC": "Heavy Duty Box", "Bulk bin RSC": "Bulk Bin"}.get(
            l["item_type"], l["item_type"].split(",")[0])
        size = fmt_dims(l, "x") if l["length_mm"] else ("1200 W" if l["uom"] == "kg" else "")
        if l["line_id"].startswith("LP"):
            size = l["item_type"].replace("Layer pad ", "")
        vals = [sn, f"{desc} {l['ply']} Ply", l["line_id"], size, l["board_gsm"],
                {"Plain": "Plain", "1 colour flexo": "1 Col", "2 colour flexo": "2 Col", "4 colour flexo": "4 Col"}[l["print"]],
                rate, remark]
        for i, v in enumerate(vals, 1):
            ws.cell(row=row, column=i, value=v)

    section("A) CORRUGATED BOXES  -  Rate in Rs. per Box")
    for l in lines:
        if l["line_id"].startswith("CB"):
            item(l, truth["V1"][l["line_id"]])
            if l["line_id"] in V1_ALT:
                row += 1
                ws.cell(row=row, column=2, value="   ALTERNATE OFFER for above (Ref CB-107) - BF 16 board, same GSM")
                ws.cell(row=row, column=3, value="CB-107 (Alt)")
                ws.cell(row=row, column=7, value=V1_ALT["CB-107"])
                ws.cell(row=row, column=8, value="Recommended - saves cost")
    section("B) DIE CUT TRAYS  -  Rate in Rs. per Pc")
    for l in lines:
        if l["line_id"].startswith("DT"):
            item(l, truth["V1"][l["line_id"]])
    section("C) PARTITIONS & PADS  -  Rate in Rs. per 100 Nos.")
    for l in lines:
        if l["line_id"].startswith(("PT", "LP")):
            item(l, round(truth["V1"][l["line_id"]] * 100, 0), "per 100 nos" if l["line_id"] == "PT-501" else "")
    section("D) ROLL  -  Rate in Rs. per Kg")
    item(LINE["CR-701"], truth["V1"]["CR-701"])
    row += 2
    ws.cell(row=row, column=2, value="Note: GST @ applicable rate extra. Freight inclusive up to Manesar.")
    for col, w in zip("ABCDEFGH", [6, 34, 13, 16, 26, 9, 10, 26]):
        ws.column_dimensions[col].width = w

    t = wb.create_sheet("Terms")
    terms = [
        ("Price basis", "FOR Manesar, freight included"), ("Taxes", "GST extra as applicable"),
        ("Payment", "60 days from GRN - accepted"), ("Validity", "Rates valid 12 months; paper price variation clause +/-5% on kraft index"),
        ("Delivery", "7 days from PO"), ("MOQ", "1,000 pcs per size per call-off"),
    ]
    for i, (k, v) in enumerate(terms, 1):
        t.cell(row=i, column=1, value=k).font = Font(bold=True)
        t.cell(row=i, column=2, value=v)
    t.column_dimensions["A"].width = 16
    t.column_dimensions["B"].width = 70

    c = wb.create_sheet("Compliance")
    ans = [
        ("ISO 9001:2015", "YES - Cert No. TQ-91877, TUV, valid till 14-Feb-2028"),
        ("In-house testing BCT / Burst / ECT + COA per lot", "YES. Sample report attached."),
        ("Food safe inks / adhesives", "YES - declaration will be given with first supply"),
        ("Capacity", "450 MT per month"),
        ("Lead time", "7 days"),
        ("60 days payment", "Accepted"),
        ("Recycled content", "approx 70%"),
        ("Plant", "Neemrana, Rajasthan (110 km from Manesar)"),
    ]
    for i, (k, v) in enumerate(ans, 1):
        c.cell(row=i, column=1, value=k)
        c.cell(row=i, column=2, value=v)
    c.column_dimensions["A"].width = 48
    c.column_dimensions["B"].width = 60
    wb.save(d / "SGC_Quotation_2611.xlsx")

    # BCT test report PDF
    pdf_simple(d / "SGC_Lab_Test_Report_Sept26.pdf", "SHREE GANESH CORRUGATORS - QC LABORATORY",
               ["Test Report No. SGC/QC/0926/114     Date: 24-09-2026",
                "Sample: 3 Ply RSC 380x280x220, 150/120/150 GSM, BF 18",
                "",
                "Parameter                 Spec      Result",
                "Bursting strength (kg/cm2) >= 5.0    5.6",
                "BCT (kgf)                 >= 180     212",
                "ECT (kN/m)                >= 4.2     4.7",
                "Moisture (%)              <= 9       7.8",
                "",
                "Instrument calibration valid till 31-Mar-2027. Tested by: R. Saini (QC In-charge)"])

    write_email(d / "email.txt", "sales@shreeganeshcorr.example (Rakesh Agarwal)", RFX["buyer"]["email"],
                "Fri, 16 Oct 2026 18:42 +0530", "RE: SF-RFQ-2026-118 Quotation for corrugated boxes",
                """Dear Priya ji,

Please find our best quotation attached in excel. We have also offered an alternate on one box
which will save you cost. Compliance details are in the 3rd sheet. Lab report attached.

Regards,
Rakesh Agarwal
Shree Ganesh Corrugators""",
                ["SGC_Quotation_2611.xlsx", "SGC_Lab_Test_Report_Sept26.pdf"])


# ---------------------------------------------------------------------------
# PDF helpers (reportlab)
# ---------------------------------------------------------------------------
def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        pdfmetrics.registerFont(TTFont("DV", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DVB", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DVS", str(FONT_DIR / "DejaVuSerif.ttf")))
    except Exception:
        pass


def pdf_simple(path, title, body_lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    _fonts()
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("DVB", 13)
    c.drawString(50, h - 60, title)
    c.setFont("DV", 10)
    y = h - 95
    for ln in body_lines:
        c.drawString(50, y, ln)
        y -= 16
    c.save()


# ---------------------------------------------------------------------------
# V2 - PDF on letterhead, discount in a footnote, 27/30 lines, expired ISO
# ---------------------------------------------------------------------------
def make_v2(d: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    _fonts()
    path = d / "Rajhans_Quotation_RPI-1187.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4

    def letterhead():
        c.setFillColor(colors.HexColor("#7A1F1F"))
        c.rect(0, H - 70, W, 70, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("DVB", 18)
        c.drawString(40, H - 38, "RAJHANS PACKAGING INDUSTRIES")
        c.setFont("DV", 8.5)
        c.drawString(40, H - 54, "Manufacturers of Corrugated Boxes, Sheets & Rolls  •  Since 1994")
        c.drawString(40, H - 64, "Plot 31, Sector 24, Faridabad, Haryana 121005  •  +91 129 400 7712  •  GSTIN 06AAXFR4821K1ZQ")
        c.setFillColor(colors.black)

    letterhead()
    c.setFont("DV", 9.5)
    y = H - 95
    for t in ["Ref: RPI/SF/1187/26-27", "Date: 18 October 2026", "",
              "To, The Purchase Department, Sunrise Foods Pvt Ltd, IMT Manesar",
              "Sub: Our offer against your enquiry no. SF-RFQ-2026-118", "",
              "Dear Madam, with reference to the above we are pleased to quote our lowest rates as under:"]:
        c.drawString(40, y, t)
        y -= 13
    y -= 4
    cols = [40, 75, 135, 330, 430, 520]
    c.setFont("DVB", 8.5)
    for x, t in zip(cols, ["#", "Item code", "Specification", "Size (ID, mm)", "Rate Rs./unit*"]):
        c.drawString(x, y, t)
    y -= 4
    c.line(40, y, 560, y)
    y -= 11
    c.setFont("DV", 8.3)
    n = 0
    for l in lines:
        lid = l["line_id"]
        n += 1
        size = fmt_dims(l, " x ") if l["length_mm"] else (l["item_type"].split(" ")[-2] if l["line_id"].startswith("LP") else "-")
        spec = f"{l['ply']} ply, {l['board_gsm']}" if l["ply"] != 7 else f"7 ply HD, BF {l['burst_factor_min']}"
        if lid in truth["V2"]:
            rate = money(truth["V2"][lid])
        elif lid == "CR-701":
            rate = "Regret"
        else:
            n -= 1
            continue  # silently omitted
        c.drawString(cols[0], y, str(n))
        c.drawString(cols[1], y, lid)
        c.drawString(cols[2], y, spec[:34])
        c.drawString(cols[3], y, size)
        c.drawRightString(cols[4] + 70, y, rate)
        y -= 12.2
        if y < 120:
            c.showPage()
            letterhead()
            y = H - 100
            c.setFont("DV", 8.3)
    y -= 6
    c.line(40, y, 560, y)
    y -= 14
    c.setFont("DV", 8.6)
    for t in ["Terms: Prices are ex-GST, delivered Manesar. Payment: 45 days. Delivery: 5 working days.",
              "Validity of offer: 30 days. Partitions priced per set. Our ISO 9001 certificate is enclosed.",
              ""]:
        c.drawString(40, y, t)
        y -= 12
    # compliance answers block
    c.setFont("DVB", 9)
    c.drawString(40, y, "Compliance to your questionnaire")
    y -= 13
    c.setFont("DV", 8.6)
    for t in ["1. ISO 9001:2015 - Yes, certified (copy enclosed)", "2. In-house lab (BCT, burst, ECT), COA with each lot - Yes",
              "3. Food grade inks / adhesives - Yes, declaration on letterhead with first lot",
              "4. Capacity - 300 MT/month   5. Lead time - 5 days   6. Payment - 45 days (60 days not possible)",
              "7. Recycled content - ~80%   8. Plant - Faridabad, Haryana"]:
        c.drawString(40, y, t)
        y -= 12
    y -= 14
    c.drawString(40, y, "Thanking you and assuring our best services,")
    c.drawString(40, y - 13, "For Rajhans Packaging Industries   —   Authorised Signatory (M. K. Rajhans)")
    # the footnote, tiny, at the very bottom
    c.setFont("DV", 6.2)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(40, 34, "* Rates marked above are list rates. A special trade discount of 5% will be extended on all items if the confirmed annual")
    c.drawString(40, 26, "  order value placed on RPI exceeds Rs. 25,00,000 (Rupees twenty-five lakh). Discount to be passed through credit note quarterly.")
    c.save()

    pdf_simple(d / "RPI_ISO9001_Certificate.pdf", "CERTIFICATE OF REGISTRATION",
               ["This is to certify that the Quality Management System of",
                "RAJHANS PACKAGING INDUSTRIES, Plot 31, Sector 24, Faridabad, Haryana",
                "has been assessed and found to conform to ISO 9001:2015",
                "",
                "Scope: Manufacture and supply of corrugated boxes, sheets and rolls",
                "Certificate No.: IQC/QMS/22/4410",
                "Date of initial registration: 01 May 2023",
                "Valid until: 30 April 2026",
                "Issued by: Indo Quality Certifications Pvt Ltd (accreditation ref. on file)",
                "",
                "Validity of this certificate is subject to annual surveillance audits."])

    write_email(d / "email.txt", "mk@rajhanspack.example (M. K. Rajhans)", RFX["buyer"]["email"],
                "Sun, 18 Oct 2026 11:05 +0530", "Offer - SF-RFQ-2026-118",
                """Respected Madam,

Kindly find attached our offer and ISO certificate. Hope to receive your valued order.

M K Rajhans
Rajhans Packaging Industries, Faridabad""",
                ["Rajhans_Quotation_RPI-1187.pdf", "RPI_ISO9001_Certificate.pdf"])


# ---------------------------------------------------------------------------
# V3 - Word doc, commercials in prose, USD, pads "per box" of 100
# ---------------------------------------------------------------------------
def make_v3(d: Path):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    doc.add_heading("GlobePack Solutions India Pvt Ltd", level=1)
    doc.add_paragraph("A GlobePack Group company  |  SIPCOT Industrial Park, Sriperumbudur, Tamil Nadu 602105")
    doc.add_paragraph("Commercial Proposal GP/IN/SF/2026/044   •   19 October 2026")
    doc.add_paragraph("Prepared for: Ms. Priya Nair, Sunrise Foods Pvt Ltd   •   Against: SF-RFQ-2026-118")

    doc.add_heading("1. Our understanding", level=2)
    doc.add_paragraph(
        "Sunrise Foods is looking to consolidate its corrugated requirement for the Manesar plant under a 12-month "
        "rate contract. GlobePack proposes to supply from its Sriperumbudur mega-plant with a dedicated buffer stock "
        "at our Bhiwadi warehouse, which keeps your effective call-off lead time short after the initial build-up.")

    doc.add_heading("2. Commercial offer", level=2)
    doc.add_paragraph(
        "As per Group policy all prices are quoted in US Dollars and will be invoiced in INR at the RBI reference "
        "rate prevailing on the date of invoice. Prices are delivered Manesar (freight and insurance included) and "
        "exclusive of GST. Unless stated otherwise, prices are per piece.")

    v = truth["V3"]
    usd = lambda lid: f"USD {v[lid] / USD_INR:.4f}".rstrip("0").rstrip(".")

    three = [l for l in lines if l["line_id"].startswith("CB-1")]
    five = [l for l in lines if l["line_id"].startswith("CB-2")]
    seven = [l for l in lines if l["line_id"].startswith("CB-3")]
    p = ("For the 3-ply regular slotted cartons (150/120/150 GSM, BF 18) our prices are: "
         + "; ".join(f"{fmt_dims(l)} mm {('plain' if l['print']=='Plain' else l['print'].split(' flexo')[0]+' print')} at {usd(l['line_id'])}"
                     for l in three) + ".")
    doc.add_paragraph(p)
    p = ("Moving to the 5-ply range, where our corrugator gives us a genuine edge, we are pleased to offer "
         + ", ".join(f"{fmt_dims(l)} at {usd(l['line_id'])}" for l in five)
         + ", all in 180/120/150/120/180 GSM, BF 20, printing as per your artwork.")
    doc.add_paragraph(p)
    p = ("The heavy-duty 7-ply range would be " + ", ".join(f"{usd(l['line_id'])} for {fmt_dims(l)}" for l in seven)
         + " respectively.")
    doc.add_paragraph(p)
    p = (f"Die-cut display trays: the 400x300x80 four-colour tray at {usd('DT-401')} and the 300x200x60 single-colour "
         f"tray at {usd('DT-402')}. Partition sets are offered at {usd('PT-501')} per set (12-cell) and {usd('PT-502')} "
         f"per set (24-cell).")
    doc.add_paragraph(p)
    p = (f"Layer pads are packed and priced by the box: USD {v['LP-601'] * 100 / USD_INR:.2f} per box for 1200x1000 mm "
         f"and USD {v['LP-602'] * 100 / USD_INR:.2f} per box for 1000x800 mm. "
         f"The 2-ply corrugated roll (1200 mm) is USD {v['CR-701'] / USD_INR:.3f} per kg.")
    doc.add_paragraph(p)

    doc.add_heading("3. Terms", level=2)
    doc.add_paragraph(
        "Payment: 60 days from GRN accepted. Lead time: 21 days from PO for the first two call-offs, thereafter 7 days "
        "from the Bhiwadi buffer. Minimum order: one full truckload (approx. 4 MT) per call-off. Validity: 6 months, "
        "after which prices are indexed to the Pulp & Paper Index (quarterly).")
    doc.add_heading("4. Quality & compliance", level=2)
    doc.add_paragraph(
        "Our Sriperumbudur plant is ISO 9001:2015, ISO 14001 and FSC-certified (certificate enclosed). All lots ship "
        "with a COA from our in-house NABL-accredited lab covering BCT, bursting strength and ECT. We use only "
        "mineral-oil-free, food-contact-compliant water-based inks and starch adhesives; a declaration is enclosed. "
        "Installed capacity is 1,800 MT per month across two corrugators. Recycled fibre content is about 60%.")
    doc.add_paragraph("We look forward to partnering with Sunrise Foods.\n\nArjun Menon\nKey Account Manager, North")
    doc.save(d / "GlobePack_Proposal_SunriseFoods.docx")

    pdf_simple(d / "GlobePack_ISO_Certificate.pdf", "CERTIFICATE - ISO 9001:2015",
               ["Organisation: GlobePack Solutions India Pvt Ltd",
                "Site: SIPCOT Industrial Park, Sriperumbudur, Tamil Nadu",
                "Standard: ISO 9001:2015 Quality Management Systems",
                "Certificate No.: BV-IND-QMS-77120",
                "Issue date: 10 March 2025", "Expiry date: 09 March 2028",
                "Certification body: (fictional) Bureau Veritas-style registrar for demo purposes"])

    write_email(d / "email.txt", "arjun.menon@globepack.example (Arjun Menon)", RFX["buyer"]["email"],
                "Mon, 19 Oct 2026 16:20 +0530", "GlobePack proposal for Sunrise Foods - SF-RFQ-2026-118",
                """Hi Priya,

Great speaking last week. Our proposal is attached along with our ISO certificate. Happy to walk you
through the Bhiwadi buffer-stock model on a call.

Best,
Arjun""",
                ["GlobePack_Proposal_SunriseFoods.docx", "GlobePack_ISO_Certificate.pdf"])


# ---------------------------------------------------------------------------
# V4 - angled phone photo of a printed rate card
# ---------------------------------------------------------------------------
def make_v4(d: Path):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np

    Wc, Hc = 1240, 1754  # A4-ish at 150 dpi
    card = Image.new("RGB", (Wc, Hc), (250, 248, 240))
    dr = ImageDraw.Draw(card)
    F = lambda s, b=False: ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if b else "DejaVuSans.ttf")), s)
    FM = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 25)
    dr.text((70, 60), "VINAYAK PACKERS", font=F(54, True), fill=(20, 30, 90))
    dr.text((70, 130), "Corrugated Boxes  •  Sheets  •  Rolls", font=F(26), fill=(40, 40, 40))
    dr.text((70, 168), "Khasra 211, Village Daulatabad, Gurugram (HR)  •  98110 XXXXX", font=F(22), fill=(40, 40, 40))
    dr.text((70, 220), "RATE LIST  w.e.f. 01-10-2026     (Rates in Rs. per piece, GST extra)", font=F(27, True), fill=(0, 0, 0))
    dr.line((70, 262, 1170, 262), fill=(0, 0, 0), width=3)
    y = 285
    dr.text((70, y), "PLY  SIZE (LxWxH mm)  PRINT     RATE", font=FM, fill=(0, 0, 0))
    y += 40
    rows = []
    for l in lines:
        lid = l["line_id"]
        if lid not in truth["V4"] or lid.startswith(("LP", "CR")):
            continue
        H_ = l["height_mm"] + (5 if lid == "CB-101" else 0)  # their standard size differs by 5 mm
        size = f"{l['length_mm']}x{l['width_mm']}x{H_}"
        prt = {"Plain": "Plain", "1 colour flexo": "1 Col", "2 colour flexo": "2 Col"}[l["print"]]
        rows.append((lid, f"{l['ply']}    {size:<17}{prt:<7}{truth['V4'][lid]:>7.2f}"))
    for lid, txt in rows:
        dr.text((70, y), txt, font=FM, fill=(10, 10, 10))
        if lid == V4_SMUDGED:
            bb = dr.textbbox((70, y), txt, font=FM)
            smudge_xy = (bb[2] - 110, y)  # over the last ~6 characters (the price)
        y += 38
    y += 20
    dr.text((70, y), "LAYER PADS 3 ply 1200x1000  Rs " + f"{truth['V4']['LP-601']:.2f}" + " / pc", font=FM, fill=(10, 10, 10)); y += 38
    dr.text((70, y), "LAYER PADS 3 ply 1000x800   Rs " + f"{truth['V4']['LP-602']:.2f}" + " / pc", font=FM, fill=(10, 10, 10)); y += 38
    dr.text((70, y), "ROLL 2 ply 1200mm 150/120   Rs " + f"{truth['V4']['CR-701']:.2f}" + " / kg", font=FM, fill=(10, 10, 10)); y += 50
    dr.line((70, y, 1170, y), fill=(0, 0, 0), width=2); y += 20
    for t in ["3 ply = 150/120/150 GSM BF 18   •   5 ply = 180/120/150/120/180 GSM BF 20",
              "7 Ply, die-cut trays & partitions: rates on enquiry",
              "Delivery 3 days in Gurugram/Manesar. Payment 30 days. Min order 500 pcs/size."]:
        dr.text((70, y), t, font=F(23), fill=(30, 30, 30)); y += 34
    # a pen tick and a coffee ring for realism
    dr.ellipse((900, 1500, 1120, 1720), outline=(170, 130, 90), width=6)

    arr = np.array(card).astype(np.float32)
    # smudge over one price
    x0, y0 = smudge_xy
    yy, xx = np.mgrid[0:Hc, 0:Wc]
    blob = np.exp(-(((xx - (x0 + 52)) / 38) ** 2 + ((yy - (y0 + 16)) / 15) ** 2))
    arr = arr * (1 - 0.97 * blob[..., None]) + np.array([70, 62, 55]) * 0.97 * blob[..., None]
    card = Image.fromarray(arr.clip(0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.6))

    # place on a desk and warp in perspective (phone held at an angle)
    bg = Image.new("RGB", (1700, 2100), (92, 64, 44))
    bgarr = np.array(bg).astype(np.float32) + np.random.RandomState(3).normal(0, 9, (2100, 1700, 3))
    bg = Image.fromarray(bgarr.clip(0, 255).astype("uint8"))

    def coeffs(src, dst):
        A, B = [], []
        for (x, y_), (u, v) in zip(dst, src):
            A.append([x, y_, 1, 0, 0, 0, -u * x, -u * y_]); B.append(u)
            A.append([0, 0, 0, x, y_, 1, -v * x, -v * y_]); B.append(v)
        return np.linalg.solve(np.array(A, float), np.array(B, float)).tolist()

    dst = [(250, 210), (1470, 255), (1500, 1990), (170, 1915)]  # card corners on photo
    src = [(0, 0), (Wc, 0), (Wc, Hc), (0, Hc)]
    warped = card.transform((1700, 2100), Image.PERSPECTIVE, coeffs(src, dst), Image.BICUBIC)
    mask = Image.new("L", (Wc, Hc), 255).transform((1700, 2100), Image.PERSPECTIVE, coeffs(src, dst), Image.BICUBIC)
    photo = Image.composite(warped, bg, mask)
    # uneven lighting + sensor noise
    p = np.array(photo).astype(np.float32)
    gy, gx = np.mgrid[0:2100, 0:1700]
    light = 0.78 + 0.32 * (gx / 1700) * (1 - gy / 2100 * 0.5)
    p = p * light[..., None] + np.random.RandomState(5).normal(0, 6, p.shape)
    photo = Image.fromarray(p.clip(0, 255).astype("uint8")).filter(ImageFilter.GaussianBlur(0.8))
    photo = photo.resize((1275, 1575))
    photo.save(d / "IMG_20261020_142233.jpg", quality=78)

    write_email(d / "email.txt", "vinayakpackers.ggn@gmail.example (Sunil Yadav)", RFX["buyer"]["email"],
                "Tue, 20 Oct 2026 14:31 +0530", "rates",
                """Madam our rate list photo attached. Best rates in Gurugram, delivery 3 days.

for your questions:
ISO - not yet, applied, audit in december
testing - we get testing done from outside lab when customer asks
inks - yes water based food safe
capacity around 45 ton per month
payment 30 days, 60 days difficult
plant Daulatabad Gurugram, 15 km from Manesar

Sunil Yadav
Vinayak Packers
Sent from my phone""",
                ["IMG_20261020_142233.jpg"])


# ---------------------------------------------------------------------------
# V5 - the one-line email
# ---------------------------------------------------------------------------
def make_v5(d: Path):
    write_email(d / "email.txt", "vikram@bharatcorrugated.example (Vikram Sethi)", RFX["buyer"]["email"],
                "Wed, 21 Oct 2026 21:58 +0530", "Re: SF-RFQ-2026-118",
                """Priya,

₹42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra.

Vikram
Bharat Corrugated, Sonipat""")


# ---------------------------------------------------------------------------
def main():
    (ROOT / "rfx").mkdir(exist_ok=True)
    (ROOT / "history").mkdir(exist_ok=True)
    KEY.mkdir(exist_ok=True)
    (ROOT / "rfx" / "rfx_seed.json").write_text(json.dumps(RFX, indent=2, ensure_ascii=False), encoding="utf-8")

    import csv
    with open(ROOT / "history" / "contract_FY2025-26_bharat.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["line_id", "vendor", "basis", "rate", "unit", "price_per_uom"])
        w.writeheader()
        w.writerows(last_year)

    vendors = [
        ("V1", "Shree Ganesh Corrugators", "email + Excel", "2026-10-16", make_v1),
        ("V2", "Rajhans Packaging Industries", "email + PDF", "2026-10-18", make_v2),
        ("V3", "GlobePack Solutions India", "email + Word", "2026-10-19", make_v3),
        ("V4", "Vinayak Packers", "email + phone photo", "2026-10-20", make_v4),
        ("V5", "Bharat Corrugated (incumbent)", "email only", "2026-10-21", make_v5),
    ]
    manifest = []
    for vid, name, channel, recv, fn in vendors:
        d = RESP / vid
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            old.unlink()
        fn(d)
        manifest.append(dict(vendor_id=vid, vendor_name=name, channel=channel, received_on=recv,
                             files=sorted(p.name for p in d.iterdir())))
    (RESP / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    key = dict(
        note="Ground truth for evaluating extraction. NOT used by the app.",
        prices_inr_per_rfx_uom=truth,
        v1_alternate=V1_ALT, v4_smudged_line=V4_SMUDGED, usd_inr_used_by_vendor=USD_INR,
        planted_edges=[
            "V1 partitions & pads priced per 100 nos", "V1 CB-107 alternate offer at BF16 (non-compliant)",
            "V2 omits CB-302, CB-303 silently; regrets CR-701", "V2 5% discount conditional on > Rs 25 lakh annual order value (footnote)",
            "V2 ISO certificate expired 30-Apr-2026", "V2 payment 45 days vs 60 asked",
            "V3 prices in USD, invoiced at RBI rate on invoice date", "V3 layer pads priced per box of 100",
            "V3 21-day initial lead time, truckload MOQ", "V4 photo at an angle; CB-206 price smudged; CB-101 listed as 380x280x225",
            "V4 fails ISO + capacity (45 MT < 120)", "V4 has no 7-ply/trays/partitions",
            "V5 per-kg prices need box weights; 'rest same as last year' needs FY25-26 contract; freight extra; questionnaire unanswered",
        ],
    )
    (KEY / "ground_truth.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    total = sum(LINE[l]["annual_qty"] * p for l, p in truth["V1"].items())
    print(f"Dataset written. 30 lines, approx annual value at V1 prices: Rs {total/1e7:.2f} crore")


if __name__ == "__main__":
    main()
