"""Phase 3b: extract measure specifications with local LLMs and score them against ground truth.

Each model reads every measure's specification section and returns JSON matching MeasureSpec.
Scoring:
  - weighting_value and weighting_category  vs Attachment G (ref_measure_weights)
  - measure_id                              vs the section header
  - general_trend, improvement_measure,
    cai_usage, case_mix_adjusted            vs the regex baseline (ref_measure_specs_regex)

Results are cached in data/interim/llm_<model>_<year>.jsonl, so reruns only process new sections.

Run:
  python llm_extract.py --limit 3                      # quick smoke test
  python llm_extract.py                                # all years, both models
  python llm_extract.py --models llama3.2 --years 2026
"""
import argparse
import json
import re
import time
from pathlib import Path

import duckdb
import ollama
import pandas as pd
from pydantic import BaseModel, Field

INTERIM = Path("data/interim")
DB = "starslens.duckdb"


class MeasureSpec(BaseModel):
    measure_id: str = Field(description="Measure ID such as C01 or D08")
    general_trend: str = Field(description="'Higher is better' or 'Lower is better'")
    improvement_measure: bool = Field(description="True if the Improvement Measure field says Included")
    cai_usage: bool = Field(description="True if the CAI Usage field says Included")
    case_mix_adjusted: bool = Field(description="True if the Case-Mix Adjusted field says Yes")
    weighting_category: str = Field(description="Exact text of the Weighting Category field")
    weighting_value: float = Field(description="Number in the Weighting Value field")


PROMPT = """You are extracting fields from a CMS Medicare Star Ratings measure specification.
Use only what the text states. Read the labeled fields near the end of the text:
General Trend, Improvement Measure, CAI Usage, Case-Mix Adjusted, Weighting Category, Weighting Value.

Specification text:
<<<
{text}
>>>"""


def norm_cat(s):
    s = s if isinstance(s, str) else ""
    s = s.lower().replace("\u2019", "'")
    s = re.sub(r"\bmeasures?\b", "", s)
    s = re.sub(r"\boutcomes\b", "outcome", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def yes(s):
    return str(s).strip().lower().startswith(("included", "yes")) if isinstance(s, str) else None


def run_model(model, sections, cache_path):
    done = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            r = json.loads(line)
            done[r["measure_code"]] = r
    with open(cache_path, "a") as f:
        for s in sections:
            if s["measure_code"] in done:
                continue
            t0 = time.time()
            try:
                resp = ollama.chat(model=model,
                                   messages=[{"role": "user", "content": PROMPT.format(text=s["text"][:20000])}],
                                   format=MeasureSpec.model_json_schema(),
                                   options={"temperature": 0, "num_ctx": 8192})
                parsed = MeasureSpec.model_validate_json(resp["message"]["content"]).model_dump()
                error = None
            except Exception as e:  # invalid JSON or schema violation counts as a failed extraction
                parsed, error = {}, f"{type(e).__name__}: {e}"[:300]
            rec = {"star_year": s["star_year"], "measure_code": s["measure_code"], "model": model,
                   "latency_s": round(time.time() - t0, 2), "error": error, **parsed}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            done[s["measure_code"]] = rec
            print(f"  {model} {s['star_year']} {s['measure_code']}: {rec['latency_s']}s"
                  + (f"  ERROR {error[:60]}" if error else ""), flush=True)
    return list(done.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["llama3.2", "gemma3:4b"])
    ap.add_argument("--years", nargs="+", type=int)
    ap.add_argument("--limit", type=int, help="sections per year, for quick tests")
    args = ap.parse_args()

    results = []
    for path in sorted(INTERIM.glob("sections_*.jsonl")):
        year = int(path.stem.split("_")[1])
        if args.years and year not in args.years:
            continue
        sections = [json.loads(l) for l in path.read_text().splitlines()][: args.limit]
        for model in args.models:
            print(f"{model} on {year}: {len(sections)} sections")
            cache = INTERIM / f"llm_{model.replace(':', '-')}_{year}.jsonl"
            results += run_model(model, sections, cache)

    df = pd.DataFrame(results)
    con = duckdb.connect(DB)
    truth = con.sql("""
        SELECT w.star_year, w.measure_code, w.weight AS truth_weight,
               COALESCE(r.weighting_category, w.weighting_category) AS truth_category,
               r.general_trend AS rx_trend, r.improvement_measure AS rx_impr, r.cai_usage AS rx_cai,
               r.case_mix_adjusted AS rx_cma
        FROM ref_measure_weights w LEFT JOIN ref_measure_specs_regex r USING (star_year, measure_code)""").df()
    m = df.merge(truth, on=["star_year", "measure_code"], how="left")
    ok = m["error"].isna()

    def col(name):
        return m[name] if name in m.columns else pd.Series([None] * len(m), index=m.index, dtype=object)

    def text(series):
        return series.map(lambda v: v if isinstance(v, str) else "")

    def score(correct, reference):
        """1.0 / 0.0 per row; NaN where there is no reference value to score against."""
        out = (correct.fillna(False).astype(bool) & ok).astype(float)
        return out.where(reference.notna())

    scored = pd.DataFrame({
        "star_year": m.star_year, "measure_code": m.measure_code, "model": m.model, "latency_s": m.latency_s,
        "valid_json": ok.astype(float),
        "measure_id": score(text(col("measure_id")).str.upper().str.replace("*", "", regex=False) == m.measure_code,
                            m.measure_code),
        "weighting_value": score(pd.to_numeric(col("weighting_value"), errors="coerce") == m.truth_weight,
                                 m.truth_weight),
        "weighting_category": score(col("weighting_category").map(norm_cat) == m.truth_category.map(norm_cat),
                                    m.truth_category),
        "general_trend": score(text(col("general_trend")).str.lower().str.contains("higher")
                               == text(m.rx_trend).str.lower().str.contains("higher"), m.rx_trend),
        "improvement_measure": score(col("improvement_measure") == m.rx_impr.map(yes), m.rx_impr),
        "cai_usage": score(col("cai_usage") == m.rx_cai.map(yes), m.rx_cai),
        "case_mix_adjusted": score(col("case_mix_adjusted") == m.rx_cma.map(yes), m.rx_cma),
    })
    con.register("tmp_raw", m.drop(columns=[c for c in m.columns if c.startswith(("rx_", "truth_"))]))
    con.execute("CREATE OR REPLACE TABLE llm_extractions AS SELECT * FROM tmp_raw")
    con.register("tmp_scored", scored)
    con.execute("CREATE OR REPLACE TABLE llm_scores AS SELECT * FROM tmp_scored")

    fields = ["valid_json", "measure_id", "weighting_value", "weighting_category",
              "general_trend", "improvement_measure", "cai_usage", "case_mix_adjusted"]
    summary = scored.groupby("model")[fields].mean().mul(100).round(1)          # NaN (no reference) skipped
    all_ok = scored[fields].fillna(1.0).eq(1.0).all(axis=1)                     # every SCORED field correct
    summary["all_fields_correct_%"] = (all_ok.groupby(scored.model).mean() * 100).round(1)
    summary["fields_without_reference"] = scored[fields].isna().sum(axis=1).groupby(scored.model).sum()
    summary["median_latency_s"] = scored.groupby("model").latency_s.median()
    summary["sections"] = scored.groupby("model").size()
    pd.set_option("display.width", 200)
    print("\n=== Field accuracy by model (%) ===")
    print(summary.T.to_string())
    print("\n=== Weight errors (model said vs Attachment G) ===")
    wrong = m[ok & (pd.to_numeric(col("weighting_value"), errors="coerce") != m.truth_weight)]
    print(wrong[["model", "star_year", "measure_code", "weighting_value", "truth_weight"]].to_string(index=False)
          if len(wrong) else "none")
    summary.to_csv("output_llm_accuracy.csv")
    con.close()


if __name__ == "__main__":
    main()
