"""Phase 2a: parse the CMS Report Card Master Tables (one Excel per year) into DuckDB.

CMS sheets are wide and messy: a title row, a domain row, a measure-code row
("C01: Breast Cancer Screening"), a measurement-period row, then one row per contract.
This script finds those rows by pattern, reshapes each sheet to a tidy long table,
and loads raw_* tables into starslens.duckdb. Typing and cleaning happen in SQL (sql/).

Run:
  python load.py
"""
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

RAW = Path("data/raw")
DB = "starslens.duckdb"
CONTRACT_RE = re.compile(r"^[A-Z]\d{4}$")
CODE_RE = re.compile(r"^([CD]\d{2})\s*:\s*(.+)$")
DOMAIN_RE = re.compile(r"^([HD]D\d)\s*:\s*(.+)$")


def clean(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).replace("\u2013", "-").replace("\u2019", "'").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def read_sheet(path, name):
    df = pd.read_excel(path, sheet_name=name, header=None, dtype=str)
    return df.map(clean)


def find_row(df, predicate, what, sheet):
    for i in range(min(len(df), 30)):
        if predicate(df.iloc[i]):
            return i
    print(f"\n!! Could not find the {what} row in sheet '{sheet}'. First rows:")
    print(df.head(8).to_string(max_colwidth=40))
    sys.exit(1)


def code_row(df, sheet):
    return find_row(df, lambda r: sum(bool(CODE_RE.match(str(v or ""))) for v in r) >= 1,
                    "measure-code", sheet)


def measure_long(df, year, sheet, value_name):
    """Measure_Stars / Measure_Data -> one row per contract x measure."""
    rc = code_row(df, sheet)
    domains = df.iloc[rc - 1].ffill()
    periods = df.iloc[rc + 1]
    hdr = df.iloc[rc - 1]
    first = find_row(df, lambda r: bool(CONTRACT_RE.match(str(r.iloc[0] or ""))), "first contract", sheet)
    data = df.iloc[first:]
    data = data[data.iloc[:, 0].fillna("").str.match(CONTRACT_RE)]
    out = []
    for j, cell in enumerate(df.iloc[rc]):
        m = CODE_RE.match(str(cell or ""))
        if not m:
            continue
        dm = DOMAIN_RE.match(str(domains.iloc[j] or ""))
        out.append(pd.DataFrame({
            "star_year": year,
            "contract_id": data.iloc[:, 0].values,
            "measure_code": m.group(1),
            "measure_name": m.group(2),
            "domain_code": dm.group(1) if dm else None,
            "domain_name": dm.group(2) if dm else None,
            "measurement_period": periods.iloc[j],
            value_name: data.iloc[:, j].values,
        }))
    contracts = data.iloc[:, :5].copy()
    contracts.columns = ["contract_id", "org_type", "contract_name", "marketing_name", "parent_org"]
    contracts.insert(0, "star_year", year)
    return pd.concat(out, ignore_index=True), contracts


def header_table(df, year, sheet, first_col_label="Contract Number"):
    """Simple sheets with one header row (Summary_Rating, CAI, Domain_Stars)."""
    h = find_row(df, lambda r: str(r.iloc[0] or "").upper() in (first_col_label.upper(), "CONTRACT_ID"),
                 "header", sheet)
    cols = [c or f"col_{i}" for i, c in enumerate(df.iloc[h])]
    data = df.iloc[h + 1:].copy()
    data.columns = cols
    data = data[data.iloc[:, 0].fillna("").str.match(CONTRACT_RE)]
    data = data.loc[:, ~data.columns.str.startswith("col_")]
    data.insert(0, "star_year", year)
    return data


def summary_long(df, year, sheet):
    t = header_table(df, year, sheet)
    t = t.rename(columns={t.columns[1]: "contract_id"})
    id_cols = ["star_year", "contract_id"]
    long = t.melt(id_vars=id_cols, value_vars=[c for c in t.columns[2:]], var_name="field", value_name="value")
    return long


def cutpoints_long(df, year, part, sheet):
    rc = code_row(df, sheet)
    domains = df.iloc[rc - 1].ffill()
    rows = df.iloc[rc + 2:]
    star_col = 1 if part == "D" else 0
    rows = rows[rows.iloc[:, star_col].fillna("").str.contains("star", case=False)]
    out = []
    for _, r in rows.iterrows():
        org_type = df.iloc[rc + 2:].iloc[:, 0].ffill().loc[r.name] if part == "D" else "All"
        for j, cell in enumerate(df.iloc[rc]):
            m = CODE_RE.match(str(cell or ""))
            if m and r.iloc[j]:
                dm = DOMAIN_RE.match(str(domains.iloc[j] or ""))
                out.append({"star_year": year, "part": part, "org_type": org_type,
                            "star_label": r.iloc[star_col], "measure_code": m.group(1),
                            "domain_code": dm.group(1) if dm else None, "threshold_text": r.iloc[j]})
    return pd.DataFrame(out)


def main():
    stars, data, contracts, summary, cai, domains, cuts = [], [], [], [], [], [], []
    for ydir in sorted(p for p in RAW.iterdir() if p.is_dir()):
        year = int(ydir.name)
        files = sorted(ydir.rglob("*Report_Card_Master_Table*.xlsx"))
        if not files:
            print(f"{year}: no master table found, skipping")
            continue
        path = files[-1]
        xl = pd.ExcelFile(path)
        print(f"{year}: {path.name}")
        s, c = measure_long(read_sheet(path, "Measure_Stars"), year, "Measure_Stars", "star_value")
        d, _ = measure_long(read_sheet(path, "Measure_Data"), year, "Measure_Data", "score_value")
        stars.append(s); data.append(d); contracts.append(c)
        summary.append(summary_long(read_sheet(path, "Summary_Rating"), year, "Summary_Rating"))
        cai.append(summary_long(read_sheet(path, "CAI"), year, "CAI"))
        domains.append(summary_long(read_sheet(path, "Domain_Stars"), year, "Domain_Stars"))
        for sheet in xl.sheet_names:
            if "cut" in sheet.lower():
                part = "C" if "_C_" in sheet or "Part C" in sheet else "D"
                cuts.append(cutpoints_long(read_sheet(path, sheet), year, part, sheet))
        print(f"   measure stars rows: {len(s):,}   contracts: {len(c):,}")

    con = duckdb.connect(DB)
    for name, frames in [("raw_measure_stars", stars), ("raw_measure_data", data),
                         ("raw_contracts", contracts), ("raw_summary", summary), ("raw_cai", cai),
                         ("raw_domain_stars", domains), ("raw_cut_points", cuts)]:
        df = pd.concat(frames, ignore_index=True)
        con.register("tmp", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp")
        con.unregister("tmp")
        print(f"[{name}] {len(df):,} rows")

    print("\nSample measures by year:")
    print(con.sql("""SELECT star_year, COUNT(DISTINCT measure_code) AS measures,
                            COUNT(DISTINCT contract_id) AS contracts
                     FROM raw_measure_stars GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    print("\nMost common non-numeric star values:")
    print(con.sql("""SELECT star_value, COUNT(*) AS n FROM raw_measure_stars
                     WHERE TRY_CAST(star_value AS INTEGER) IS NULL
                     GROUP BY 1 ORDER BY n DESC LIMIT 8""").df().to_string(index=False))
    print("\nSummary fields:")
    print(con.sql("""SELECT star_year, field, COUNT(*) AS n FROM raw_summary
                     GROUP BY 1, 2 ORDER BY 1, 2""").df().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
