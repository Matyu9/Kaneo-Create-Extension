# Template format

A template is a YAML file in `TEMPLATES_DIR`. Its identifier is the file name without the extension. Templates are read again on every page load: adding or editing a file needs no restart.

Invalid files are skipped, logged as a warning and listed at the top of the page with the reason (the path of the faulty field, never its value).

## Example

```yaml
version: 1
name: Event
description: Tasks scheduled around the event date
end_date:
  required: true
  label: Event date
columns:
  - To do
  - In progress
  - Done
labels:
  logistics: "#f59e0b"
tasks:
  - title: Book the room
    due: -30d
    priority: high
    column: To do
    description: Check capacity and access
    labels: [logistics]
  - title: Send the invitations
    due: -7d
    priority: medium
  - title: Prepare the agenda
    due: -5b
  - title: Write the minutes
    due: +3d
    subtasks:
      - title: Proofread
        due: +5d
      - title: Send
  - title: Debrief with the team
```

More examples in [`templates-examples/`](../templates-examples).

## Fields

| Field | Required | Description |
|---|---|---|
| `version` | yes | Always `1`. Another value is rejected. |
| `name` | yes | Shown in the template list. |
| `description` | no | Shown under the template list. |
| `end_date` | no | The date the `due` offsets are computed from. See below. |
| `start_date` | no | A second date, for offsets written `start+2d`. Same keys as `end_date`. |
| `columns` | no | Board columns, in order. See below. |
| `labels` | no | Colors of the labels the template creates. See below. |
| `tasks` | no | The tasks, in creation order. A template without tasks is valid. |

### `end_date`

| Key | Default | Description |
|---|---|---|
| `required` | `true` | `true`: the date must be entered. `false`: without a date, tasks are created without due date. |
| `label` | "End date" | Label of the date field. |

`end_date:` with no value means `required: true`. Without `end_date`, no task may have a `due` relative to the end date.

### `start_date`

Same keys as `end_date` (`required`, default `true`, and `label`, default "Start date"). It adds a start date field to the form; offsets written `start…` are computed from it:

```yaml
start_date:
  label: First rehearsal
end_date:
  label: Concert
tasks:
  - title: Hand out the scores
    due: start
  - title: First full run
    due: start+10b
  - title: Dress rehearsal
    due: -2d
```

The preview warns when the start date is after the end date.

### `columns`

- **Absent**: the project keeps the four columns Kaneo creates (`To Do`, `In Progress`, `In Review`, `Done`).
- **Present**: the template columns replace them, in the template order. The last one is the final column (tasks moved there count as done).

Each name must give a non-empty Kaneo slug (lowercase, other characters than letters and digits become `-`), distinct from the others and other than `planned` or `archived`, which Kaneo reserves.

### `labels`

A mapping from label name to a hex color (`#rgb` or `#rrggbb`):

```yaml
labels:
  logistics: "#f59e0b"
  governance: "#8b5cf6"
```

Quote the colors: `#` starts a comment in YAML. A label missing from this section is created grey (`#6b7280`). Labels that already exist in the workspace keep their color. Listing a label here does not create it: only the labels used by tasks are created.

### Tasks and subtasks

| Key | Default | Description |
|---|---|---|
| `title` | required | Task title. Duplicate titles are allowed. |
| `description` | empty | Plain text. |
| `priority` | `no-priority` | `no-priority`, `low`, `medium`, `high` or `urgent`. |
| `column` | first column | When the template defines `columns`, must be one of them. Otherwise matched by name or slug against the project columns; an unknown column puts the task in the first column with a warning. |
| `labels` | none | Workspace label names. Missing labels are created, with the color from the `labels` section or grey. |
| `due` | no due date | Offset from the end date, see below. |
| `subtasks` | none | Same keys, except `subtasks` (one level only). A subtask without `column` goes to its parent's column. |

Unknown keys are rejected, to catch typos such as `dues:`.

## Variables

Titles and descriptions may contain placeholders, replaced when the project is created:

| Placeholder | Replaced by |
|---|---|
| `{{project_name}}` | The project name entered in the form |
| `{{end_date}}` | The end date (`24/03/2027` in French, `2027-03-24` in English), or nothing when no date was entered |
| `{{start_date}}` | The start date, in the same format |

```yaml
tasks:
  - title: "Write the minutes of {{project_name}}"
    description: "Meeting of {{end_date}}"
```

Spaces inside the braces are allowed (`{{ end_date }}`). Any other name is rejected when the template loads; `{{end_date}}` and `{{start_date}}` require the matching section. Quote titles that start with `{`: YAML reads them as mappings otherwise.

## `due` syntax

| Value | Meaning |
|---|---|
| `-7d` / `+3d` | 7 calendar days before / 3 after the end date |
| `0d` | the end date itself |
| `-5b` / `+2b` | 5 business days before / 2 after (Monday to Friday, minus the public holidays of `HOLIDAYS` when set) |
| `J-7` / `J+3` / `J0` | aliases of `-7d` / `+3d` / `0d` |
| `start` / `start+2d` / `start-1b` | the start date / 2 calendar days after it / 1 business day before it (needs `start_date`) |

- A sign is required for non-zero offsets: `7d` is rejected.
- Offsets have at most 4 digits.
- Business days count from the end date whatever its weekday: from a Saturday, `+1b` is Monday and `-1b` is Friday.
- A YAML number (`due: -7`) is rejected: write `-7d`.

The preview flags due dates that fall on a weekend, on a public holiday or in the past; neither blocks the creation. Due dates are sent to Kaneo at noon UTC so that they show on the same day in every time zone.

## Checking templates

`kaneo-templates check` validates a folder without starting the app or calling Kaneo, and exits with status 1 when a template is invalid, which suits a CI job or a pre-commit hook:

```console
$ docker run --rm -v ./templates:/data/templates:ro ghcr.io/matyu9/kaneo-create-extension check
ok     evenement: 5 tasks, 2 subtasks
error  reunion: tasks[3].due: invalid due offset: expected forms like -7d, +3d, 0d, -5b, J-7, J+3
```

The folder defaults to `TEMPLATES_DIR`; pass another one as argument (`kaneo-templates check ./templates`).

## Files

- Extensions `.yml` and `.yaml`. Hidden files are ignored.
- 256 KiB at most, UTF-8.
- File name (without extension): letters, digits, `-` and `_`, starting with a letter or digit, 64 characters at most.
- When `x.yml` and `x.yaml` both exist, the first in alphabetical order is used and the other is reported.
