INSERT INTO legacy_snapshot_audit(
    source_database,
    aggregate_type,
    aggregate_id,
    original_sha256,
    original_json,
    migration_action,
    imported_at
)
SELECT
    'agent-team-os.sqlite',
    'delivery',
    id,
    sha256(snapshot_json),
    snapshot_json,
    'normalize-missing-journey-sha256',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM deliveries
WHERE json_type(snapshot_json, '$.resolved_journey_sha256') IS NULL;

UPDATE deliveries
SET snapshot_json = json_set(
    snapshot_json,
    '$.status', 'failed',
    '$.error_code', 'LEGACY_INCOMPLETE_EVIDENCE',
    '$.updated_at', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
)
WHERE json_type(snapshot_json, '$.resolved_journey_sha256') IS NULL
  AND json_extract(snapshot_json, '$.status') IN (
      'queued',
      'planning',
      'awaiting_plan_decision',
      'executing',
      'verifying',
      'awaiting_candidate_decision',
      'applying'
  );

UPDATE deliveries
SET snapshot_json = json_set(snapshot_json, '$.resolved_journey_sha256', NULL)
WHERE json_type(snapshot_json, '$.resolved_journey_sha256') IS NULL;
