# Decisions

Choices not covered by `SPEC.md`, with the default taken. Each can be revisited.

## D1. Project key and icon

Kaneo requires `slug` (project key) and `icon` on `POST /project`.
Default: derive the key from the project name with the same algorithm as the Kaneo web UI (`apps/web/src/lib/generate-project-id.ts`), fall back to `PRJ` when empty, then append `2`, `3`, ... until it is not used by any project of the workspace (read from `GET /project`). Icon: `Layout`, the Kaneo UI default.
A `409` on creation is reported as a failure; no retry with another key, to keep writes non-repeated.

## D2. Template columns replace Kaneo's default columns

Kaneo seeds four columns on every project. When a template defines `columns`, the defaults whose slug matches a template column are reused (renamed if the display name differs), the others are deleted while still empty, and the order is set with `PUT /column/reorder/{projectId}`. The last template column gets `isFinal: true`.

## D3. Labels

Workspace label definitions are the rows with `taskId == null`. Missing definitions are created with color `#6b7280`. Labels are attached with `PUT /label/{definitionId}/task`, never with a task-bound row id (that would move the label away from its task).

## D4. Due date sent to Kaneo

`YYYY-MM-DDT12:00:00.000Z` (noon UTC), as the spec requires.

## D5. Rate limiting

A `429` from Kaneo is the only write that is replayed, after `Retry-After` (capped at 60 s per wait, 5 waits per request). A 429 means the request was not processed, so it cannot create a duplicate.

## D6. Token validation

A pasted token can be an API key or a session token. It is validated with `GET /workspace`, which accepts both (`GET /auth/get-session` only resolves sessions).

## D7. `KANEO_BASE_URL` and device flow paths

`KANEO_BASE_URL` includes `/api`. Device flow endpoints are called as `{KANEO_BASE_URL}/auth/device/code` and `{KANEO_BASE_URL}/auth/device/token`. `KANEO_PUBLIC_URL` defaults to `KANEO_BASE_URL` with a trailing `/api` removed.

## D8. Python version

The spec targets Python 3.13 (Docker image `python:3.13-slim`). `pyproject.toml` declares `requires-python = ">=3.13"` and CI runs 3.13.
