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

## D9. `end_date.required` default

When `end_date` is present without `required`, the date is required. A template that plans tasks rarely wants them created silently without due dates. `end_date:` with no value is accepted and means the same.

## D10. `due` syntax details

- A sign is required for non-zero offsets (`7d` is rejected, `+7d` accepted); `0d`, `0b` and `J0` need none.
- The `J` alias is calendar days only (`J-7`, `J+3`, case-insensitive). Business days use `-5b`.
- Offsets are limited to 4 digits.
- Business days count from the end date whatever its weekday: from a Saturday, `+1b` is Monday and `-1b` is Friday. `0b` is the end date itself, even on a weekend (flagged in the preview).
- A YAML integer (`due: -7`) is rejected with the expected syntax.

## D11. Subtask column

A subtask without `column` goes to its parent's column.

## D12. Template files

- `.yml` and `.yaml`, hidden files ignored, 256 KiB maximum, UTF-8.
- Identifier = file name without extension, matching `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`; other names are reported as invalid. When `x.yml` and `x.yaml` both exist, the first in alphabetical order wins and the other is reported.
- Unknown keys are rejected (catches typos such as `dues:`).
- A template without tasks is valid.
- Validation messages give the path (`tasks[2].subtasks[0]`) but never the values, so that template content stays out of logs.

## D13. `SECRET_KEY`

At least 32 characters, any content. The Fernet key is derived from it with HKDF-SHA256 (D18), so users do not need to generate a Fernet-formatted key.

## D14. Creation order and stop on failure

Top-level tasks are created first, in template order, then subtasks, each one linked to its parent right after being created. Labels are attached right after their task. The creation stops at the first failed write; the report lists what was created and the failing step.

## D15. `HOST` and `PORT`

`kaneo-templates` listens on `HOST` (default `0.0.0.0`, for the container) and `PORT` (default `8000`). For a local run, set `HOST=127.0.0.1`.

## D16. CSRF before step 6

Double-submit token: a random value in an `HttpOnly`, `SameSite=Lax` cookie, repeated in a hidden form field and compared in constant time. Kept as is in step 6: it covers both modes, including `service` mode where there is no `SECRET_KEY`.

## D17. HTMX

htmx 2.0.11 is vendored in `src/kaneo_templates/web/static/htmx.min.js` (0BSD licence). Its inline indicator styles are disabled (`htmx-config` meta) so that the CSP can forbid inline styles.

## D18. User mode cookies

- `kt_session` holds `{"token": ...}` encrypted with Fernet. Key: HKDF-SHA256 of `SECRET_KEY`, info `kaneo-templates cookies`.
- Cookie and Fernet lifetime: 30 days. Kaneo sessions slide by 7 days on use, so Kaneo decides when a session ends; a `401` clears the cookie.
- Always `Secure`, as the spec requires, even when the request looks like plain HTTP (a proxy may terminate TLS without forwarding the scheme). Browsers accept `Secure` cookies on `http://localhost`.
- The pending device flow (device code, user code, interval, expiry) lives in a second encrypted cookie, `kt_device`, so the app stays stateless. The device code never reaches the page.
- The browser polls `POST /login/device/poll` with `hx-trigger="load delay:Ns"`, `N` being Kaneo's interval, raised by 5 s on `slow_down`.
- Sign-in, polling and sign-out are `POST` with the CSRF token. Success answers `HX-Refresh: true`.
- In user mode, the preview and template fields also require a session, so templates are not readable without signing in.

## D19. Stylesheet

Tailwind CSS v4, the framework of Kaneo's web UI, with Kaneo's theme tokens (`apps/web/src/index.css`): neutral palette, translucent borders, board lanes and cards, Geist. The stylesheet is compiled with the pinned standalone CLI (`scripts/build-css.sh`, version `v4.3.3`, fetched through the `pytailwindcss` dev dependency) and committed, so the image needs no Node and the app no front-end build at run time. CI rebuilds it and fails if the committed file differs. Geist (latin and latin-ext subsets) is served locally because the CSP forbids external fonts.

## D20. Board preview

The preview shows the board as it will be created: one lane per column, cards placed with the same rule as the creation (`plan_board` mirrors `Creator._column_for`), numbered in creation order (top-level tasks, then subtasks) with the key derived from the project name. That key may end up with a suffix when it is already taken in the workspace.

## D21. Public holidays

`HOLIDAYS` selects a calendar computed locally (no dependency, no network): `fr` (the 11 national holidays) or `fr-alsace-moselle` (plus Good Friday and 26 December). Easter uses the anonymous Gregorian algorithm. Holidays are skipped by business-day offsets only; calendar-day offsets and `0b` may land on a holiday, which the preview flags with the holiday name.
