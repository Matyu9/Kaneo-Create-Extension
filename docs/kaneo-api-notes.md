# Kaneo API notes

Verified against Kaneo **v2.46.2** (`usekaneo/kaneo`, commit `46ef6f7`), better-auth **1.6.26**.
Paths below are relative to the API base URL (`KANEO_BASE_URL`, which ends with `/api`).
Source references point to files in the Kaneo repository at that commit, or to the published `better-auth` package.

## 1. Authentication

| Point | Finding | Source |
|---|---|---|
| Header | `Authorization: Bearer <token>`. The same header accepts an API key or a session token (browser session or device flow token). | `apps/docs/openapi.json` (`components.securitySchemes.bearerAuth`), `apps/docs/api-reference/authentication.mdx`, `apps/api/src/utils/authenticate-api-request.ts` |
| Alternate header | `x-api-key: <key>` is also accepted for API keys. Not used by this module. | `apps/api/src/auth.ts` (`apiKey({ apiKeyHeaders: "x-api-key" })`) |
| Generating an API key | Kaneo UI: Settings → Account → API Keys (Developer section). The key is shown once. | `apps/docs/api-reference/authentication.mdx` |
| Token check | `GET /auth/get-session` returns `{ session, user }` when the token is a valid session; for an API key, a live instance answers `200` with `null` (seen on an instance in October 2026); `401` otherwise. The module uses it only to show the signed-in user's name. Used by `kaneo-mcp` to validate a stored token. For an API key, use `GET /workspace` instead (`get-session` only resolves sessions). | `packages/mcp/src/auth/auth-service.ts` (`validateAccessToken`), `apps/api/src/utils/get-user-session.ts` |
| Rate limit | API keys: 100 requests per 60 s sliding window, then `429 RATE_LIMITED` with `Retry-After`. Session and device tokens are not counted. | `apps/docs/api-reference/authentication.mdx` (Rate limits), `apps/api/src/auth.ts` |

## 2. Device flow (RFC 8628)

Enabled through better-auth's `deviceAuthorization` plugin with `verificationUri = <KANEO_CLIENT_URL>/device` and a client allow list.

| Point | Finding | Source |
|---|---|---|
| Allowed clients | `DEVICE_AUTH_CLIENT_IDS` (comma separated). Unset: `kaneo-cli`, `kaneo-mcp`. Setting it replaces the defaults, so they must be listed again. | `apps/api/src/auth.ts` (`getDeviceAuthClientIds`), `apps/docs/core/installation/environment-variables.mdx` |
| Request code | `POST /auth/device/code`, JSON `{ "client_id": "..." }` (optional `scope`). | `packages/mcp/src/auth/device-flow.ts` (`requestDeviceCode`) |
| Code response (200) | `{ device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval }`. Header `Cache-Control: no-store`. | better-auth `dist/plugins/device-authorization/routes.mjs` (`deviceCode`) |
| Code defaults | `expires_in` = 1800 s (30 min, Kaneo keeps the plugin default; the docs example shows 600). `interval` = 5 s. `user_code` 8 characters. | better-auth `dist/plugins/device-authorization/index.mjs` (option defaults), `apps/api/src/auth.ts` (no override) |
| Code errors | `400 { "error": "invalid_client", "error_description": ... }` for a client not in the allow list. | better-auth `routes.mjs` (`deviceCode`) |
| Poll | `POST /auth/device/token`, JSON `{ "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": "...", "client_id": "..." }`. | `packages/mcp/src/auth/device-flow.ts` (`pollDeviceTokenOnce`) |
| Token response (200) | `{ access_token, token_type: "Bearer", expires_in, scope }`. `access_token` is a better-auth session token. | better-auth `routes.mjs` (`deviceToken`) |
| Poll errors | All `400 { "error": ..., "error_description": ... }`: `authorization_pending`, `slow_down` (polled faster than `interval`), `expired_token` (code deleted), `access_denied` (code deleted), `invalid_grant` (unknown code, client mismatch, or client not allowed: the docs say `invalid_client`, the code returns `invalid_grant`). `500 server_error` on internal failure. | better-auth `routes.mjs` (`deviceToken`), `apps/docs/api-reference/errors.mdx` (Exceptions) |
| `slow_down` handling | Add 5 s to the interval, as `kaneo-mcp` does (RFC 8628 §3.5). | `packages/mcp/src/auth/device-flow.ts` (`pollDeviceAccessToken`) |
| Token lifetime | Session default: 7 days (`session.expiresIn` not set by Kaneo). Sliding: when the session is used and is more than 1 day old (`updateAge` default), its expiry is pushed back 7 days. An unused token expires after 7 days. | better-auth `dist/context/create-context.mjs` (`sessionConfig`), `apps/api/src/auth.ts` (`session` block only sets `cookieCache`) |
| Refresh token | None. The response has no `refresh_token`; renewal is the sliding expiry above, otherwise a new device flow. | better-auth `routes.mjs` (`deviceToken`) |
| Revocation | Password reset revokes existing sessions, device tokens included. | `apps/docs/core/installation/environment-variables.mdx` |

## 3. Workspaces

| Point | Finding | Source |
|---|---|---|
| List | `GET /workspace` → array of `{ id, name, slug, logo, description, createdAt, role }`, workspaces the caller belongs to. | `openapi.json` (`listWorkspaces`) |
| Single | `GET /workspace/{workspaceId}`. | `openapi.json` (`getWorkspace`) |

## 4. Projects

| Point | Finding | Source |
|---|---|---|
| Create | `POST /project`, JSON `{ name, workspaceId, icon, slug }`, **all four required**. Response **200** with the project (`id`, `workspaceId`, `slug`, `name`, ...). | `openapi.json` (`createProject`), `apps/api/src/project/schema.ts` |
| `slug` | The project key used in task ids (`KEY-12`). Must be unique per workspace, case-insensitive after NFKC; conflict → `409` "This workspace already has a project using the key ...". The web UI derives it from the name: uppercase, keep letters/marks/digits, first 3 characters of a single word or initials of the first 3 words. | `apps/api/src/project/controllers/project-placement.ts` (`assertProjectKeyAvailable`), `apps/api/src/project/project-key.ts`, `apps/web/src/lib/generate-project-id.ts` |
| `icon` | Name of a Lucide icon used by the UI; the UI default is `Layout`. | `apps/web/src/components/shared/modals/create-project-modal.tsx` |
| Default columns | Every new project is seeded with 4 columns: `To Do` (`to-do`), `In Progress` (`in-progress`), `In Review` (`in-review`), `Done` (`done`, `isFinal: true`). | `apps/api/src/project/controllers/insert-project.ts` (`DEFAULT_PROJECT_COLUMNS`) |
| List | `GET /project?workspaceId=...` (`includeArchived` optional). | `openapi.json` (`listProjects`) |
| Delete | `DELETE /project/{id}` → 200 with the deleted project. | `openapi.json` (`deleteProject`) |
| UI link | `<KANEO_PUBLIC_URL>/dashboard/workspace/{workspaceId}/project/{projectId}/board`. | `apps/web/src/routes/_layout/_authenticated/dashboard/workspace/$workspaceId/project/$projectId/board.tsx` |

## 5. Columns

| Point | Finding | Source |
|---|---|---|
| List | `GET /column/{projectId}` → array sorted by `position`: `{ id, projectId, name, slug, position, icon, color, isFinal, ... }`. | `openapi.json` (`getColumns`) |
| Create | `POST /column/{projectId}`, JSON `{ name, icon?, color?, isFinal? }`. Appended at the end. Response 200. | `openapi.json` (`createColumn`), `apps/api/src/column/controllers/create-column.ts` |
| Slug | Derived from the name: NFKC, lowercase, runs of non letter/mark/digit → `-`, trimmed. `"À faire"` → `à-faire`. Empty slug → `400`. Slug already in the project → `409`. `planned` and `archived` are reserved → `409`. | `apps/api/src/column/controllers/create-column.ts` (`toSlug`), `apps/api/src/task/validate-task-fields.ts` (`VIRTUAL_STATUSES`) |
| Link with tasks | A task's `status` is the slug of its column. | `openapi.json` (`createTask.status`: "The target column's slug.") |
| Delete | `DELETE /column/{id}`; `409` if the column holds tasks. | `apps/api/src/column/controllers/delete-column.ts` |
| Reorder | `PUT /column/reorder/{projectId}`, JSON `{ columns: [{ id, position }] }`. | `apps/api/src/column/schema.ts` (`reorderColumnsBody`) |

## 6. Tasks

| Point | Finding | Source |
|---|---|---|
| Create | `POST /task/{projectId}`, JSON. Required: `title`, `description` (may be `""`), `priority`, `status`. Optional: `startDate`, `dueDate`, `userId` (assignee), `customFields`, `draftAssetIds`. Response 200 with the task (`id`, `number`, `status`, `dueDate`, ...). | `openapi.json` (`createTask`), `apps/api/src/task/schema.ts` (`createTaskBody`) |
| `priority` | Enum `no-priority`, `low`, `medium`, `high`, `urgent`. | `apps/api/src/task/schema.ts`, `apps/docs/api-reference/errors.mdx` |
| `status` | Slug of an existing column of the project (or a virtual status); otherwise rejected. | `apps/api/src/task/controllers/create-task.ts` (`assertValidTaskStatus`) |
| `dueDate` format | String parsed by JavaScript `new Date()`; empty string → `400`. Stored as a timestamp, returned as ISO 8601 date-time. This module sends `YYYY-MM-DDT12:00:00.000Z`. | `apps/api/src/utils/validate-dates.ts`, `apps/api/src/task/index.ts` (`createTaskRoute`) |
| Labels on create | Not accepted by `POST /task`; attached afterwards (§7). | `apps/api/src/task/schema.ts` |
| Required custom fields | A project custom field marked required without default makes task creation fail. Not an issue for a project created by this module. | `apps/api/src/task/controllers/create-task.ts` |

## 7. Labels

Kaneo stores labels as rows `(name, color, workspaceId, taskId)`. A row with `taskId = null` is a workspace label (a definition). Attaching a label to a task creates a **separate row** bound to that task.

| Point | Finding | Source |
|---|---|---|
| List | `GET /label/workspace/{workspaceId}` → workspace labels **and** task-bound copies. Filter on `taskId == null` to get definitions. | `apps/api/src/label/controllers/get-labels-by-workspace-id.ts` |
| Create definition | `POST /label`, JSON `{ name, color, workspaceId }` (`color` required, free string; the UI uses hex). Idempotent on `(workspaceId, name)` for definitions: returns the existing one. | `apps/api/src/label/schema.ts`, `apps/api/src/label/controllers/create-label.ts` |
| Attach | `PUT /label/{id}/task`, JSON `{ taskId }`. With a definition (`taskId == null`) it inserts a copy bound to the task. **With a label already bound to another task, it moves it** (the old binding is deleted). Only definitions must be passed. | `apps/api/src/label/controllers/assign-label-to-task.ts` |
| Alternative | `POST /label` with `taskId` creates the task-bound row directly, idempotent on `(taskId, name)`. | `apps/api/src/label/controllers/create-label.ts` |

## 8. Subtasks

| Point | Finding | Source |
|---|---|---|
| Mechanism | A subtask is a normal task of the project, linked with a relation. | `openapi.json` (`createTaskRelation`) |
| Create link | `POST /task-relation`, JSON `{ sourceTaskId, targetTaskId, relationType: "subtask" }`. **`sourceTaskId` = parent, `targetTaskId` = subtask.** Duplicate relation → `409`; self relation → `400`. Other types: `blocks`, `related`. | `apps/api/src/task-relation/controllers/create-task-relation.ts`, `apps/api/src/task/get-subtask-counts.ts` (counts children by `sourceTaskId`), `apps/api/src/task/controllers/duplicate-task.ts` |
| List | `GET /task-relation/{taskId}`. | `openapi.json` (`getTaskRelations`) |
| Board | Subtasks also appear as cards in their column; parents show a `subtaskCounts` progress. | `openapi.json` (`listTasks` response) |

## 9. Errors

| Point | Finding | Source |
|---|---|---|
| Format | JSON `{ message, code, issues?, missingPermissions? }`, `Content-Type: application/json`. Branch on `code`, not on `message`. | `apps/docs/api-reference/errors.mdx`, `openapi.json` (error responses) |
| Codes | Derived from status (`BAD_REQUEST`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, ...) or specific: `VALIDATION_ERROR` (with `issues[].path` / `issues[].message`), `MISSING_PERMISSION`, `API_KEY_SCOPE`, `SESSION_REQUIRED`, `INTEGRATION_AUTH_FAILED`. | `apps/docs/api-reference/errors.mdx` |
| 401 | Only `code == "UNAUTHORIZED"` means the credential is rejected (`INTEGRATION_AUTH_FAILED` is also 401 but unrelated). | `apps/docs/api-reference/errors.mdx` |
| Retry | Do not retry 4xx other than 408 and 429. 429 carries `Retry-After`. | `apps/docs/api-reference/errors.mdx` |
| Older instances | Errors may be plain text: parse JSON only when valid, fall back to the text. | `apps/docs/api-reference/errors.mdx` (Older instances) |
| Device endpoints | RFC 8628 bodies `{ error, error_description }` (§2). | `apps/docs/api-reference/errors.mdx` (Exceptions) |
| Success status | Create endpoints answer `200`, not `201`. | `openapi.json` |
