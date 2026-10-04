-- Measure scores as numbers. Percent strings ("78%") become 78; plain numbers pass through.
CREATE OR REPLACE TABLE stg_measure_scores AS
SELECT
  star_year,
  trim(contract_id)                                                            AS contract_id,
  measure_code,
  score_value                                                                  AS score_text,
  TRY_CAST(replace(replace(score_value, '%', ''), ',', '') AS DOUBLE)          AS score,
  contains(coalesce(score_value, ''), '%')                                     AS is_percent
FROM raw_measure_data;
