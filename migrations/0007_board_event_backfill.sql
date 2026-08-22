INSERT INTO product_events(
    event_id,
    event_type,
    aggregate_type,
    aggregate_id,
    aggregate_version,
    payload_json,
    occurred_at
)
SELECT
    id || ':legacy-import:' || coalesce(json_extract(snapshot_json, '$.version'), 1),
    'delivery.legacy-imported',
    'delivery',
    id,
    coalesce(json_extract(snapshot_json, '$.version'), 1),
    json_object(
        'status', json_extract(snapshot_json, '$.status'),
        'title', coalesce(
            json_extract(snapshot_json, '$.task.title'),
            json_extract(snapshot_json, '$.user_request')
        ),
        'acceptance_ids', coalesce(
            json_extract(snapshot_json, '$.task.acceptance_ids'),
            json('[]')
        ),
        'execution_identity', json_extract(snapshot_json, '$.execution_identity'),
        'error_code', json_extract(snapshot_json, '$.error_code')
    ),
    coalesce(json_extract(snapshot_json, '$.updated_at'), CURRENT_TIMESTAMP)
FROM deliveries AS delivery
WHERE NOT EXISTS (
    SELECT 1 FROM product_events AS event
    WHERE event.aggregate_type = 'delivery'
      AND event.aggregate_id = delivery.id
);
