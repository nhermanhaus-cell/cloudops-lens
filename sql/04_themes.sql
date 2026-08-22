CREATE TABLE dim_incident_theme AS
SELECT
    row_number() OVER (ORDER BY theme_slug) AS theme_id,
    theme_slug,
    theme_name
FROM (
    SELECT DISTINCT theme_slug, theme_name
    FROM raw_incident_theme
);

CREATE TABLE bridge_incident_theme AS
SELECT DISTINCT
    source.incident_id,
    theme.theme_id,
    source.rule_id,
    source.evidence
FROM raw_incident_theme AS source
JOIN dim_incident_theme AS theme USING (theme_slug, theme_name);

