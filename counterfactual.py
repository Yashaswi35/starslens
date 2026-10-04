"""Phase 4b: what if CMS had NOT cut patient experience, complaints, and access weights from 4 to 2 in 2026?

Recomputes every 2026 rating twice with identical code, actual weights vs the old weights, and lists the
contracts whose top-level rating crossed the 4-star bonus line because of the reweighting alone.

Reward factor thresholds are percentiles of all contracts' scores, so they move when weights move.
Both scenarios therefore use thresholds computed from the data (65th/85th percentile of means,
30th/70th of variances). Step 1 checks that this reproduces CMS's published 2026 thresholds.

Run (after replicate.py):
  python counterfactual.py | tee counterfactual_log.txt
"""
import duckdb
import numpy as np
import pandas as pd

import replicate as rp

YEAR = 2026
REWEIGHTED = {"Patients’ Experience and Complaints Measure", "Patients' Experience and Complaints Measure",
              "Measures Capturing Access"}
OLD_WEIGHT = 4.0


def stats_frame(stars, contracts, disaster):
    """Mean and variance for every contract x rating x scenario (no reward factor yet)."""
    dis = disaster.pivot_table(index=["star_year", "contract_id"], columns="disaster_year", values="pct", aggfunc="max")
    rows = []
    for (year, cid), g in stars.groupby(["star_year", "contract_id"]):
        c = contracts[(contracts.star_year == year) & (contracts.contract_id == cid)]
        if c.empty:
            continue
        c = c.iloc[0]
        ctype = rp.classify(c, (g.part == "D").any())
        if c.puerto_rico_only == "Yes":
            g = g.assign(weight=np.where(g.measure_code.isin(["D08", "D09", "D10"]), 0.0, g.weight))
        plans = []
        if ctype in ("MA-PD", "MA-Only"):
            plans.append(("part_c", "part_c", g[g.part == "C"], c.part_c_summary, c.part_c_fac))
        if ctype == "MA-PD":
            plans.append(("part_d", "part_d_mapd", g[g.part == "D"], c.part_d_summary, c.part_d_mapd_fac))
            plans.append(("overall", "overall", g[~((g.part == "D") & g.measure_name.isin(rp.OVERALL_DUPLICATES))],
                          c.overall_rating, c.overall_fac))
        if ctype == "PDP":
            plans.append(("part_d", "part_d_pdp", g[g.part == "D"], c.part_d_summary, c.part_d_pdp_fac))
        new_codes = rp.NEW_MEASURES.get(year, set())
        yrs = dis.loc[(year, cid)] if (year, cid) in dis.index else pd.Series(dtype=float)
        d_hos, d_hedis = (yrs.get(year - 3, 0) or 0), (yrs.get(year - 2, 0) or 0)
        for rating, rtype, m, official, fac in plans:
            if pd.isna(official):
                continue
            has_hos = m.measure_code.isin(new_codes & rp.NEW_HOS).any()
            has_hedis = m.measure_code.isin(new_codes & rp.NEW_HEDIS).any()
            eligible = bool(new_codes) and ((has_hos and has_hedis and (d_hos >= 25 or d_hedis >= 25)) or
                                            (has_hedis and not has_hos and d_hedis >= 25) or
                                            (has_hos and not has_hedis and d_hos >= 25))
            top = {"MA-PD": "overall", "MA-Only": "part_c", "PDP": "part_d"}[ctype] == rating
            for impr in ("With", "Without"):
                for new in ("With", "Without"):
                    sub = m if impr == "With" else m[m.weighting_category != "Improvement Measure"]
                    sub = sub if new == "With" else sub[~sub.measure_code.isin(new_codes)]
                    mean, var, n = rp.weighted_stats(sub)
                    rows.append(dict(star_year=year, contract_id=cid, ctype=ctype, rating=rating, rtype=rtype,
                                     official=official, fac=fac, top=top, eligible=eligible,
                                     impr=impr, new=new, mean=mean, var=var))
    return pd.DataFrame(rows)


def computed_thresholds(st):
    t = {}
    for (year, rtype, impr, new), d in st.groupby(["star_year", "rtype", "impr", "new"]):
        for p in (65, 85):
            t[(year, impr, new, "mean", p, rtype)] = np.nanpercentile(d["mean"], p)
        for p in (30, 70):
            t[(year, impr, new, "variance", p, rtype)] = np.nanpercentile(d["var"], p)
    return t


def finalize(st, t, cai_map):
    st = st.copy()
    st["rf"] = [rp.reward_factor(r.mean, r.var, r.star_year, r.impr, r.new, r.rtype, t) for r in st.itertuples()]
    st["cai"] = [cai_map.get((r.star_year, r.rtype, int(r.fac)), 0.0) if pd.notna(r.fac) else 0.0
                 for r in st.itertuples()]
    st["s3"] = (st["mean"] + st.rf + st.cai).map(rp.cms_round)
    key = ["star_year", "contract_id", "rating"]
    wide = st.pivot_table(index=key + ["ctype", "official", "top", "eligible"], columns=["impr", "new"],
                          values="s3").reset_index()

    def impr_rule(r, new):
        w, wo = r[("With", new)], r[("Without", new)]
        return wo if (wo >= 4 and w < wo) else w

    finals = []
    for _, r in wide.iterrows():
        s4 = impr_rule(r, "With") if r[("top", "")] else r[("With", "With")]
        alt = impr_rule(r, "Without") if r[("top", "")] else r[("With", "Without")]
        finals.append(max(s4, alt) if r[("eligible", "")] and not pd.isna(alt) else s4)
    out = wide[[(k, "") for k in key + ["ctype", "official", "top"]]].copy()
    out.columns = key + ["ctype", "official", "top"]
    out["final"] = finals
    return out


def main():
    con = duckdb.connect(rp.DB)
    stars, contracts, disaster, thr, cai = rp.load(con)
    stars, contracts = stars[stars.star_year == YEAR], contracts[contracts.star_year == YEAR]
    disaster = disaster[disaster.star_year == YEAR]
    cai_map = {(r.star_year, r.rating_type, r.fac): r.cai_value for r in cai.itertuples()}
    parents = con.sql(f"SELECT contract_id, parent_org FROM dim_contract WHERE star_year = {YEAR}").df()

    actual = stats_frame(stars, contracts, disaster)
    old = stats_frame(stars.assign(weight=np.where(stars.weighting_category.isin(REWEIGHTED), OLD_WEIGHT,
                                                   stars.weight)), contracts, disaster)
    t_actual, t_old = computed_thresholds(actual), computed_thresholds(old)

    print("=== Step 1: our computed thresholds vs CMS published (2026, with improvement, with new measures) ===")
    rows = []
    for r in thr[(thr.star_year == YEAR) & (thr.improvement == "With") & (thr.new_measures == "With")].itertuples():
        ours = t_actual.get((YEAR, "With", "With", r.stat, r.percentile, r.rating_type))
        rows.append(dict(rating_type=r.rating_type, stat=r.stat, percentile=r.percentile,
                         cms=r.value, ours=ours, diff=None if ours is None else ours - r.value))
    print(pd.DataFrame(rows).round(4).to_string(index=False))

    base, cf = finalize(actual, t_actual, cai_map), finalize(old, t_old, cai_map)
    print(f"\nBaseline match rate with computed thresholds: {100 * (base.final == base.official).mean():.1f}% "
          f"of {len(base)} ratings")

    m = base.merge(cf[["star_year", "contract_id", "rating", "final"]], on=["star_year", "contract_id", "rating"],
                   suffixes=("_actual", "_old_weights"))
    m = m.merge(parents, on="contract_id", how="left")
    top = m[m.top].copy()
    top["change"] = top.final_actual - top.final_old_weights
    print("\n=== Step 2: top-level rating, actual 2026 weights minus old weights (contracts) ===")
    print(top.change.value_counts().sort_index().to_string())

    gained = top[(top.final_actual >= 4) & (top.final_old_weights < 4)]
    lost = top[(top.final_actual < 4) & (top.final_old_weights >= 4)]
    print(f"\n=== Step 3: crossings of the 4-star bonus line caused by the reweighting ===")
    print(f"Reached 4+ only because of the new weights: {len(gained)} contracts")
    print(f"Fell below 4 only because of the new weights: {len(lost)} contracts")
    print(f"Net: {len(gained) - len(lost):+d}   (rated top-level contracts: {len(top)})")
    cols = ["contract_id", "parent_org", "ctype", "final_old_weights", "final_actual", "official"]
    for label, d in [("Gained bonus status", gained), ("Lost bonus status", lost)]:
        print(f"\n--- {label} ---")
        print(d[cols].sort_values("parent_org").to_string(index=False) if len(d) else "none")
    print("\n=== By parent organization (contracts gained minus lost, top 10 by absolute net) ===")
    by = pd.concat([gained.assign(g=1), lost.assign(g=-1)]).groupby("parent_org").g.agg(["sum", "count"])
    print(by.reindex(by["sum"].abs().sort_values(ascending=False).index).head(10).to_string())

    out = m.assign(crossed=np.select([m.index.isin(gained.index), m.index.isin(lost.index)],
                                     ["gained", "lost"], ""))
    con.register("tmp", out)
    con.execute("CREATE OR REPLACE TABLE cf_reweighting_2026 AS SELECT * FROM tmp")
    con.close()


if __name__ == "__main__":
    main()
