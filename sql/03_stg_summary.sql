-- Summary ratings pivoted to one row per contract x year. Field names carry a year prefix
-- ("2026 Part C Summary"), so match on the suffix.
CREATE OR REPLACE TABLE stg_summary AS
WITH p AS (
  SELECT
    star_year,
    trim(contract_id) AS contract_id,
    max(CASE WHEN field LIKE '%Part C Summary' THEN value END) AS part_c_text,
    max(CASE WHEN field LIKE '%Part D Summary' THEN value END) AS part_d_text,
    max(CASE WHEN field LIKE '%Overall'        THEN value END) AS overall_text,
    max(CASE WHEN field = 'Organization Type'  THEN value END) AS org_type,
    max(CASE WHEN field = 'SNP'                THEN value END) AS snp
  FROM raw_summary
  GROUP BY 1, 2
)
SELECT *,
  TRY_CAST(part_c_text  AS DOUBLE) AS part_c_summary,
  TRY_CAST(part_d_text  AS DOUBLE) AS part_d_summary,
  TRY_CAST(overall_text AS DOUBLE) AS overall_rating
FROM p;
