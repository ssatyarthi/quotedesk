# QuoteDesk: Kill the Quote Spreadsheet

Aerchain PM take-home. A buyer drafts an RFx with an AI co-pilot, five vendors reply in five
different formats, the system reads every reply, lands them in one comparison (same lines,
same units, same currency), shows what it isn't sure about, and lets the buyer ask questions
in plain language all the way to an award memo.

**Scenario:** Sunrise Foods (fictional FMCG, plant at Manesar) re-sourcing 30 lines of
corrugated packaging, about ₹4 crore a year. Five replies: an Excel that ignores the template,
a letterhead PDF with a discount in the footnote, a Word doc with prices in prose and in USD,
an angled phone photo of a rate card, and a one-line email ("₹42/kg for the 5-ply, 38 for
the 3-ply, rest same as last year, freight extra").

---

## Run it (about 10 minutes the first time)

1. **Install Python 3.11 or newer** from python.org. On Windows, tick **"Add python.exe to PATH"** in the installer.
2. **Get an API key:** console.anthropic.com → Billing (add ~$10 credit) → API Keys → Create key.
3. **Unzip** this folder anywhere.
4. **Start it**
   - Windows: double-click `run.bat`. The first time, it installs packages and opens `.env` in Notepad.
     Paste your key after `ANTHROPIC_API_KEY=`, save, close, and double-click `run.bat` again.
   - Mac: open Terminal in the folder, then `chmod +x run.sh && ./run.sh`, paste the key into `.env`, run `./run.sh` again.
5. The app opens at http://localhost:8501.
6. Go to **2 · Inbox → Read all responses**. It reads all five vendors in parallel (about 1–3 minutes, roughly $1).
   Results are cached in `cache/`, so the demo is instant afterwards. Use "Re-read with AI" on a vendor
   to show it live.
7. Optional: measure accuracy against the answer key: `python tests/eval_extraction.py`
   (activate the venv first: `.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on Mac).

Manual install instead of the scripts: `python -m venv .venv`, activate it, `pip install -r requirements.txt`,
copy `.env.example` to `.env` and add the key, then `streamlit run app.py`.

## Put it online (Streamlit Community Cloud, free)

1. **Read the replies once locally** (step 6 above) so `cache/extractions/` holds the AI's reads.
   They're committed with the code, so the hosted app opens instantly and costs nothing until someone re-reads.
2. **Push to GitHub:** create a repository (private is fine) and upload this folder.
   `.env` is excluded by `.gitignore`, so your key never goes to GitHub.
3. Go to **share.streamlit.io** → sign in with GitHub → **Create app** → pick the repository, branch `main`,
   main file `app.py` → under **Advanced settings** choose Python 3.12 and paste into **Secrets**:
   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "a-password-you-share-with-aerchain"
   ```
4. **Deploy.** You get a link like `https://<name>.streamlit.app`; that's the live link. The GitHub URL is
   the "where it's hosted" link.
5. **Protect your credit:** set a monthly spend limit in the Anthropic console (Settings → Limits).

Notes for the hosted demo: everyone using the link shares the same review decisions (use
*Review queue → Audit trail → Reset all decisions* before a demo), and the app sleeps after a few days
without visits; opening the link wakes it in under a minute.

## The six screens

| Screen | What it does |
|---|---|
| 1 · Draft RFx | Chat with the co-pilot; it drafts lines from the item master, computes box weights, sets terms and a questionnaire, checks gaps, then "sends" (stubbed: `.eml` files in `outbox/`). |
| 2 · Inbox | Each vendor's raw files exactly as received, plus what the AI read from them. |
| 3 · Compare | 30 lines × 5 vendors in landed ₹ per RFx unit. Select a row to see, for every vendor, the verbatim source text, file location, conversion math and flags. Questionnaire results, terms and attachments below. |
| 4 · Review queue | Everything the system wasn't sure about, worst first. Accept, dismiss, or override a price after checking with the vendor (audit trail kept). AI-drafted clarification email per vendor with open issues (sending stubbed). |
| 5 · Ask | Natural-language analyst. Answers only by running SQL on the extracted data or the award optimizer; queries are shown under each answer. Tables, charts, Excel exports, award memo (.docx). |
| 6 · Award | Exact lowest-cost split under the buyer's rules: max vendors (default 3), who may win (unresolved vendors included with a warning), price basis (landed or + payment terms), conditional discounts only if earned. Shows what each rule costs. |

## How it's built

```
vendor files ──► ingest.py      format → model-readable content, keeps addresses (sheet!cell, page, ¶)
             ──► extract.py     Claude reads everything; records values AS WRITTEN (vendor's unit,
                                currency) + source + confidence. No arithmetic.
             ──► normalize.py   code: unit basis → RFx unit, per-kg × box weight, USD × FX,
                                discounts, freight, quality rules, cross-vendor sanity checks, flags
             ──► store.py       DuckDB tables
             ──► analyst.py     Claude agent with run_sql / show_table / show_chart / export / memo tools
```

- `data/generate_dataset.py` builds the whole fabricated dataset; `data/_answer_key/` holds the ground
  truth. **The app never reads the answer key.** Only `tests/eval_extraction.py` does, to score the AI.
- `tests/fixtures.py` contains hand-made stand-ins for model output, used only to test the deterministic code.
- Plumbing that is stubbed: email sending (writes `.eml` to `outbox/`) and receiving (vendor replies sit
  in `data/responses/`). The AI loops (extraction, co-pilot, analyst) are real.

See `docs/decision_note.md` for the one-page note and `docs/demo_script.md` for the walkthrough.
