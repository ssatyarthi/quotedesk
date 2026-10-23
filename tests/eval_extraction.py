"""
Scores the REAL AI extraction (cache/extractions/*.json) against the answer key
the dataset generator wrote. Run after "Read all responses":

    python tests/eval_extraction.py

Prints price accuracy per vendor and whether each planted "ugly edge" was caught.
The app never reads the answer key; this is how we measure it.
"""
import json
import sys

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ingest, normalize  # noqa: E402
from core.config import Assumptions  # noqa: E402
from core.extract import load_all_cached  # noqa: E402

KEY = json.loads((ROOT / "data/_answer_key/ground_truth.json").read_text())
RFX = json.loads((ROOT / "data/rfx/rfx_seed.json").read_text())


def main():
    man = ingest.load_manifest()
    ext = load_all_cached([m["vendor_id"] for m in man], RFX)
    if not ext:
        print("No cached extractions. Run the app and click 'Read all responses' first.")
        return
    # same FX the vendor used, so USD lines can be scored exactly
    t = normalize.build(RFX, ext, man, Assumptions(usd_inr=KEY["usd_inr_used_by_vendor"], award_date="2026-10-22"))
    q, f = t["quotes"], t["flags"]
    truth = KEY["prices_inr_per_rfx_uom"]
    total_ok = total_n = 0
    print("\nPRICE ACCURACY (Rs per RFx unit, before freight; within 0.5%)")
    for vid, lines in truth.items():
        if vid not in ext:
            print(f"  {vid}: not read")
            continue
        ok, wrong, missing = 0, [], []
        for lid, p in lines.items():
            r = q[(q.vendor_id == vid) & (q.line_id == lid) & (~q.is_alternate)]
            got = r.unit_price_inr.iloc[0] if not r.empty else None
            if got is None or got != got:
                missing.append(lid)
            elif abs(got - p) / p <= 0.005:
                ok += 1
            else:
                wrong.append(f"{lid}: got {got:.2f}, expected {p:.2f}")
        total_ok += ok
        total_n += len(lines)
        print(f"  {vid}: {ok}/{len(lines)} correct" + (f" | no price: {', '.join(missing)}" if missing else ""))
        for w in wrong:
            low = q[(q.vendor_id == vid) & (q.line_id == w.split(':')[0])].confidence.iloc[0]
            print(f"      x {w}  (shown to buyer as confidence={low})")
    print(f"  OVERALL: {total_ok}/{total_n} = {total_ok / max(total_n, 1):.1%}")
    # The metric that matters most: a wrong price the buyer could act on without being warned
    silent = []
    for vid, lines in truth.items():
        for lid, p in lines.items():
            r = q[(q.vendor_id == vid) & (q.line_id == lid) & (~q.is_alternate)]
            if not r.empty and pd.notna(r.unit_price_inr.iloc[0]) and abs(r.unit_price_inr.iloc[0] - p) / p > .005 \
                    and r.confidence.iloc[0] in ("high", "medium"):
                silent.append(f"{vid} {lid} ({r.confidence.iloc[0]})")
    print(f"  WRONG PRICES THAT COULD WIN A LINE (not held back as low confidence): {len(silent)}"
          + (f" -> {', '.join(silent)}" if silent else ""))

    def has(vid, cat, line=None):
        g = f[(f.vendor_id == vid) & (f.category == cat)]
        return not g.empty if line is None else not g[g.line_id == line].empty

    v4s = q[(q.vendor_id == "V4") & (q.line_id == KEY["v4_smudged_line"])]
    checks = [
        ("V1 pads/partitions per 100 nos converted", all(abs(q[(q.vendor_id == 'V1') & (q.line_id == l) & (~q.is_alternate)].unit_price_inr.iloc[0] - truth['V1'][l]) / truth['V1'][l] < .005 for l in ("PT-501", "LP-601"))),
        ("V1 CB-107 alternate (BF16) kept separate", not q[(q.vendor_id == 'V1') & (q.line_id == 'CB-107') & (q.is_alternate)].empty),
        ("V2 silent omissions CB-302/CB-303 flagged", has("V2", "silently_missing", "CB-302") and has("V2", "silently_missing", "CB-303")),
        ("V2 CR-701 regret recognised", not q[(q.vendor_id == 'V2') & (q.line_id == 'CR-701') & (q.status == 'regret')].empty),
        ("V2 footnote discount found as conditional", has("V2", "conditional_discount")),
        ("V2 expired ISO certificate caught", has("V2", "quality:Q1")),
        ("V3 USD + FX clause flagged", has("V3", "fx_exposure")),
        ("V3 layer pads 'per box': converted, or pack size flagged instead of guessed",
         (lambda r: (pd.notna(r.unit_price_inr) and abs(r.unit_price_inr - truth['V3']['LP-601']) / truth['V3']['LP-601'] < .005)
          or (pd.isna(r.unit_price_inr) and has("V3", "pack_size_unknown", "LP-601")))(q[(q.vendor_id == 'V3') & (q.line_id == 'LP-601')].iloc[0])),
        ("V5 '38 for the 3-ply' on non-carton items: held back as ambiguous, or read as 'same as last year'",
         all((r.confidence == "low") or (abs(r.unit_price_inr - truth['V5'][r.line_id]) / truth['V5'][r.line_id] < .005)
             for r in q[(q.vendor_id == 'V5') & q.line_id.str.startswith(("DT", "PT", "LP"))].itertuples())),
        ("V4 smudged price not shown as high confidence", not v4s.empty and v4s.confidence.iloc[0] != "high"),
        ("V4 380x280x225 size mismatch flagged", has("V4", "match_confidence", "CB-101")),
        ("V4 fails quality", (t["vendors"].set_index("vendor_id").loc["V4", "quality_status"] == "FAILED")),
        ("V5 per-kg converted with weights", has("V5", "weight_based_conversion")),
        ("V5 'same as last year' resolved from contract", has("V5", "inherited_price")),
        ("V5 freight extra applied", has("V5", "freight_extra")),
        ("V5 unanswered questionnaire -> unresolved", (t["vendors"].set_index("vendor_id").loc["V5", "quality_status"] == "UNRESOLVED")),
    ]
    print("\nUGLY EDGES CAUGHT")
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'MISS'}  {name}")
    print(f"  {sum(ok for _, ok in checks)}/{len(checks)}\n")


if __name__ == "__main__":
    main()
