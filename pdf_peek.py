"""Look inside a Technical Notes PDF: a real measure specification page and the weight tables.

Run:
  python pdf_peek.py 2026
Writes pdf_peek_<year>.txt.
"""
import re
import sys
from pathlib import Path

import pdfplumber

year = sys.argv[1] if len(sys.argv) > 1 else "2026"
pdf_path = Path(f"data/raw/{year}/{year}_technical_notes.pdf")
out = []
with pdfplumber.open(pdf_path) as pdf:
    pages = [(i + 1, p.extract_text() or "") for i, p in enumerate(pdf.pages)]

    def is_toc(t):
        return t.count("....") > 5

    spec = [n for n, t in pages if not is_toc(t) and re.search(r"^Measure:\s*[CD]\d{2}", t, re.M)]
    weight_tables = [n for n, t in pages if not is_toc(t) and re.search(r"Table G-[12]", t)]
    out.append(f"{pdf_path.name}: {len(pages)} pages")
    out.append(f"measure spec pages (non-TOC): {len(spec)} -> {spec}")
    out.append(f"weight table pages (Table G-1/G-2): {weight_tables}")

    def dump(n, label):
        out.append(f"\n{'=' * 30} PDF PAGE {n} ({label}) {'=' * 30}")
        out.append(pages[n - 1][1][:5000])

    for n in spec[:2]:
        dump(n, "measure spec")
        dump(n + 1, "continued")
    for n in weight_tables:
        dump(n, "weight table text")
        tables = pdf.pages[n - 1].extract_tables()
        out.append(f"\n--- extract_tables() found {len(tables)} table(s) on page {n} ---")
        for t in tables:
            for row in t[:60]:
                out.append(" | ".join("" if c is None else str(c).replace("\n", " ") for c in row))

Path(f"pdf_peek_{year}.txt").write_text("\n".join(out))
print("\n".join(out[:3]))
print(f"\nFull output written to pdf_peek_{year}.txt")
