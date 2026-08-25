ALTER TABLE wiki_spaces ADD COLUMN scope_kind TEXT NOT NULL DEFAULT 'project';
ALTER TABLE wiki_spaces ADD COLUMN project_id TEXT REFERENCES projects(id);
UPDATE wiki_spaces SET project_id='legacy-default' WHERE scope_kind='project';
CREATE INDEX idx_wiki_spaces_project ON wiki_spaces(project_id,name);

CREATE VIRTUAL TABLE knowledge_search_fts USING fts5(
    project_id UNINDEXED,
    source_kind UNINDEXED,
    source_id UNINDEXED,
    title,
    searchable_text,
    revision UNINDEXED,
    content_sha256 UNINDEXED,
    source_link UNINDEXED
);
