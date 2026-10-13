# Deployment

kaneo-templates is one container with no database. It needs network access to the Kaneo API and a folder of templates.

## Security first

In `AUTH_MODE=service` the app does not authenticate its own users: **anyone who can open it creates projects with the service account**. Put it behind a reverse proxy that authenticates (forward auth, SSO, VPN…) or set `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD`.

In `AUTH_MODE=user` every user signs in with their own Kaneo account and projects are created in their name. The session cookie is `Secure`: serve the app over HTTPS (browsers accept it on `http://localhost` for testing).

## With Docker Compose

```sh
mkdir kaneo-templates && cd kaneo-templates
curl -O https://raw.githubusercontent.com/Matyu9/Kaneo-Create-Extension/main/docker-compose.example.yml
curl -o .env https://raw.githubusercontent.com/Matyu9/Kaneo-Create-Extension/main/.env.example
mv docker-compose.example.yml docker-compose.yml
mkdir templates   # put your .yml templates here
# edit .env, then:
docker compose up -d
```

The port is bound to `127.0.0.1:8000` by default, for a reverse proxy on the same host. Set `BIND_IP` and `HOST_PORT` in the shell or in a Compose `.env` to change it.

The container runs as UID 10001 with a read-only file system and no capability. The templates folder only needs to be readable by that user.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `KANEO_BASE_URL` | required | Kaneo API URL, ending with `/api`, e.g. `https://kaneo.example.com/api` |
| `KANEO_PUBLIC_URL` | `KANEO_BASE_URL` without `/api` | Kaneo web URL, for the links to created projects |
| `AUTH_MODE` | `service` | `service` or `user` |
| `KANEO_TOKEN` | — | Kaneo API key, required in `service` mode |
| `SECRET_KEY` | — | Required in `user` mode, at least 32 characters. Generate one with `openssl rand -base64 48`. Changing it signs everyone out. |
| `DEVICE_CLIENT_ID` | `kaneo-templates` | Client id sent to Kaneo's device sign-in |
| `KANEO_WORKSPACE_ID` | — | Workspace selected by default |
| `TEMPLATES_DIR` | `/data/templates` | Templates folder |
| `APP_LANG` | `fr` | `fr` or `en` |
| `ROOT_PATH` | — | URL prefix when served under a sub-path, e.g. `/templates` |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | — | Optional HTTP Basic protection, both or neither |
| `HOLIDAYS` | — | Public holidays skipped by business-day offsets (`-5b`): `fr` or `fr-alsace-moselle` |
| `LOG_LEVEL` | `info` | `critical`, `error`, `warning`, `info` or `debug` |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Listening address |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | Proxies whose `X-Forwarded-*` headers are trusted (read by uvicorn) |

The app refuses to start, with the list of problems, when the configuration is invalid.

### Service mode

Create an API key in Kaneo (Settings → Account → API Keys) for the account that will own the projects, and set it as `KANEO_TOKEN`. API keys are limited by Kaneo to 100 requests per minute; the app waits and resumes when the limit is hit.

### User mode

Users sign in with Kaneo's device flow: the app shows a code, the user approves it on Kaneo's `/device` page, and the app receives a session token. A Kaneo API key can also be pasted instead.

On the Kaneo server, add the app's client id to `DEVICE_AUTH_CLIENT_IDS`, **keeping the defaults** since the variable replaces them:

```
DEVICE_AUTH_CLIENT_IDS=kaneo-cli,kaneo-mcp,kaneo-templates
```

Without it, the sign-in button answers that the client is not allowed; pasting a token still works.

Sessions last 7 days and are extended each time they are used. When Kaneo rejects a token, the cookie is cleared and the user is sent back to the sign-in page.

## Reverse proxy

The app reads `X-Forwarded-Proto` and `X-Forwarded-For` from the proxy, but only from the addresses listed in `FORWARDED_ALLOW_IPS` (default `127.0.0.1,::1`). When the proxy reaches the container over a Docker network, set it to the proxy's address or network, for example `FORWARDED_ALLOW_IPS=172.18.0.0/16`. Every URL in the pages is relative, so the app also works under a sub-path once `ROOT_PATH` is set and the proxy strips the prefix.

### Caddy

```caddy
templates.example.com {
	reverse_proxy 127.0.0.1:8000
}
```

Under a sub-path (`ROOT_PATH=/templates`):

```caddy
kaneo.example.com {
	redir /templates /templates/
	handle_path /templates/* {
		reverse_proxy 127.0.0.1:8000
	}
	reverse_proxy 127.0.0.1:1337   # Kaneo
}
```

### nginx

```nginx
server {
    listen 443 ssl;
    server_name templates.example.com;
    # ssl_certificate ...

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Traefik (labels)

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.kaneo-templates.rule=Host(`templates.example.com`)
  - traefik.http.routers.kaneo-templates.entrypoints=websecure
  - traefik.http.routers.kaneo-templates.tls=true
  - traefik.http.services.kaneo-templates.loadbalancer.server.port=8000
  # Service mode: add your authentication middleware, for example
  # - traefik.http.routers.kaneo-templates.middlewares=authelia@docker
```

## Health and logs

`GET /healthz` answers `{"status": "ok"}` without authentication; the image uses it as its Docker health check. Logs go to stdout. They never contain tokens or template contents; each creation logs one line with the template, the project key and the counts.

## Updating

```sh
docker compose pull && docker compose up -d
```

Images are published on GHCR for every version tag (`1.2.3`, `1.2`, `latest`).
