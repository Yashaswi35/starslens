-- One row per contract x year: official ratings, CAI category, and rated-measure counts.
-- star_rating_for_bonus: overall rating for MA-PD, Part C summary for MA-only contracts.
CREATE OR REPLACE TABLE fct_contract_year AS
WITH counts AS (
  SELECT star_year, contract_id,
         count(*) FILTER (WHERE part = 'C' AND stars IS NOT NULL) AS rated_c_measures,
         count(*) FILTER (WHERE part = 'D' AND stars IS NOT NULL) AS rated_d_measures
  FROM stg_measure_stars GROUP BY 1, 2
)
SELECT
  d.star_year, d.contract_id, d.org_type, d.parent_org, d.contract_family,
  s.part_c_summary, s.part_d_summary, s.overall_rating, s.snp,
  c.part_c_fac, c.part_d_mapd_fac, c.part_d_pdp_fac, c.overall_fac,
  n.rated_c_measures, n.rated_d_measures,
  COALESCE(s.overall_rating, s.part_c_summary) AS star_rating_for_bonus,
  COALESCE(s.overall_rating, s.part_c_summary) >= 4 AS meets_4_star
FROM dim_contract d
LEFT JOIN stg_summary s USING (star_year, contract_id)
LEFT JOIN stg_cai     c USING (star_year, contract_id)
LEFT JOIN counts      n USING (star_year, contract_id);
