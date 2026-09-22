-- v0.5.2 首次引导状态独立于 Project lifecycle。
CREATE TABLE project_onboarding(
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    mode TEXT NOT NULL CHECK(mode IN ('guided_evaluation','standard')),
    status TEXT NOT NULL CHECK(status IN ('setup','in_evaluation','ready')),
    evaluation_delivery_id TEXT REFERENCES deliveries(id),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK(
        (status='setup' AND evaluation_delivery_id IS NULL AND completed_at IS NULL)
        OR (status='in_evaluation' AND evaluation_delivery_id IS NOT NULL AND completed_at IS NULL)
        OR (status='ready' AND completed_at IS NOT NULL)
    )
);

-- 历史项目不得被新引导门禁破坏；幂等迁移后直接为 ready。
INSERT INTO project_onboarding(
    project_id,mode,status,evaluation_delivery_id,version,created_at,updated_at,completed_at
)
SELECT id,'standard','ready',NULL,1,created_at,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
FROM projects;

CREATE UNIQUE INDEX idx_project_onboarding_evaluation_delivery
ON project_onboarding(evaluation_delivery_id)
WHERE evaluation_delivery_id IS NOT NULL;
