ALTER TABLE agent_deployments
ADD COLUMN extension_snapshot_json TEXT NOT NULL DEFAULT '[]';
