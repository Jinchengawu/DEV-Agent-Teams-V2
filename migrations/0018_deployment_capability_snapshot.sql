ALTER TABLE agent_deployments
ADD COLUMN capability_requirements_json TEXT NOT NULL DEFAULT '[]';

UPDATE agent_deployments
SET capability_requirements_json = COALESCE(
    (
        SELECT json_extract(agent_profile_revisions.spec_json, '$.capabilities')
        FROM agent_profile_revisions
        WHERE agent_profile_revisions.profile_id = agent_deployments.profile_id
          AND agent_profile_revisions.revision = agent_deployments.profile_revision
    ),
    '[]'
);
