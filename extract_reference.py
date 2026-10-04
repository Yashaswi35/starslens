"""Phase 3a: deterministic extraction from the Technical Notes PDFs.

1. Attachment G weight tables (Tables G-1, G-2) -> ref_measure_weights   (ground truth)
2. Each measure's specification section       -> data/interim/sections_<year>.jsonl
3. Regex parse of the spec "Field: Value" lines -> ref_measure_specs_regex (baseline)

Run:
  python extract_reference.py
"""
import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import pdfplumber

RAW = Path("data/raw")
INTERIM = Path("data/interim")
DB = "starslens.duckdb"
CODE_CELL = re.compile(r"^([CD]\d{2})\*?$")
SPEC_HEADER = re.compile(r"^Measure:\s*([CD]\d{2})\s*[-\u2013\u2014:]?\s*(.*)$", re.M)
FOOTER = re.compile(r"\(Last Updated[^)]*\)\s*Page\s*\d+")
FIELDS = {
    "primary_data_source": r"Primary Data Source:\s*(.+)",
    "general_trend": r"General Trend:\s*(.+)",
    "statistical_method": r"Statistical Method:\s*(.+)",
    "improvement_measure": r"Improvement Measure:\s*(.+)",
    "cai_usage": r"CAI Usage:\s*(.+)",
    "case_mix_adjusted": r"Case-Mix Adjusted:\s*(.+)",
    "weighting_category": r"Weighting Category:\s*(.+)",
    "weighting_value": r"Weighting Value:\s*(.+)",
    "data_time_frame": r"Data Time Frame:\s*(.+)",
}


def is_toc(text):
    return text.count("....") > 5


CATEGORY_RE = (r"(Process Measure|Intermediate Outcome Measure|Outcome Measure|"
               r"Patients.? Experience and Complaints Measure|Measures Capturing Access|Improvement Measure)")
G_LINE = re.compile(r"^([CD]\d{2})\*?\s+(.+?)\s+" + CATEGORY_RE + r"\s+(\d+(?:\.\d+)?)\*?\s*$", re.M)


def weights_from_attachment_g(pdf, year):
    """Parse every page from the Attachment G heading up to the Attachment H heading.

    Tables can continue onto pages that don't repeat the 'Table G-1' title, so the whole
    page range is scanned. Rows come from extract_tables(), with a text-line regex as a
    fallback for rows the table extractor misses.
    """
    texts = [p.extract_text() or "" for p in pdf.pages]
    start = next((i for i, t in enumerate(texts) if not is_toc(t) and "Attachment G:" in t), None)
    if start is None:
        return []
    end = next((i for i, t in enumerate(texts) if i > start and not is_toc(t) and "Attachment H:" in t), start + 3)
    rows = {}
    for i in range(start, end + 1):
        for table in pdf.pages[i].extract_tables():
            for r in table:
                cells = [re.sub(r"\s+", " ", c or "").strip() for c in r]
                m = CODE_CELL.match(cells[0]) if cells else None
                weight = re.sub(r"[^\d.]", "", cells[-1]) if cells else ""
                if m and weight and m.group(1) not in rows:
                    rows[m.group(1)] = {"star_year": year, "measure_code": m.group(1), "measure_name": cells[1],
                                        "weighting_category": cells[2] if len(cells) > 3 else None,
                                        "weight": float(weight), "source": "table"}
        for m in G_LINE.finditer(texts[i]):
            if m.group(1) not in rows:
                rows[m.group(1)] = {"star_year": year, "measure_code": m.group(1), "measure_name": m.group(2),
                                    "weighting_category": m.group(3), "weight": float(m.group(4)),
                                    "source": "text"}
    return list(rows.values())


def spec_sections(pdf):
    text = "\n".join(FOOTER.sub("", p.extract_text() or "") for p in pdf.pages
                     if not is_toc(p.extract_text() or ""))
    heads = list(SPEC_HEADER.finditer(text))
    sections = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.start():end]
        # The last measure runs into the attachments, so cut at an attachment heading,
        # but only one AFTER the weighting lines: some specs reference an attachment
        # mid-section (SNP Care Management -> Attachment E, MTM -> Attachment N).
        w = re.search(r"Weighting Value", body)
        if w:
            a = re.search(r"\nAttachment [A-Z]:", body[w.end():])
            if a:
                body = body[: w.end() + a.start()]
        sections.append({"measure_code": h.group(1), "measure_name": h.group(2).strip(), "text": body})
    # keep the first full section per code (later mentions are cross-references)
    seen, out = set(), []
    for s in sections:
        if s["measure_code"] not in seen and re.search(r"Weighting\s+(Category|Value)", s["text"]):
            seen.add(s["measure_code"])
            out.append(s)
    return out


def regex_fields(text):
    out = {}
    for field, pat in FIELDS.items():
        m = re.search(pat, text)
        out[field] = m.group(1).strip() if m else None
    return out


def main():
    INTERIM.mkdir(parents=True, exist_ok=True)
    weights, specs = [], []
    for ydir in sorted(p for p in RAW.iterdir() if p.is_dir()):
        year = int(ydir.name)
        pdf_path = ydir / f"{year}_technical_notes.pdf"
        if not pdf_path.exists():
            continue
        with pdfplumber.open(pdf_path) as pdf:
            w = weights_from_attachment_g(pdf, year)
            secs = spec_sections(pdf)
        with open(INTERIM / f"sections_{year}.jsonl", "w") as f:
            for s in secs:
                f.write(json.dumps({"star_year": year, **s}) + "\n")
        for s in secs:
            specs.append({"star_year": year, "measure_code": s["measure_code"],
                          "spec_measure_name": s["measure_name"], "section_chars": len(s["text"]),
                          **regex_fields(s["text"])})
        weights += w
        print(f"{year}: {len(w)} weights from Attachment G, {len(secs)} spec sections")

    con = duckdb.connect(DB)
    for name, rows in [("ref_measure_weights", weights), ("ref_measure_specs_regex", specs)]:
        con.register("tmp", pd.DataFrame(rows))
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp")
        con.unregister("tmp")

    print("\n=== Coverage checks ===")
    print(con.sql("""
        WITH m AS (SELECT DISTINCT star_year, measure_code FROM stg_measure_stars)
        SELECT m.star_year,
               count(*)                                         AS measures_in_data,
               count(w.measure_code)                            AS with_weight,
               count(r.measure_code)                            AS with_spec_section,
               count(*) FILTER (WHERE r.weighting_value IS NULL) AS spec_missing_weight_line
        FROM m
        LEFT JOIN ref_measure_weights w USING (star_year, measure_code)
        LEFT JOIN ref_measure_specs_regex r USING (star_year, measure_code)
        GROUP BY 1 ORDER BY 1""").df().to_string(index=False))

    print("\n=== Measures with no weight or no spec section (diagnostics) ===")
    missing = con.sql("""
        WITH m AS (SELECT DISTINCT star_year, measure_code, measure_name FROM stg_measure_stars)
        SELECT m.star_year, m.measure_code, m.measure_name,
               w.measure_code IS NOT NULL AS has_weight, r.measure_code IS NOT NULL AS has_spec
        FROM m LEFT JOIN ref_measure_weights w USING (star_year, measure_code)
               LEFT JOIN ref_measure_specs_regex r USING (star_year, measure_code)
        WHERE w.measure_code IS NULL OR r.measure_code IS NULL ORDER BY 1, 2""").df()
    print(missing.to_string(index=False) if len(missing) else "none")
    for _, row in missing[~missing.has_spec].iterrows():
        pdf_path = RAW / str(row.star_year) / f"{row.star_year}_technical_notes.pdf"
        with pdfplumber.open(pdf_path) as pdf:
            for n, p in enumerate(pdf.pages, 1):
                t = p.extract_text() or ""
                if is_toc(t):
                    continue
                for line in t.splitlines():
                    if row.measure_code in line and "Measure" in line:
                        print(f"   {row.star_year} {row.measure_code} p{n}: {line[:110]}")
                        break

    print("\n=== Spec page weight vs Attachment G weight (internal consistency of the CMS document) ===")
    print(con.sql("""
        SELECT r.star_year, r.measure_code, r.weighting_value AS spec_page_says, w.weight AS attachment_g_says
        FROM ref_measure_specs_regex r JOIN ref_measure_weights w USING (star_year, measure_code)
        WHERE TRY_CAST(regexp_extract(r.weighting_value, '[0-9.]+') AS DOUBLE) IS DISTINCT FROM w.weight
        ORDER BY 1, 2""").df().to_string(index=False))

    print("\n=== Weights by category and year ===")
    print(con.sql("""
        SELECT weighting_category, star_year, min(weight) AS w_min, max(weight) AS w_max, count(*) AS measures
        FROM ref_measure_weights GROUP BY 1, 2 ORDER BY 1, 2""").df().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
