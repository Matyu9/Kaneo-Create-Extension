# kaneo-templates

Create a [Kaneo](https://github.com/usekaneo/kaneo) project with its predefined tasks from a YAML template, with due dates computed relative to a project end date.

Work in progress. See [SPEC.md](SPEC.md) for the specification (in French) and [docs/kaneo-api-notes.md](docs/kaneo-api-notes.md) for the Kaneo API details this module relies on.

## Development

```sh
uv sync
uv run ruff check
uv run ruff format --check
uv run pytest
```

## License

MIT
