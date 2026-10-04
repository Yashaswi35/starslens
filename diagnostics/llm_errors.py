"""List every field an LLM got 'wrong', next to the model's answer and the reference value.

Run (after llm_extract.py):
  python llm_errors.py | tee llm_errors.txt
"""
import duckdb
import pandas as pd

con = duckdb.connect("starslens.duckdb")
df = con.sql("""
    SELECT s.model, s.star_year, s.measure_code,
           e.weighting_category AS llm_category,  w.weighting_category AS ref_category,
           s.weighting_category AS category_ok,
           e.improvement_measure AS llm_improvement, r.improvement_measure AS ref_improvement,
           s.improvement_measure AS improvement_ok,
           e.case_mix_adjusted AS llm_case_mix, r.case_mix_adjusted AS ref_case_mix,
           s.case_mix_adjusted AS case_mix_ok,
           e.general_trend AS llm_trend, r.general_trend AS ref_trend, s.general_trend AS trend_ok,
           e.cai_usage AS llm_cai, r.cai_usage AS ref_cai, s.cai_usage AS cai_ok
    FROM llm_scores s
    JOIN llm_extractions e USING (model, star_year, measure_code)
    LEFT JOIN ref_measure_weights w USING (star_year, measure_code)
    LEFT JOIN ref_measure_specs_regex r USING (star_year, measure_code)
""").df()
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)
for field, cols in [("weighting_category", ["llm_category", "ref_category"]),
                    ("improvement_measure", ["llm_improvement", "ref_improvement"]),
                    ("case_mix_adjusted", ["llm_case_mix", "ref_case_mix"]),
                    ("general_trend", ["llm_trend", "ref_trend"]),
                    ("cai_usage", ["llm_cai", "ref_cai"])]:
    ok = {"weighting_category": "category_ok", "improvement_measure": "improvement_ok",
          "case_mix_adjusted": "case_mix_ok", "general_trend": "trend_ok", "cai_usage": "cai_ok"}[field]
    miss = df[~df[ok].astype(bool)]
    print(f"\n=== {field}: {len(miss)} misses ===")
    if len(miss):
        print(miss[["model", "star_year", "measure_code"] + cols].sort_values(["star_year", "measure_code", "model"])
              .to_string(index=False))

print("\n=== Weighting category: spec page vs Attachment G (CMS internal consistency) ===")
print(con.sql("""
    SELECT r.star_year, r.measure_code, r.weighting_category AS spec_page_says,
           w.weighting_category AS attachment_g_says
    FROM ref_measure_specs_regex r JOIN ref_measure_weights w USING (star_year, measure_code)
    WHERE lower(regexp_replace(r.weighting_category, 's? measures?$', ''))
       <> lower(regexp_replace(w.weighting_category, 's? measures?$', ''))
    ORDER BY 1, 2""").df().to_string(index=False))

print("\n=== Sections where the regex baseline missed fields (raw text near 'Weighting') ===")
import json
from pathlib import Path
missing = con.sql("""SELECT star_year, measure_code FROM ref_measure_specs_regex
                     WHERE improvement_measure IS NULL OR case_mix_adjusted IS NULL
                        OR weighting_category IS NULL""").fetchall()
for year, code in missing:
    for line in Path(f"data/interim/sections_{year}.jsonl").read_text().splitlines():
        sec = json.loads(line)
        if sec["measure_code"] == code:
            i = sec["text"].find("Statistical Method")
            print(f"\n--- {year} {code} ---")
            print(sec["text"][max(i - 200, 0): i + 700])
con.close()
