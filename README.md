# QuoteDesk

**Draft an RFx with AI, read whatever vendors send back, and decide the award with numbers you can trust.**

| | |
|---|---|
| **Live app** | _link added after deployment_ (password on request) |
| **Walkthrough video** | _link added after recording_ |
| **Product write-up** | _link to the deck / document_ |

---

## The scenario

A sample buyer, Sunrise Foods (a fictional FMCG company with a plant at Manesar), re-sources **30 lines of corrugated packaging, about ₹4 crore a year**. Five vendors reply, none in the template:

| Vendor | What they sent | What makes it hard |
|---|---|---|
| Shree Ganesh | Excel in their own layout | Pads priced "per 100 nos"; an off-spec alternate offer |
| Rajhans | PDF on letterhead | 27 of 30 lines; 5% discount in a footnote; expired ISO certificate |
| GlobePack | Word doc, prices in sentences | USD; pads "per box" with no pack size |
| Vinayak | Phone photo of a rate card, at an angle | One price smudged; sizes slightly off; fails quality |
| Bharat (incumbent) | One-line email | "₹42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra" |

## What it does

1. **RFx co-pilot:** the buyer describes the need in chat; it drafts lines, terms and a quality questionnaire, and won't send until unit, currency and freight are pinned down.
2. **Reads any reply:** Excel, PDF, Word, photo or email, as sent.
3. **One comparison:** same 30 lines, same unit, same currency, with questionnaire results and documents alongside.
4. **Review queue:** everything the system isn't sure about, worst first, plus a drafted clarification email per vendor.
5. **Ask the analyst:** plain-language questions answered from the data, with tables, charts and exports.
6. **Award:** the lowest-cost split under the buyer's rules (max vendors, who may win, payment terms), and a memo for the approver.

## How it earns trust

- **AI reads, code calculates.** The model records each price exactly as written; unit, currency, discount and freight conversions are plain code, with every step shown under the number.
- **Every number has a source.** Click any cell to see the vendor's own words, the file and the location.
- **Unsure numbers can't decide the award.** A smudged digit, an unclear pack size or an ambiguous rule is held back until the buyer confirms it.
- **The analyst can't make numbers up.** It answers only by querying the extracted data, and shows its queries.

## Results on this dataset

| Measure | Result |
|---|---|
| Prices read exactly | 132 of 140 (the other 8 are genuine ambiguities, flagged instead of guessed) |
| Wrong prices that could win a line | **0** |
| Planted edge cases handled | 16 of 16 |

---

<details>
<summary><b>How it's built</b></summary>

- Python + Streamlit, Claude (Anthropic API), DuckDB for the analyst's queries
- `core/extract.py`: AI extraction with source and confidence per value
- `core/normalize.py`: conversions, quality rules, flags (no AI)
- `core/award.py`: exact award search over vendor combinations (no AI)
- `core/analyst.py`, `core/copilot.py`, `core/clarify.py`: the AI agents
- `data/generate_dataset.py`: builds the fabricated dataset; `tests/eval_extraction.py` scores the AI against an answer key the app never reads
- Email sending and receiving are stubbed (emails are written to files)

Run it yourself: see [docs/SETUP.md](docs/SETUP.md).

</details>
