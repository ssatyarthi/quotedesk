# QuoteDesk: what I decided, and what I left out

**Built:** RFx co-pilot → five messy vendor replies → one normalized comparison with provenance and a
review queue → a natural-language analyst that answers through SQL, up to an award memo.
Category: corrugated packaging for an FMCG plant (30 lines, ~₹4 Cr/yr, 5 vendors).

## The decisions that matter

1. **The AI reads; code calculates.** The model records each value exactly as written (₹42 *per kg*,
   USD 24.51 *per box of 100*) with the verbatim snippet, its location and a confidence level. Unit,
   currency, discount and freight conversions are deterministic code, and every step is printed under the
   number. A buyer with ₹4 Cr at stake can re-derive any cell; the model can't silently "fix" a price.
2. **Normalize to the buyer's unit, not the vendor's.** Everything lands as landed ₹ per RFx unit, ex-GST.
   Per-kg quotes are converted with the box weight the RFx computed from dimensions and GSM. That is why the
   co-pilot calculates weights when drafting: you can't compare ₹/kg with ₹/box without them.
3. **Uncertainty is shown, never hidden.** Each price is high, medium or low confidence. Low-confidence prices
   (a smudged digit, a price 97× the peer median) **cannot win a line** until the buyer confirms them. A
   cross-vendor ratio check catches "per box" vs "per 100 pieces" errors even when the model misreads.
4. **Absence has three meanings.** "Regret", "on enquiry" and "not mentioned at all" are tracked separately,
   because "27 of 30 quoted" is a negotiation lever, not a blank cell.
5. **Claims are checked against evidence.** Quality rules are code, not model opinion. A vendor who says
   "ISO certified" but attaches a certificate that expired in April is **Unresolved**, not Pass, and not Fail.
   One phone call can fix it, and the analyst says so.
6. **Ambiguous commercials stay the buyer's call.** A 5% discount "if annual orders exceed ₹25 lakh" is not
   applied by default (there's a toggle). "Freight extra" uses a visible ₹/kg assumption. The FX rate is an
   input with its source and date. "Rest same as last year" is resolved from last year's contract and
   flagged for written confirmation.
7. **The analyst can only answer through SQL.** Every number comes from a query, and the queries are shown
   under the answer. The analyst must state its filters (which vendors were excluded and why) and name open
   flags that affect the answer.
8. **Review once, not 140 times.** Medium issues are grouped per vendor and type (e.g. "20 per-kg
   conversions"). High issues are decided line by line, with overrides and an audit trail.
9. **Measured, not asserted.** The dataset generator writes an answer key the app never sees;
   `tests/eval_extraction.py` scores price accuracy and the 15 planted edge cases.
   Result on my run: **[__/140 prices exact, __/15 edges caught]**.

## Deliberately left out

- Real email in and out (stubbed as `.eml`), a vendor portal, logins, multi-user state
- Spec compliance from test reports (BCT values are read but not checked against the spec)
- Full total cost of ownership: cost of 45- vs 60-day payment, MOQ and truckload constraints, FX hedging (shown as terms, not priced)
- GST treatment, currencies other than INR and USD, negotiation rounds
- Scale: hundreds of lines would need chunked extraction and a second "re-check" pass on low-confidence fields

## Where I think the interesting problem actually is

**Retyping isn't the real cost. Trust is.** Extraction is now good enough; what stops a buyer from awarding
off the screen is not knowing which three numbers are wrong. So the review queue and the evidence trail are
the product, not the chat. Two things follow:

- **Fix the RFx, not just the replies.** Most of the mess (per kg vs per box, freight unstated, "same as last
  year") comes from RFx documents that never pinned down unit, weight and price basis. The co-pilot enforces
  those upstream.
- **Turn flags into clarification loops.** The next feature is not a better chart. It is a one-click,
  vendor-specific clarification email for each open flag ("please send your renewed ISO certificate",
  "confirm the CB-206 price", "restate the 7-ply prices"), with the replies read back in automatically. In
  this dataset, the cheapest qualified award costs more than last year. If Rajhans sends a renewed
  certificate, it wins enough lines to earn its 5% footnote discount, and the award drops below last year.
  The money is in resolving flags, not in reading faster.
