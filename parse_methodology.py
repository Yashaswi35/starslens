"""Phase 4 prep: parse the rating-methodology parameters out of the Technical Notes text dumps.

Inputs : methodology_<year>.txt (from methodology_peek.py), methodology_2026_extra.txt (methodology_peek2.py)
Outputs: DuckDB tables
  ref_reward_thresholds  star_year, improvement, new_measures, stat (mean|variance), percentile, rating_type, value
  ref_cai_values         star_year, rating_type, fac, cai_value

Run:
  python parse_methodology.py
"""
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

DB = "starslens.duckdb"
RATING_TYPES = ["part_c", "part_d_mapd", "part_d_pdp", "overall"]
CAI_TABLES = {
    "overall": r"CAI Values for the Overall Rating",
    "part_c": r"CAI Values for the Part C Summary",
    "part_d_mapd": r"CAI Values for the MA-PD Part D Summary",
    "part_d_pdp": r"CAI Values for the PDP Part D Summary",
}
NUM = r"(-?\d+\.\d+)"
THRESH_LINE = re.compile(
    r"^(With|Without)\s+(?:(With|Without)\s+)?(\d{2})th\s+" + r"\s+".join([NUM] * 4) + r"\s*$", re.M)
CAI_VALUE = re.compile(r"(-?0\.\d[\d ]{4,8}\d)")


def text_only(raw):
    """Drop the extract_tables() dumps so each value is read once, from the page text."""
    keep, skipping = [], False
    for line in raw.splitlines():
        if line.startswith("--- table ---"):
            skipping = True
            continue
        if line.startswith("====="):
            skipping = False
        if not skipping and not line.startswith("=====") and not line.startswith("(Last Updated"):
            keep.append(line)
    return "\n".join(keep)


def thresholds(text, year):
    rows = []
    for title, stat in [("Table 8: Performance Summary Thresholds", "mean"), ("Table 9: Variance Thresholds", "variance")]:
        i = text.find(title)
        if i < 0:
            continue
        block = text[i: i + 1500]
        nxt = re.search(r"\n(Table \d+:|Categorical Adjustment Index)", block[len(title):])
        block = block[: len(title) + nxt.start()] if nxt else block
        for m in THRESH_LINE.finditer(block):
            impr, new, pct = m.group(1), m.group(2) or "With", int(m.group(3))
            for rt, v in zip(RATING_TYPES, m.groups()[3:]):
                rows.append({"star_year": year, "improvement": impr, "new_measures": new, "stat": stat,
                             "percentile": pct, "rating_type": rt, "value": float(v)})
    return rows


def cai_values(text, year):
    rows = []
    for rt, title in CAI_TABLES.items():
        m = re.search(r"Table \d+: Final Adjustment Categories and " + title, text)
        if not m:
            continue
        block = text[m.end():]
        stop = re.search(r"\n(Table \d+:|Tables \d+|Calculation Precision)", block)
        block = block[: stop.start()] if stop else block
        for fac, v in enumerate(CAI_VALUE.findall(block), 1):
            rows.append({"star_year": year, "rating_type": rt, "fac": fac,
                         "cai_value": float(v.replace(" ", ""))})
    return rows


def main(folder="."):
    thr, cai = [], []
    for path in sorted(Path(folder).glob("methodology_20[0-9][0-9].txt")):
        year = int(path.stem.split("_")[1])
        text = text_only(path.read_text())
        extra = Path(folder) / f"methodology_{year}_extra.txt"
        if extra.exists():
            text += "\n" + text_only(extra.read_text())
        thr += thresholds(text, year)
        cai += cai_values(text, year)
    thr, cai = pd.DataFrame(thr).drop_duplicates(), pd.DataFrame(cai)

    con = duckdb.connect(DB)
    for name, df in [("ref_reward_thresholds", thr), ("ref_cai_values", cai)]:
        con.register("tmp", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp")
        con.unregister("tmp")
    pd.set_option("display.width", 200)
    print("=== Reward factor thresholds parsed (rows per year, stat) ===")
    print(thr.groupby(["star_year", "stat"]).size().unstack().to_string())
    print("\n=== CAI categories parsed per year and rating type ===")
    print(cai.pivot_table(index="star_year", columns="rating_type", values="fac", aggfunc="max").to_string())
    try:
        print("\n=== Check: highest FAC used in the data vs categories parsed ===")
        print(con.sql("""
            WITH used AS (
              SELECT star_year, 'part_c' AS rating_type, max(part_c_fac) AS max_fac FROM stg_cai GROUP BY 1
              UNION ALL SELECT star_year, 'part_d_mapd', max(part_d_mapd_fac) FROM stg_cai GROUP BY 1
              UNION ALL SELECT star_year, 'part_d_pdp', max(part_d_pdp_fac) FROM stg_cai GROUP BY 1
              UNION ALL SELECT star_year, 'overall', max(overall_fac) FROM stg_cai GROUP BY 1)
            SELECT u.star_year, u.rating_type, u.max_fac AS max_fac_in_data, max(c.fac) AS categories_parsed,
                   CASE WHEN u.max_fac <= max(c.fac) THEN 'ok' ELSE 'MISSING' END AS status
            FROM used u LEFT JOIN ref_cai_values c USING (star_year, rating_type)
            GROUP BY 1, 2, 3 ORDER BY 1, 2""").df().to_string(index=False))
    except duckdb.CatalogException:
        print("(stg_cai not built yet, skipping)")
    con.close()


if __name__ == "__main__":
    main(*sys.argv[1:])
