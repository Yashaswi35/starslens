-- One row per contract x measure x year, with numeric stars or an explicit not-rated reason.
-- Measure codes are NOT stable across years (2026 added measures and renumbered),
-- so measure_key (normalized name) is the cross-year join key.
CREATE OR REPLACE TABLE stg_measure_stars AS
SELECT
  star_year,
  trim(contract_id)                                                    AS contract_id,
  measure_code,
  measure_name,
  left(measure_code, 1) || ':' || trim(regexp_replace(lower(measure_name), '[^a-z0-9]+', ' ', 'g')) AS measure_key,
  left(measure_code, 1)                                                AS part,
  domain_code,
  domain_name,
  measurement_period,
  TRY_CAST(TRY_CAST(star_value AS DOUBLE) AS INTEGER)                  AS stars,
  CASE WHEN TRY_CAST(star_value AS DOUBLE) IS NULL THEN star_value END AS not_rated_reason
FROM raw_measure_stars;
