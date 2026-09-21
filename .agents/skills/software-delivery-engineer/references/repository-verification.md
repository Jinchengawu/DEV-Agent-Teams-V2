# Repository verification

Select checks from the changed surface; do not claim checks that were not run. The GitHub CI workflow is the executable source of truth if this reference drifts.

## Fast focused loop

- Python: run the narrowest relevant test module or test case with `.venv/bin/python -m pytest -q <path>` when the repository environment exists; otherwise use `uv run pytest -q <path>`.
- Console: run the relevant Vitest target through `pnpm --dir console test -- <target>` when supported by the current package scripts.
- Contract or migration changes: run the closest contract/migration test before the broader suite.

A focused pass proves only the selected scope.

## CI-aligned qualification

Use the applicable commands in this order so cheap failures arrive first:

```sh
uv run agent-team-os-dev eval validate-dataset
uv run ruff check .
uv run mypy
uv run pytest -q
uv build
uv run python scripts/export_openapi.py --output console/openapi.json
pnpm --dir console api:check
pnpm --dir console typecheck
pnpm --dir console test
pnpm --dir console build
```

Fresh-database migration validation and browser tests may require isolated temporary state or additional runtime dependencies. Follow `.github/workflows/ci.yml` and the relevant runbook rather than improvising against an existing operator database.

## Release evidence boundary

CI-aligned checks qualify code for review; they do not prove a formal Agent-Team-OS release. A major-version handoff still requires same-revision Browser, Deterministic, and Live evidence, each with `FAIL=0`, `WARN=0`, and `skipped=0`, plus the repository's required Release/Apply receipts and read-back.

If a command cannot run because of sandbox, network, port binding, missing credentials, or unavailable external services, report it as an environment or authorization boundary. Do not convert it into either a product failure or a pass.
