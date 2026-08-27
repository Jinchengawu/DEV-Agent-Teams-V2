ALTER TABLE wiki_spaces
ADD COLUMN space_kind TEXT NOT NULL DEFAULT 'custom';

ALTER TABLE wiki_spaces
ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE wiki_documents
ADD COLUMN document_kind TEXT NOT NULL DEFAULT 'project-general';

ALTER TABLE wiki_documents
ADD COLUMN role_key TEXT;

ALTER TABLE wiki_documents
ADD COLUMN delivery_id TEXT;

ALTER TABLE wiki_documents
ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE wiki_revisions
ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE wiki_revisions
ADD COLUMN asset_references_json TEXT NOT NULL DEFAULT '[]';

UPDATE wiki_revisions
SET provenance_json=json_object(
    'producer_kind',CASE WHEN created_by IS NULL THEN 'legacy' ELSE 'human' END,
    'producer_id',COALESCE(created_by,'legacy-system')
);

CREATE TABLE knowledge_legacy_document_mappings(
    legacy_document_id TEXT PRIMARY KEY REFERENCES wiki_documents(id),
    migrated_document_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    migrated_at TEXT NOT NULL
);

INSERT INTO wiki_spaces(
    id,name,description,version,created_by,created_at,updated_at,
    scope_kind,project_id,space_kind,lifecycle_status
)
SELECT
    'project-docs:' || project.id,
    project.name || ' · 项目文档',
    '项目角色在交付过程中发布的可协作文档。',
    1,
    (SELECT user.id FROM users user WHERE user.id=project.created_by),
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP,
    'project',
    project.id,
    'project-documents',
    CASE WHEN project.lifecycle_status='archived' THEN 'archived' ELSE 'active' END
FROM projects project;

UPDATE wiki_spaces
SET space_kind='legacy-archive',lifecycle_status='archived'
WHERE id='system:delivery-evidence';

UPDATE wiki_documents
SET lifecycle_status='archived'
WHERE space_id='system:delivery-evidence';

INSERT INTO knowledge_legacy_document_mappings(
    legacy_document_id,migrated_document_id,project_id,migrated_at
)
SELECT
    document.id,
    'project-docs:migrated:' || document.id,
    space.project_id,
    CURRENT_TIMESTAMP
FROM wiki_documents document
JOIN wiki_spaces space ON space.id=document.space_id
WHERE document.source_kind='manual'
AND space.project_id IS NOT NULL
AND space.id!='system:delivery-evidence';

INSERT INTO wiki_documents(
    id,space_id,parent_id,title,current_revision,version,created_by,created_at,
    updated_at,source_kind,source_id,document_kind,role_key,delivery_id,lifecycle_status
)
SELECT
    mapping.migrated_document_id,
    'project-docs:' || mapping.project_id,
    NULL,
    document.title,
    document.current_revision,
    document.version,
    document.created_by,
    document.created_at,
    document.updated_at,
    'legacy-migrated',
    document.id,
    document.document_kind,
    document.role_key,
    document.delivery_id,
    'active'
FROM knowledge_legacy_document_mappings mapping
JOIN wiki_documents document ON document.id=mapping.legacy_document_id;

UPDATE wiki_documents
SET parent_id=(
    SELECT parent_mapping.migrated_document_id
    FROM knowledge_legacy_document_mappings child_mapping
    JOIN wiki_documents legacy_child
        ON legacy_child.id=child_mapping.legacy_document_id
    JOIN knowledge_legacy_document_mappings parent_mapping
        ON parent_mapping.legacy_document_id=legacy_child.parent_id
    WHERE child_mapping.migrated_document_id=wiki_documents.id
)
WHERE id IN (
    SELECT migrated_document_id FROM knowledge_legacy_document_mappings
)
AND EXISTS(
    SELECT 1
    FROM knowledge_legacy_document_mappings child_mapping
    JOIN wiki_documents legacy_child
        ON legacy_child.id=child_mapping.legacy_document_id
    JOIN knowledge_legacy_document_mappings parent_mapping
        ON parent_mapping.legacy_document_id=legacy_child.parent_id
    WHERE child_mapping.migrated_document_id=wiki_documents.id
);

INSERT INTO wiki_revisions(
    document_id,revision,content_json,search_text,content_sha256,created_by,created_at,
    provenance_json,asset_references_json
)
SELECT
    mapping.migrated_document_id,
    revision.revision,
    revision.content_json,
    revision.search_text,
    revision.content_sha256,
    revision.created_by,
    revision.created_at,
    json_object(
        'producer_kind','legacy-migration',
        'producer_id',COALESCE(revision.created_by,'legacy-system')
    ),
    revision.asset_references_json
FROM knowledge_legacy_document_mappings mapping
JOIN wiki_revisions revision ON revision.document_id=mapping.legacy_document_id;

CREATE INDEX idx_wiki_spaces_kind_status
ON wiki_spaces(project_id,space_kind,lifecycle_status);

CREATE INDEX idx_wiki_documents_role_filters
ON wiki_documents(space_id,document_kind,role_key,delivery_id,lifecycle_status,updated_at);
