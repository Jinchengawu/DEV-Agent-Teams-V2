-- 工作区机器验证方案独立于既有 Git 能力回执；历史绑定保持未资格化。
ALTER TABLE workspace_bindings ADD COLUMN verification_profile_id TEXT;
ALTER TABLE workspace_bindings ADD COLUMN verification_profile_json TEXT;
ALTER TABLE workspace_bindings ADD COLUMN verification_profile_error_code TEXT;
