CREATE TABLE fact_github_repository_snapshot AS
SELECT DISTINCT *
FROM raw_github_repository_snapshot;

CREATE TABLE fact_github_event AS
SELECT
    event_id,
    arg_max(event_type, source_snapshot_at) AS event_type,
    arg_max(event_category, source_snapshot_at) AS event_category,
    arg_max(event_created_at, source_snapshot_at) AS event_created_at,
    arg_max(repository_name, source_snapshot_at) AS repository_name,
    arg_max(event_action, source_snapshot_at) AS event_action,
    arg_max(push_commit_count, source_snapshot_at) AS push_commit_count,
    min(source_snapshot_at) AS first_seen_at,
    max(source_snapshot_at) AS last_seen_at
FROM raw_github_event
GROUP BY event_id;

CREATE TABLE mart_github_repository_latest AS
SELECT *
FROM fact_github_repository_snapshot
QUALIFY row_number() OVER (
    PARTITION BY repository_id
    ORDER BY snapshot_at DESC
) = 1;

CREATE TABLE mart_github_portfolio AS
WITH anchor AS (
    SELECT max(snapshot_at) AS snapshot_at
    FROM mart_github_repository_latest
)
SELECT
    anchor.snapshot_at,
    count(*) AS public_repositories,
    count(*) FILTER (WHERE NOT repository.is_fork) AS owned_repositories,
    count(*) FILTER (WHERE repository.is_fork) AS forked_repositories,
    count(*) FILTER (WHERE repository.is_archived) AS archived_repositories,
    count(*) FILTER (
        WHERE NOT repository.is_fork
          AND NOT repository.is_archived
          AND repository.pushed_at >= anchor.snapshot_at - INTERVAL 90 DAY
    ) AS active_owned_repositories_90d,
    sum(repository.stargazers_count) FILTER (WHERE NOT repository.is_fork)
        AS stars_on_owned_repositories
FROM mart_github_repository_latest AS repository
CROSS JOIN anchor
GROUP BY anchor.snapshot_at;

CREATE TABLE mart_github_activity_daily AS
SELECT
    date_trunc('day', event_created_at) AS event_date,
    event_category,
    event_type,
    count(*) AS event_count,
    sum(push_commit_count) AS captured_push_commits
FROM fact_github_event
GROUP BY event_date, event_category, event_type;
