"""Dump the Technical Notes pages that define how summary and overall ratings are calculated.

Run:
  python methodology_peek.py
Writes methodology_<year>.txt for each year.
"""
import re
from pathlib import Path

import pdfplumber

KEYWORDS = [
    r"METHODOLOGY FOR CALCULATING PART C AND PART D SUMMARY",
    r"METHODOLOGY FOR CALCULATING THE OVERALL",
    r"COMPLETING THE SUMMARY AND OVERALL",
    r"APPLYING THE IMPROVEMENT MEASURE",
    r"APPLYING THE REWARD FACTOR",
    r"Reward Factor",
    r"Final Adjustment Categories and CAI Values",
    r"Rounding Rules for Summary and Overall",
    r"Calculation of Weighted Star Rating and Variance",
]

for ydir in sorted(Path("data/raw").iterdir()):
    pdf_path = ydir / f"{ydir.name}_technical_notes.pdf"
    if not pdf_path.exists():
        continue
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.count("....") > 5:          # table of contents
                continue
            hits = [k for k in KEYWORDS if re.search(k, text, re.I)]
            if hits:
                out.append(f"\n{'=' * 25} PDF PAGE {n}  (matched: {', '.join(hits)}) {'=' * 25}\n{text}")
                for t in page.extract_tables():
                    out.append("--- table ---")
                    for row in t:
                        out.append(" | ".join("" if c is None else str(c).replace("\n", " ") for c in row))
    Path(f"methodology_{ydir.name}.txt").write_text("\n".join(out))
    print(f"{ydir.name}: {len(out)} blocks written to methodology_{ydir.name}.txt")
