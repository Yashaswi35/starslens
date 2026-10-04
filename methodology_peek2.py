"""Grab the 2026 variance thresholds (Table 9) and the new-measure rule from the Technical Notes.

Run:
  python methodology_peek2.py
Writes methodology_2026_extra.txt.
"""
import re
from pathlib import Path

import pdfplumber

out = []
with pdfplumber.open("data/raw/2026/2026_technical_notes.pdf") as pdf:
    for n, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        if text.count("....") > 5:
            continue
        if re.search(r"Variance Thresholds|new measures? (?:are|is|will be) |Applying the New Measure|"
                     r"with and without (?:the )?new measures|without the new measures", text, re.I):
            out.append(f"\n{'=' * 25} PDF PAGE {n} {'=' * 25}\n{text}")
Path("methodology_2026_extra.txt").write_text("\n".join(out))
print(f"{len(out)} pages written to methodology_2026_extra.txt")
