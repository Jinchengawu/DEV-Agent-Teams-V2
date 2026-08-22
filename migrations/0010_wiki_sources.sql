ALTER TABLE wiki_documents ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE wiki_documents ADD COLUMN source_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wiki_documents_source
ON wiki_documents(source_kind, source_id)
WHERE source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wiki_documents_space_parent
ON wiki_documents(space_id, parent_id, updated_at);
