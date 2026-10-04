-- Categorical Adjustment Index final adjustment categories (FAC) per contract x year.
CREATE OR REPLACE TABLE stg_cai AS
SELECT
  star_year,
  trim(contract_id) AS contract_id,
  TRY_CAST(max(CASE WHEN field = 'Part C FAC'       THEN value END) AS INTEGER) AS part_c_fac,
  TRY_CAST(max(CASE WHEN field = 'Part D MA-PD FAC' THEN value END) AS INTEGER) AS part_d_mapd_fac,
  TRY_CAST(max(CASE WHEN field = 'Part D PDP FAC'   THEN value END) AS INTEGER) AS part_d_pdp_fac,
  TRY_CAST(max(CASE WHEN field = 'Overall FAC'      THEN value END) AS INTEGER) AS overall_fac,
  max(CASE WHEN field = 'Puerto Rico Only' THEN value END)                      AS puerto_rico_only
FROM raw_cai
GROUP BY 1, 2;
