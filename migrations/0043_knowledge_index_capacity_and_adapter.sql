ALTER TABLE embedding_qualification_snapshots
    ADD COLUMN vector_index_adapter_revision TEXT NOT NULL
    DEFAULT 'sqlite-vec-vector-index-v1';

ALTER TABLE knowledge_index_revisions
    ADD COLUMN document_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE knowledge_index_revisions
    ADD COLUMN capacity_status TEXT NOT NULL DEFAULT 'normal'
    CHECK(capacity_status IN ('normal','warning'));
