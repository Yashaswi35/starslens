"""Phase 4a: replicate CMS Part C, Part D, and overall Star Ratings from measure stars.

Follows the Technical Notes, one step at a time, and reports the match rate after each:
  S1  weighted mean of measure stars (Attachment G weights), improvement measures included
  S2  + reward factor (CMS's published mean and variance percentile thresholds)
  S3  + CAI (Categorical Adjustment Index) for the contract's final adjustment category
  S4  + improvement hold-harmless (contracts at 4+ stars without improvement keep the higher rating)
  S5  + new-measure hold-harmless (2026 only, contracts with 25%+ of members in disaster areas)
Ratings are rounded to the nearest half star with CMS's rule (3.75 rounds up to 4.0).

Run:
  python replicate.py | tee replicate_log.txt
"""
import math

import duckdb
import numpy as np
import pandas as pd

DB = "starslens.duckdb"
EPS = 1e-9
OVERALL_DUPLICATES = {"complaints about the drug plan", "members choosing to leave the plan"}  # Part D copies
NEW_MEASURES = {2026: {"C04", "C05", "C13"}}       # new in 2026 per the Technical Notes
NEW_HOS, NEW_HEDIS = {"C04", "C05"}, {"C13"}


def cms_round(x):
    """Nearest half star; exact halves round up (3.75 -> 4.0, 3.749999 -> 3.5)."""
    return np.nan if pd.isna(x) else min(math.floor(x * 2 + 0.5 + EPS) / 2, 5.0)


def load(con):
    stars = con.sql("""
        SELECT s.star_year, s.contract_id, s.measure_code, s.part, lower(s.measure_name) AS measure_name, s.stars,
               w.weight, w.weighting_category
        FROM stg_measure_stars s
        JOIN ref_measure_weights w USING (star_year, measure_code)
        WHERE s.stars IS NOT NULL""").df()
    contracts = con.sql("""
        SELECT d.star_year, d.contract_id, d.org_type,
               s.part_c_summary, s.part_d_summary, s.overall_rating,
               c.part_c_fac, c.part_d_mapd_fac, c.part_d_pdp_fac, c.overall_fac,
               coalesce(c.puerto_rico_only, 'No') AS puerto_rico_only
        FROM dim_contract d
        LEFT JOIN stg_summary s USING (star_year, contract_id)
        LEFT JOIN stg_cai c USING (star_year, contract_id)""").df()
    disaster = con.sql("""
        SELECT star_year, trim(contract_id) AS contract_id,
               TRY_CAST(regexp_extract(field, '(20[0-9]{2})', 1) AS INTEGER) AS disaster_year,
               TRY_CAST(replace(value, '%', '') AS DOUBLE) AS pct
        FROM raw_summary WHERE field LIKE '%Disaster%'""").df()
    thr = con.sql("SELECT * FROM ref_reward_thresholds").df()
    cai = con.sql("SELECT * FROM ref_cai_values").df()
    return stars, contracts, disaster, thr, cai


def classify(row, has_d):
    is_pdp = row.contract_id.startswith("S") or "PDP" in str(row.org_type or "").upper()
    if is_pdp:
        return "PDP"
    return "MA-PD" if has_d else "MA-Only"


def threshold_lookup(thr):
    t = {}
    for r in thr.itertuples():
        t[(r.star_year, r.improvement, r.new_measures, r.stat, r.percentile, r.rating_type)] = r.value
    return t


def reward_factor(mean, var, year, impr, new, rtype, t):
    key = lambda stat, p: t.get((year, impr, new, stat, p, rtype),
                                t.get((year, impr, "With", stat, p, rtype)))  # 2025 has no new-measure split
    p65, p85, v30, v70 = key("mean", 65), key("mean", 85), key("variance", 30), key("variance", 70)
    if None in (p65, p85, v30, v70) or pd.isna(var):
        return 0.0
    high, rel_high = mean >= p85 - EPS, p65 - EPS <= mean < p85 - EPS
    low, medium = var < v30 - EPS, v30 - EPS <= var < v70 - EPS
    if high and low:
        return 0.4
    if high and medium:
        return 0.3
    if rel_high and low:
        return 0.2
    if rel_high and medium:
        return 0.1
    return 0.0


def weighted_stats(df):
    w, s = df.weight.to_numpy(float), df.stars.to_numpy(float)
    keep = w > 0
    w, s = w[keep], s[keep]
    n, W = len(s), w.sum()
    if n == 0 or W == 0:
        return np.nan, np.nan, 0
    mean = (w * s).sum() / W
    var = (n / (n - 1)) * (w * (s - mean) ** 2).sum() / W if n > 1 else np.nan
    return mean, var, n


def main():
    con = duckdb.connect(DB)
    stars, contracts, disaster, thr, cai = load(con)
    t = threshold_lookup(thr)
    cai_map = {(r.star_year, r.rating_type, r.fac): r.cai_value for r in cai.itertuples()}
    dis = disaster.pivot_table(index=["star_year", "contract_id"], columns="disaster_year", values="pct", aggfunc="max")

    rows = []
    for (year, cid), g in stars.groupby(["star_year", "contract_id"]):
        c = contracts[(contracts.star_year == year) & (contracts.contract_id == cid)]
        if c.empty:
            continue
        c = c.iloc[0]
        has_d = (g.part == "D").any()
        ctype = classify(c, has_d)
        if c.puerto_rico_only == "Yes":                     # adherence weights are zero for PR-only contracts
            g = g.assign(weight=np.where(g.measure_code.isin(["D08", "D09", "D10"]), 0.0, g.weight))

        plans = []
        if ctype in ("MA-PD", "MA-Only"):
            plans.append(("part_c", "part_c", g[g.part == "C"], c.part_c_summary, c.part_c_fac))
        if ctype == "MA-PD":
            plans.append(("part_d", "part_d_mapd", g[g.part == "D"], c.part_d_summary, c.part_d_mapd_fac))
            overall = g[~((g.part == "D") & g.measure_name.isin(OVERALL_DUPLICATES))]
            plans.append(("overall", "overall", overall, c.overall_rating, c.overall_fac))
        if ctype == "PDP":
            plans.append(("part_d", "part_d_pdp", g[g.part == "D"], c.part_d_summary, c.part_d_pdp_fac))

        new_codes = NEW_MEASURES.get(year, set())
        yrs = dis.loc[(year, cid)] if (year, cid) in dis.index else pd.Series(dtype=float)
        d_hos = yrs.get(year - 3, 0) or 0          # 2026 ratings: HOS disasters in 2023, HEDIS in 2024
        d_hedis = yrs.get(year - 2, 0) or 0

        for rating, rtype, m, official, fac in plans:
            if pd.isna(official):
                continue
            cai_v = cai_map.get((year, rtype, int(fac)), 0.0) if pd.notna(fac) else 0.0
            res = {}
            for impr in ("With", "Without"):
                for new in ("With", "Without"):
                    sub = m
                    if impr == "Without":
                        sub = sub[sub.weighting_category != "Improvement Measure"]
                    if new == "Without":
                        sub = sub[~sub.measure_code.isin(new_codes)]
                    mean, var, n = weighted_stats(sub)
                    rf = reward_factor(mean, var, year, impr, new, rtype, t)
                    res[(impr, new)] = dict(mean=mean, var=var, n=n, rf=rf,
                                            s1=cms_round(mean), s2=cms_round(mean + rf),
                                            s3=cms_round(mean + rf + cai_v), raw=mean + rf + cai_v)

            def impr_rule(new):
                w_, wo = res[("With", new)]["s3"], res[("Without", new)]["s3"]
                return wo if (wo >= 4 and w_ < wo) else w_

            # Hold-harmless rules apply only to the contract's highest-level rating:
            # overall for MA-PD, Part C summary for MA-Only, Part D summary for PDP.
            top_level = {"MA-PD": "overall", "MA-Only": "part_c", "PDP": "part_d"}[ctype] == rating
            s4 = impr_rule("With") if top_level else res[("With", "With")]["s3"]
            has_hos = m.measure_code.isin(new_codes & NEW_HOS).any()
            has_hedis = m.measure_code.isin(new_codes & NEW_HEDIS).any()
            eligible = bool(new_codes) and (
                (has_hos and has_hedis and (d_hos >= 25 or d_hedis >= 25)) or
                (has_hedis and not has_hos and d_hedis >= 25) or
                (has_hos and not has_hedis and d_hos >= 25))
            alt_new = impr_rule("Without") if top_level else res[("With", "Without")]["s3"]
            s5 = max(s4, alt_new) if eligible else s4
            base = res[("With", "With")]
            rows.append(dict(star_year=year, contract_id=cid, contract_type=ctype, rating=rating,
                             official=official, n_measures=base["n"], mean=base["mean"], variance=base["var"],
                             reward_factor=base["rf"], cai=cai_v, raw_final=base["raw"],
                             s1=base["s1"], s2=base["s2"], s3=base["s3"], s4=s4, s5=s5,
                             new_measure_rule_eligible=eligible, top_level=top_level,
                             alt_with_impr_with_new=res[("With", "With")]["s3"],
                             alt_without_impr_with_new=res[("Without", "With")]["s3"],
                             alt_with_impr_without_new=res[("With", "Without")]["s3"],
                             alt_without_impr_without_new=res[("Without", "Without")]["s3"],
                             alt_no_reward_factor=cms_round(base["mean"] + cai_v)))

    out = pd.DataFrame(rows)
    con.register("tmp", out)
    con.execute("CREATE OR REPLACE TABLE rep_ratings AS SELECT * FROM tmp")
    con.unregister("tmp")

    stages = ["s1", "s2", "s3", "s4", "s5"]
    labels = {"s1": "S1 weighted mean", "s2": "S2 + reward factor", "s3": "S3 + CAI",
              "s4": "S4 + improvement rule", "s5": "S5 + new-measure rule"}
    pd.set_option("display.width", 220)
    print("=== Exact match rate vs CMS official rating (%) ===")
    summ = out.groupby(["star_year", "rating"]).apply(
        lambda d: pd.Series({"contracts": len(d), **{labels[s]: round(100 * (d[s] == d.official).mean(), 1)
                                                      for s in stages}}), include_groups=False)
    print(summ.to_string())
    print("\nAll years and ratings combined:")
    print({labels[s]: round(100 * (out[s] == out.official).mean(), 1) for s in stages}, "contracts:", len(out))

    print("\n=== Final stage: replicated minus official (count of ratings) ===")
    out["diff"] = out.s5 - out.official
    print(out.groupby(["star_year", "rating"])["diff"].value_counts().unstack(fill_value=0).to_string())

    print("\n=== Misses near a rounding boundary (raw score within 0.05 of x.25 or x.75) ===")
    miss = out[out.s5 != out.official].copy()
    miss["dist_to_boundary"] = ((miss.raw_final - 0.25) % 0.5).map(lambda v: min(v, 0.5 - v))
    print(f"{(miss.dist_to_boundary < 0.05).sum()} of {len(miss)} misses are within 0.05 of a boundary")

    print("\n=== For misses: which alternative calculation would have matched CMS? (count) ===")
    alts = [c for c in miss.columns if c.startswith("alt_")]
    print(miss.groupby(["star_year", "rating"]).apply(
        lambda d: pd.Series({a.replace("alt_", ""): int((d[a] == d.official).sum()) for a in alts} | {"misses": len(d)}),
        include_groups=False).to_string())

    print("\n=== Sample of misses ===")
    cols = ["star_year", "contract_id", "contract_type", "rating", "official", "s5", "mean", "variance",
            "reward_factor", "cai", "raw_final", "n_measures"]
    print(miss.sort_values(["star_year", "rating"])[cols].head(25).round(4).to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
