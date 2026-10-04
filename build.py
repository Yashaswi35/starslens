"""Phase 2b: build DuckDB staging and mart tables from raw_*, then run validation checks.

Run:
  python build.py
Exits non-zero if any ERROR check fails.
"""
import sys
from pathlib import Path

import duckdb

DB = "starslens.duckdb"

CHECKS = [
    ("ERROR", "measure stars unique per year x contract x measure", """
        SELECT count(*) FROM (SELECT star_year, contract_id, measure_code FROM stg_measure_stars
                              GROUP BY 1, 2, 3 HAVING count(*) > 1)"""),
    ("ERROR", "star values within 1 to 5", """
        SELECT count(*) FROM stg_measure_stars WHERE stars NOT BETWEEN 1 AND 5"""),
    ("ERROR", "every measure row has stars or a not-rated reason", """
        SELECT count(*) FROM stg_measure_stars WHERE stars IS NULL AND not_rated_reason IS NULL"""),
    ("ERROR", "summary ratings are half-star values from 1 to 5", """
        SELECT count(*) FROM stg_summary
        WHERE (part_c_summary IS NOT NULL AND (part_c_summary NOT BETWEEN 1 AND 5 OR part_c_summary * 2 <> round(part_c_summary * 2)))
           OR (part_d_summary IS NOT NULL AND (part_d_summary NOT BETWEEN 1 AND 5 OR part_d_summary * 2 <> round(part_d_summary * 2)))
           OR (overall_rating IS NOT NULL AND (overall_rating NOT BETWEEN 1 AND 5 OR overall_rating * 2 <> round(overall_rating * 2)))"""),
    ("ERROR", "every contract with measure stars has a summary row", """
        SELECT count(*) FROM (SELECT DISTINCT star_year, contract_id FROM stg_measure_stars) m
        ANTI JOIN stg_summary s USING (star_year, contract_id)"""),
    ("ERROR", "every measure code maps to exactly one measure name per year", """
        SELECT count(*) FROM (SELECT star_year, measure_code FROM stg_measure_stars
                              GROUP BY 1, 2 HAVING count(DISTINCT measure_name) > 1)"""),
    ("WARN", "measure scores that are present but not numeric", """
        SELECT count(*) FROM stg_measure_scores m JOIN stg_measure_stars s USING (star_year, contract_id, measure_code)
        WHERE s.stars IS NOT NULL AND m.score IS NULL"""),
    ("WARN", "rated contracts with no CAI category", """
        SELECT count(*) FROM fct_contract_year WHERE star_rating_for_bonus IS NOT NULL
        AND part_c_fac IS NULL AND part_d_mapd_fac IS NULL AND part_d_pdp_fac IS NULL"""),
]


def main():
    con = duckdb.connect(DB)
    for f in sorted(Path("sql").glob("0*.sql")):
        con.execute(f.read_text())
        table = f.stem[3:]
        print(f"[{table}] {con.sql(f'SELECT count(*) FROM {table}').fetchone()[0]:,} rows")

    print("\n=== Validation ===")
    errors = 0
    for level, name, sql in CHECKS:
        bad = con.sql(sql).fetchone()[0] or 0
        status = "PASS" if bad == 0 else ("FAIL" if level == "ERROR" else "FLAG")
        errors += status == "FAIL"
        print(f"{status:4} [{level}] {name}: {bad}")

    print("\n=== Rating distribution (contracts with a rating used for bonus) ===")
    print(con.sql("""
        SELECT star_year,
               count(*) FILTER (WHERE star_rating_for_bonus IS NOT NULL)            AS rated_contracts,
               round(avg(star_rating_for_bonus), 2)                                  AS avg_rating,
               round(100.0 * avg(CASE WHEN meets_4_star THEN 1 ELSE 0 END)
                     FILTER (WHERE star_rating_for_bonus IS NOT NULL), 1)            AS pct_4_plus
        FROM fct_contract_year WHERE contract_family = 'MA'
        GROUP BY 1 ORDER BY 1""").df().to_string(index=False))

    print("\n=== Measures that changed between years (by name) ===")
    print(con.sql("""
        WITH m AS (SELECT DISTINCT star_year, measure_key, measure_code FROM stg_measure_stars)
        SELECT measure_key,
               max(CASE WHEN star_year = 2024 THEN measure_code END) AS code_2024,
               max(CASE WHEN star_year = 2025 THEN measure_code END) AS code_2025,
               max(CASE WHEN star_year = 2026 THEN measure_code END) AS code_2026
        FROM m GROUP BY 1
        HAVING count(DISTINCT star_year) < 3 OR count(DISTINCT measure_code) > 1
        ORDER BY 1""").df().to_string(index=False))
    print("\n=== Examples of non-numeric scores on rated measures ===")
    print(con.sql("""
        SELECT s.measure_code, s.measure_name, m.score_text, count(*) AS n
        FROM stg_measure_scores m JOIN stg_measure_stars s USING (star_year, contract_id, measure_code)
        WHERE s.stars IS NOT NULL AND m.score IS NULL
        GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 12""").df().to_string(index=False))
    con.close()
    if errors:
        sys.exit(f"{errors} ERROR check(s) failed")


if __name__ == "__main__":
    main()
