# ADR-0006: Local Identity and Role-Based Authorization

Status: accepted for V0.2.1.

## Decision

The local release supports Administrator, Editor, and Viewer roles. Passwords use salted scrypt;
stored sessions contain only hashed bearer values and expire. Mutating cookie-authenticated requests
must pass same-origin checks.

Administrators manage users, settings, Journey publication, Candidate apply, and workspace reset.
Editors create Deliveries, approve plans, edit Wiki content, and comment. Viewers have read access.
Document permissions may narrow inherited Space access but cannot exceed the user's product role.

Credentials for Agent Instances remain `env:` or `keychain:` references. No Interface returns
password material, session bearer values, or referenced secret values.

