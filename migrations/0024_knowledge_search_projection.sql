CREATE TABLE knowledge_search_projection(
    project_id TEXT NOT NULL REFERENCES projects(id),
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    searchable_text TEXT NOT NULL,
    revision TEXT NOT NULL,
    content_sha256 TEXT,
    source_link TEXT NOT NULL,
    PRIMARY KEY(project_id,source_kind,source_id)
);

CREATE INDEX idx_knowledge_search_projection_source
ON knowledge_search_projection(project_id,source_kind,title);

CREATE VIRTUAL TABLE knowledge_search_fts_v2 USING fts5(
    project_id UNINDEXED,
    source_kind UNINDEXED,
    source_id UNINDEXED,
    title,
    summary,
    searchable_text,
    revision UNINDEXED,
    content_sha256 UNINDEXED,
    source_link UNINDEXED
);
