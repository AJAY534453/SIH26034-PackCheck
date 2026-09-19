# POCKET — Scanning Accuracy Report

_Generated: 2026-09-17T19:34:18_

This report contains ONLY measured benchmark results on the images listed in
`data/benchmark/manifest.csv` with human-entered ground truth. Unit tests and synthetic
checks are reported separately in TESTING.md and are never blended into these numbers.
No accuracy percentage is claimed beyond what is measured below.

## Dataset

- Manifest entries: 0
- Evaluated (image + ground truth present): 0
- Ground truth source: human-entered JSON per image (`data/benchmark/ground_truth/`)
- Annotated products in `data/annotations/`: 1
- Product views evaluated (multi-view fusion): 1

## Result: INSUFFICIENT DATA

No real images with ground truth have been added to the benchmark yet.
No accuracy is claimed. To measure accuracy:

1. Place real package photos in `data/benchmark/`.
2. Add one line per image to `manifest.csv`.
3. Copy `ground_truth/_schema_example.json`, fill in the values **you** can read,
   and save as `ground_truth/<image>.json`.
4. Re-run: `.venv/Scripts/python scripts/run_benchmark.py`

## Field-level results (annotated products, multi-view fusion)

**Sample too small for rates:** 1 product(s) with ground truth
across 12 field cases. Per-field counts are listed for transparency; no
accuracy percentage is claimed from them.

| Field | Images w/ GT | Exact | Normalized | Wrong | Not detected | Accuracy (exact+norm) | Mean conf (correct) |
|---|---|---|---|---|---|---|---|
| mrp | 1 | 0 | 0 | 0 | 1 | 0% | 0% |
| net_quantity_value | 1 | 1 | 0 | 0 | 0 | 100% | 87% |
| net_quantity_unit | 1 | 1 | 0 | 0 | 0 | 100% | 87% |
| batch_lot | 1 | 1 | 0 | 0 | 0 | 100% | 81% |
| date_manufacturing | 1 | 0 | 0 | 0 | 1 | 0% | 0% |
| date_best_before | 1 | 0 | 1 | 0 | 0 | 100% | 88% |
| fssai_license | 1 | 1 | 0 | 0 | 0 | 100% | 90% |
| manufacturer | 1 | 0 | 1 | 0 | 0 | 100% | 86% |
| consumer_care_phone | 1 | 1 | 0 | 0 | 0 | 100% | 88% |
| consumer_care_email | 1 | 1 | 0 | 0 | 0 | 100% | 88% |
| website | 1 | 1 | 0 | 0 | 0 | 100% | 90% |
| product_name | 1 | 1 | 0 | 0 | 0 | 100% | 83% |

A field counts as correct when the observed value is semantically equal to the human
reading OR contains it. Containment is required by two deliberate production contracts:
`manufacturer` carries the entity together with its address (the address is also offered
separately as `manufacturer_address`), and a duration is reported with its reference
('12 months from the date of manufacture').

Per-product detail:

| Product | Field | Expected (human) | Observed | Outcome |
|---|---|---|---|---|
| p0001 (1 view(s)) | product_name | SUKKUKAAPI | SUKKUKAAPI | correct |
| p0001 (1 view(s)) | manufacturer | GURUCHARAA PRODUCT | GURUCHARAAPRODUCT, 19/115,,KonguNagar,MuthurRoad, SenapathyPalayam,Vellakovil-638111,, Tirupur (Dt), Tamilnadu,India. | correct |
| p0001 (1 view(s)) | mrp | 200 | — | NOT DETECTED |
| p0001 (1 view(s)) | net_quantity_value | 500 | 500 | correct |
| p0001 (1 view(s)) | net_quantity_unit | g | g | correct |
| p0001 (1 view(s)) | batch_lot | 90130 | 90130 | correct |
| p0001 (1 view(s)) | date_manufacturing | 2025-02-04 | — | NOT DETECTED |
| p0001 (1 view(s)) | date_best_before | 12 months | {"type": "DURATION_FROM_REFERENCE", "duration": "12 months", "reference": "MANUFACTURING_DATE", "display": "12 months"} | correct |
| p0001 (1 view(s)) | fssai_license | 12419027000075 | 12419027000075 | correct |
| p0001 (1 view(s)) | consumer_care_phone | 09487593380 | 09487593380 | correct |
| p0001 (1 view(s)) | consumer_care_email | gurucharaa_product@yahoo.com | gurucharaa_product@yahoo.com | correct |
| p0001 (1 view(s)) | website | www.vedhaproducts.com | www.vedhaproducts.com | correct |


## Limitations

- Sample sizes are small; percentages are indicative only, not guarantees.
- Ground truth reflects one human reading; ambiguous labels may differ legitimately.
- A field absent from the ground truth is NOT evaluated: omitted means 'not visible',
  never 'the declaration is absent'.
- A declaration reported NOT DETECTED may simply sit on a package face that has not
  been ingested yet (see the product's `_views_note` in data/annotations/). Multi-view
  products are evaluated on the union of their views, so ingesting the remaining faces
  is what turns those misses into measurements.
- Results must not be generalized to arbitrary images or lighting conditions.
