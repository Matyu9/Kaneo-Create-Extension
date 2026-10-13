#!/bin/sh
# Rebuild src/kaneo_templates/web/static/app.css from web/tailwind.css and the templates.
# The output is committed, so the app needs no front-end build at run time.
# Usage: scripts/build-css.sh [--watch]
set -eu
cd "$(dirname "$0")/.."
export TAILWINDCSS_VERSION="${TAILWINDCSS_VERSION:-v4.3.3}"
exec uv run tailwindcss \
  --input src/kaneo_templates/web/tailwind.css \
  --output src/kaneo_templates/web/static/app.css \
  --minify "$@"
