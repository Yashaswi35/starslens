# StarsLens: Medicare Star Ratings Analytics

Replicates CMS's Medicare Advantage and Part D Star Ratings from public data, then measures how CMS's 2026 reweighting moved plans across the 4-star bonus threshold. Includes an evaluation of local LLMs extracting methodology details from CMS's 600+ pages of Technical Notes.

## Key findings

**1. CMS's 2026 reweighting pushed more plans out of bonus status than into it.** For 2026, CMS cut the weight of patient experience, complaints, and access measures from 4 to 2. Recomputing 2026 ratings under the old weights, with identical code:

| Effect on top-level ratings (561 rated contracts) | Contracts |
|---|---|
| Rating changed by half a star | 140 (25%) |
| Lower under the new weights | 88 |
| Higher under the new weights | 52 |
| **Fell below 4 stars only because of the reweighting** | **31** |
| **Reached 4+ stars only because of the reweighting** | **16** |

Losses concentrate in large national carriers (UnitedHealth and Elevance each lost 4 contracts, CVS and Humana each a net of 2), while gains went mostly to regional and provider-affiliated plans (SCAN, Devoted, Alignment, Providence). Since 4 stars is the threshold for quality bonus payments, the reweighting redistributed bonus eligibility, not just scores.

*How it was tested:* reward factor thresholds are percentiles of all contracts' scores, so they shift when weights shift. Both scenarios compute thresholds from the data, and this method reproduces CMS's published 2026 thresholds within 0.01 to 0.05 stars. Counts are contracts, not members, since enrollment is not in this dataset.

**2. The replication matches 97.5% of 5,097 official CMS ratings.** CMS's Technical Notes state that exact replication is not possible from public data. Each methodology step was added in turn, and the match rate shows what each one contributes:

| Step | Match rate |
|---|---|
| Weighted mean of measure stars | 74.1% |
| + reward factor | 88.6% |
| + Categorical Adjustment Index (CAI) | 94.5% |
| + improvement measure hold-harmless rule | 97.4% |
| + 2026 new-measure hold-harmless rule | 97.5% |

| Rating | 2024 | 2025 | 2026 |
|---|---|---|---|
| Overall (MA-PD) | 98.0% | 99.8% | 90.3% |
| Part C summary | 98.5% | 99.8% | 99.8% |
| Part D summary | 94.3% | 99.4% | 97.7% |

Remaining gaps are documented, not tuned away. 2024 Part D misses mostly disappear without the reward factor, consistent with CMS's July 2024 court-ordered recalculation shifting thresholds after the Technical Notes were published. 2026 overall ratings run about half a star low for 50 contracts, and no alternative rule tested explains them, which points to CMS's July 2026 update of the 2026 summary file.

**3. Local LLMs extracted measure specifications with up to 99.2% accuracy.** Llama 3.2 (3B) and Gemma 3 (4B), run locally through Ollama, read all 129 measure specification sections and returned structured JSON scored against rule-based ground truth.

| | Gemma 3 (4B) | Llama 3.2 (3B) |
|---|---|---|
| All fields correct | 99.2% | 95.3% |
| Measure weights correct | 100% | 100% |
| Median seconds per measure | 3.7 | 2.4 |

Llama's misses are all one pattern: it marked the Quality Improvement measures as included in the improvement calculation, apparently inferring from the measure's name instead of reading the field. Gemma's only miss came from a section where the PDF's embedded formula extracts as garbage characters. The evaluation also caught a CMS documentation inconsistency: in 2026, the spec pages call Improving Physical and Mental Health "Outcome Measures," while Attachment G calls them "Intermediate Outcome Measures."

## Data

| Source | Coverage |
|---|---|
| [CMS Star Ratings Data Tables](https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data) | 2024 to 2026, 2,415 contract-years, 103,737 measure-level ratings, 42 to 45 measures per year |
| CMS Star Ratings Technical Notes (PDF) | 2024 to 2026, about 210 pages each: measure specifications, weights, reward factor thresholds, CAI values, rounding rules |

The 2024 tables are CMS's July 2024 recalculated release, and the 2026 summary ratings come from the July 2026 master table.

## Architecture

```
CMS Data Tables (Excel)            CMS Technical Notes (PDF)
   │ load.py                          │ extract_reference.py   Attachment G weights, spec sections, regex baseline
   ▼                                  │ parse_methodology.py   reward factor thresholds, CAI values
starslens.duckdb ◄────────────────────┤ llm_extract.py         Llama 3.2 + Gemma 3, scored against ground truth
   │ build.py + sql/   staging tables, fct_contract_year, 8 validation checks
   ▼
replicate.py        CMS methodology, step by step, match rate per step
counterfactual.py   2026 ratings under pre-2026 weights
```

## Data quality

| Issue | Handling |
|---|---|
| Measure codes renumbered in 2026 (Controlling Blood Pressure: C11 to C14) | Cross-year joins on part plus normalized measure name |
| Same measure names in Part C and Part D | Part included in the measure key |
| About a third of measure rows are not rated ("Plan too new," "Not enough data") | Kept as explicit statuses, never averaged in |
| Encoding differs by year in the CSVs | Loaded from the Excel master tables instead |
| Attachment G tables span untitled pages, and two specs reference attachments mid-section | Page-range parsing with a text fallback, verified to 100% coverage |
| Validation | 6 blocking checks pass (uniqueness, star ranges, half-star summaries, coverage) |

## Run it

```bash
pip install duckdb pandas pyarrow pdfplumber requests ollama pydantic openpyxl
python download.py && python load.py && python build.py
python extract_reference.py && python llm_extract.py      # needs Ollama with llama3.2 and gemma3:4b
python methodology_peek.py && python methodology_peek2.py && python parse_methodology.py
python replicate.py && python counterfactual.py
```

Logs from the runs behind these numbers are in `results/`.
