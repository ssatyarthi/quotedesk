# Demo script and interview prep

## Before any demo (5 min)
- Start the app, open **2 · Inbox**, click **Read all responses** once (cached afterwards).
- Run `python tests/eval_extraction.py` and write the two numbers into `docs/decision_note.md`.
- Clear old decisions: **4 · Review queue → Audit trail → Reset all decisions**.
- Have one extra messy file ready (your own, or one they send) to try **Add a vendor reply** live.

## Recorded walkthrough (about 7 minutes)

**0:00 · The problem (20 sec).** "Five vendors, five formats, nobody used the template. The buyer loses four
days retyping, and still can't fully trust the spreadsheet. I optimised for trust, not just speed."

**0:20 · 1 · Draft RFx.** Click the first starter prompt. Point out: it pulled the 30 items from the item
master, **computed box weights** (needed later for per-kg quotes), set price basis and payment terms, and
added a questionnaire with pass rules. "Half the mess in replies is caused by vague RFx documents, so I fix it
upstream." Click **Send** and open one stubbed email.

**1:30 · 2 · Inbox.** Show the angled photo, then Bharat's one-line email. Click **Re-read with AI** on
Vinayak to show extraction is live. Open "What the AI read".

**2:30 · 3 · Compare.** Read the KPI row: last year ₹3.91 Cr; cheapest-anything looks like savings, but
**cheapest qualified is above last year**. Then select three rows:
- **CB-206**: Vinayak's smudged price is red and *can't win*.
- **LP-601**: GlobePack's "USD per box": the math shows ÷100, then × FX.
- **CB-101**: Bharat's ₹38/kg × 325 g + assumed freight; Vinayak's 225 mm vs 220 mm match.
Scroll to the questionnaire: Rajhans is **Contradicted** (says certified, certificate expired April 2026).

**4:00 · 4 · Review queue.** "Everything the system isn't sure about, worst first." Override CB-206 after
"calling the vendor" and show the ✔ buyer-confirmed mark back on Compare.

**4:45 · 5 · Ask.** Questions in this order:
1. *"What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire? Total vs last year?"* (the VP's question from the brief)
2. *"Which numbers on this comparison should I not trust yet, and why?"*
3. *"If Rajhans sends a renewed ISO certificate and earns its 5% discount, what does the split look like then?"*
4. *"That's 3 vendors to manage. What does it cost to give everything to just two?"*
5. *"Chart landed price vs last year for the 5-ply range, by vendor."*
6. *"Draft a one-page award recommendation for my VP."* Download the .docx.
Open "How I got this" once to show the SQL.

**6:30 · Close.** "The interesting problem isn't reading. It's resolving the flags. Next I'd turn every
open flag into a one-click clarification email whose reply is read back in automatically. Here, one
renewed certificate moves the award below last year."

## Live demo: what they'll probably try
- **Their own file:** Inbox → *Add a vendor reply* → upload → Read. If it misreads, show how it surfaces (confidence, peer-ratio check) instead of hiding it.
- **"Change the FX rate":** sidebar → Buyer assumptions. GlobePack's prices move and every affected number updates.
- **Ask something unexpected:** the analyst writes new SQL, so nothing is scripted. If it can't answer, it says what data is missing.

## Questions to expect, with short answers
- **Why not let the model do the conversions?** Arithmetic in code is reproducible and auditable; a model "fixing" ₹42/kg into a per-box price is exactly the error a buyer can't see. The model does what code can't: reading.
- **How do you know the extraction is right?** Three layers: the model's own confidence, a cross-vendor ratio check that catches unit errors regardless, and source text for every cell. Plus an eval against an answer key: [your numbers].
- **What about 500 lines and 20 vendors?** Extract per document section in parallel, cache by file hash, and add a targeted re-check pass on low-confidence fields only. Cost today is about $1 per five-vendor RFx.
- **Hallucination in the chat?** The analyst can only answer through read-only SQL, and the queries are shown. It must name exclusions and open flags.
- **Can engineering build forward from this?** `core/` has no UI code (ingest, extract, normalize, store, agents), so it can sit behind an API. Streamlit is the throwaway part.
- **How would you measure success?** Time from last reply to award decision; % of prices needing human touch; override rate (extraction quality); share of flags dismissed as noise (flag precision); share of sourcing events run through it (adoption).
- **What would you do differently?** Weight-based per-kg conversion relies on an estimated weight, so I'd make vendors state weight per box in the RFx. Grouped flags can hide one bad line inside a group of 20, so high-severity flags stay line by line for that reason.
