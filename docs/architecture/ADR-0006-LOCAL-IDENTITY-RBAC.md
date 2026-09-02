# ADR-0006: Local Identity and Role-Based Authorization

Status: accepted for V0.2.1. ADR-0016 records the project-scoped extension; that slice is now
implemented and Deterministic verified, while its composite architecture change remains
`Accepted/Not Implemented`.

## Decision

The local release supports Administrator, Editor, and Viewer roles. Passwords use salted scrypt;
stored sessions contain only hashed bearer values and expire. Mutating cookie-authenticated requests
must pass same-origin checks.

Administrators manage users, settings, Journey publication, Candidate apply, and workspace reset.
Editors create Deliveries, approve plans, edit Wiki content, and comment. Viewers have read access.
Document permissions may narrow inherited Space access but cannot exceed the user's product role.

Credentials for Agent Instances remain `env:` or `keychain:` references. No Interface returns
password material, session bearer values, or referenced secret values.

## 2026-09-02 后续关系

ADR-0016 在不替代本 ADR 的本地身份、Session、CSRF 和全局 Capability 上限的前提下，接受
`ProjectRole` 与项目资源授权。当前 Revision 已实现 `ProjectMembership`、最后 Owner 保护、
Administrator bypass Receipt 与统一 Project Access Policy；有效权限是全局 Capability 上限与
ProjectRole/Resource Policy 的交集。它仍不等于生产级多租户隔离或逐用户 Feishu ACL。
