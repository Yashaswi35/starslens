-- Contract attributes per year. Contract ID prefix: H/R = MA, S = standalone PDP, E = employer.
CREATE OR REPLACE TABLE dim_contract AS
SELECT DISTINCT
  star_year,
  trim(contract_id)  AS contract_id,
  trim(org_type)     AS org_type,
  trim(contract_name) AS contract_name,
  trim(marketing_name) AS marketing_name,
  trim(parent_org)   AS parent_org,
  CASE left(trim(contract_id), 1)
       WHEN 'S' THEN 'PDP' WHEN 'E' THEN 'Employer' ELSE 'MA' END AS contract_family
FROM raw_contracts;
