ALTER TABLE pipeline_drafts
ADD COLUMN workcell_stage_map_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE pipeline_drafts
ADD COLUMN release_contract_snapshot_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE pipeline_revisions
ADD COLUMN workcell_stage_map_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE pipeline_revisions
ADD COLUMN release_contract_snapshot_json TEXT NOT NULL DEFAULT '[]';
