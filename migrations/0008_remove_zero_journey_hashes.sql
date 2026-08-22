UPDATE deliveries
SET snapshot_json = json_set(snapshot_json, '$.resolved_journey_sha256', NULL)
WHERE json_extract(snapshot_json, '$.resolved_journey_sha256') =
      '0000000000000000000000000000000000000000000000000000000000000000';

