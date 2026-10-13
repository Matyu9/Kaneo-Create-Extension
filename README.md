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

## Local test

1. Create a `.env` file outside the repository (or anywhere ignored by git) with:

   ```
   KANEO_BASE_URL=https://kaneo.example.com/api
   KANEO_TOKEN=your-api-key
   TEMPLATES_DIR=templates-examples
   HOST=127.0.0.1
   PORT=8000
   ```

2. Start the app:

   ```sh
   uv run --env-file path/to/.env kaneo-templates
   ```

3. Open http://127.0.0.1:8000. Every click on "Create project" creates a real project in Kaneo.

Only `AUTH_MODE=service` is available at this stage.

## License

MIT
