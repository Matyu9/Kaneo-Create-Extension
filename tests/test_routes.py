import base64
import json
import re
import shutil
import time
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kaneo_templates import auth
from kaneo_templates.config import load_settings
from kaneo_templates.kaneo_client import KaneoClient
from kaneo_templates.main import create_app

BASE = "https://kaneo.test/api"
EXAMPLES = Path(__file__).parent.parent / "templates-examples"
WORKSPACES = [{"id": "w1", "name": "Main"}, {"id": "w2", "name": "Other"}]
SECRET = "s" * 40
SERVICE_TOKEN = "svc-token-123"
USER_ENV = {"AUTH_MODE": "user", "SECRET_KEY": SECRET, "KANEO_TOKEN": ""}


@pytest.fixture
def templates_dir(tmp_path):
    for name in ("evenement.yml", "onboarding.yml"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    (tmp_path / "broken.yml").write_text("version: 1\nname: [\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def kaneo():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/workspace").respond(json=WORKSPACES)
        yield mock


def make_client(templates_dir, **env):
    settings = load_settings(
        {
            "KANEO_BASE_URL": BASE,
            "KANEO_TOKEN": SERVICE_TOKEN,
            "TEMPLATES_DIR": str(templates_dir),
            **env,
        }
    )
    app = create_app(settings)
    app.state.client_factory = lambda s, token: KaneoClient(s.kaneo_base_url, token)
    # https so that the Secure session cookies of the user mode are sent back.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client(templates_dir, kaneo):
    return make_client(templates_dir)


@pytest.fixture
def user_client(templates_dir, kaneo):
    return make_client(templates_dir, **USER_ENV)


def csrf(client):
    client.get("/")
    return client.cookies.get(auth.CSRF_COOKIE)


def sign_in(client, token="user-tok"):
    client.cookies.set(auth.SESSION_COOKIE, client.app.state.cipher.seal({"token": token}))


def set_cookie_headers(response):
    return response.headers.get_list("set-cookie")


def cookie_header(response, name):
    return next((h for h in set_cookie_headers(response) if h.startswith(f"{name}=")), None)


def is_cleared(response, name):
    header = cookie_header(response, name)
    return header is not None and ("Max-Age=0" in header or 'Max-Age="0"' in header)


def auth_headers(mock):
    return [c.request.headers.get("Authorization") for c in mock.calls]


def board_columns(html):
    """Map each preview column to the titles of its cards, in order."""
    columns = {}
    for name, body in re.findall(r'data-column="([^"]*)">(.*?)</ol>', html, flags=re.S):
        columns[name] = re.findall(r"text-board-title\">([^<]*)</p>", body)
    return columns


# Service mode: page and static files


def test_index_lists_workspaces_and_templates(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Main" in html
    assert "Organisation d&#39;un événement" in html
    assert "broken" in html
    assert 'src="static/htmx.min.js"' in html
    assert auth.CSRF_COOKIE in response.cookies
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_index_never_leaks_service_token(client):
    assert SERVICE_TOKEN not in client.get("/").text


def test_index_links_to_kaneo(client):
    assert 'href="https://kaneo.test"' in client.get("/").text


def test_index_without_sign_out_in_service_mode(client):
    assert 'hx-post="logout"' not in client.get("/").text


@pytest.mark.parametrize(
    "path, marker",
    [
        ("/static/htmx.min.js", "htmx"),
        ("/static/app.css", "tailwindcss"),
        ("/static/favicon.svg", "<svg"),
    ],
)
def test_static_files_served_locally(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    assert marker in response.text[:400]


def test_fonts_served_locally(client):
    response = client.get("/static/fonts/geist-latin-wght-normal.woff2")
    assert response.status_code == 200
    assert response.content[:4] == b"wOF2"


def test_page_uses_no_external_resource(client):
    html = client.get("/").text
    assert not re.search(r'(src|href)="(https?:)?//(?!kaneo\.test)', html)


def test_compiled_css_has_kaneo_theme(client):
    css = client.get("/static/app.css").text
    assert "Geist Variable" in css
    assert "prefers-color-scheme:dark" in css.replace(" ", "")
    for cls in (".btn-primary", ".input", ".card", ".chip", ".bg-board-lane"):
        assert cls in css, cls


def test_healthz_is_public(templates_dir, kaneo):
    client = make_client(templates_dir, BASIC_AUTH_USER="a", BASIC_AUTH_PASSWORD="b")
    assert client.get("/healthz").json() == {"status": "ok"}


def test_workspace_preselected(templates_dir, kaneo):
    html = make_client(templates_dir, KANEO_WORKSPACE_ID="w2").get("/").text
    assert '<option value="w2" selected>' in html


def test_token_rejected_message(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").respond(401, json={"message": "no", "code": "UNAUTHORIZED"})
        html = make_client(templates_dir).get("/").text
    assert "KANEO_TOKEN" in html


def test_kaneo_unreachable(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").mock(side_effect=httpx.ConnectError("down"))
        html = make_client(templates_dir).get("/").text
    assert "Impossible de lire les workspaces" in html


def test_no_valid_template(tmp_path, kaneo):
    html = make_client(tmp_path).get("/").text
    assert "Aucun modèle valide" in html
    assert 'id="create-form"' not in html


def test_english_interface(templates_dir, kaneo):
    html = make_client(templates_dir, APP_LANG="en").get("/").text
    assert '<html lang="en">' in html
    assert "Create project" in html


def test_root_path_uses_relative_urls(templates_dir, kaneo):
    client = make_client(templates_dir, ROOT_PATH="/tools")
    response = client.get("/")
    html = response.text
    assert 'hx-get="preview"' in html
    assert 'href="static/app.css"' in html
    assert "Path=/tools" in cookie_header(response, auth.CSRF_COOKIE)


# Preview


def test_preview(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-14"}).text
    assert "15/05/2030" in html
    assert "Relire le brouillon" in html
    assert "À faire" in html


def test_preview_shows_weekday(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-14"}).text
    # 2030-05-15 is a Wednesday.
    assert "mer. 15/05/2030" in html


def test_preview_weekday_in_english(templates_dir, kaneo):
    client = make_client(templates_dir, APP_LANG="en")
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-14"}).text
    assert "Wed 2030-05-15" in html


def test_preview_weekend_flag(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-12"}).text
    assert "week-end" in html


def test_preview_past_dates(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2000-01-10"}).text
    assert "date passée" in html
    assert "Certaines échéances sont dans le passé" in html


def test_preview_without_optional_end_date(client):
    html = client.get("/preview", params={"template": "onboarding"}).text
    assert "Pas de date de fin" in html
    assert "sans échéance" in html


def test_preview_invalid_date_is_ignored(client):
    response = client.get("/preview", params={"template": "evenement", "end_date": "nope"})
    assert response.status_code == 200
    assert "sans échéance" in response.text


def test_preview_board_places_cards_in_columns(client):
    html = client.get("/preview", params={"template": "onboarding", "end_date": "2030-06-14"}).text
    columns = board_columns(html)
    assert list(columns) == ["Avant l&#39;arrivée", "Première semaine", "Fait"]
    assert columns["Avant l&#39;arrivée"] == [
        "Préparer le poste de travail",
        "Créer les comptes",
        "Entretien de suivi",
        "Messagerie",
        "Accès aux outils de l&#39;équipe",
    ]
    assert columns["Première semaine"] == [
        "Présenter l&#39;équipe",
        "Point de fin de première semaine",
    ]
    assert columns["Fait"] == []
    assert "Aucune tâche" in html


def test_preview_shows_subtask_parent_and_count(client):
    html = client.get("/preview", params={"template": "onboarding"}).text
    assert "Sous-tâche de « Créer les comptes »" in html
    assert "0/2" in html


def test_preview_numbers_cards_with_project_key(client):
    params = {"template": "evenement", "end_date": "2030-06-14", "name": "Concert de printemps"}
    html = client.get("/preview", params=params).text
    assert "CDP-1" in html
    # Subtasks are created after every top-level task.
    assert re.search(r"CDP-6</span>.*?Relire le brouillon", html, flags=re.S)


def test_preview_numbers_without_name(client):
    html = client.get("/preview", params={"template": "evenement"}).text
    assert "#-1" in html


def test_preview_renders_variables(client, templates_dir):
    (templates_dir / "vars.yml").write_text(
        "version: 1\nname: V\nend_date:\ntasks:\n  - title: 'Bilan {{project_name}}'\n"
        "    description: 'Avant le {{end_date}}'\n"
    )
    params = {"template": "vars", "name": "Gala <b>", "end_date": "2027-03-24"}
    html = client.get("/preview", params=params).text
    assert "Bilan Gala &lt;b&gt;" in html
    assert "Avant le 24/03/2027" in html


def test_create_renders_variables(client, kaneo, templates_dir):
    route = mock_simple_creation(kaneo)
    (templates_dir / "vars.yml").write_text(
        "version: 1\nname: V\ntasks:\n  - title: 'Bilan {{project_name}}'\n"
    )
    data = {"csrf_token": csrf(client), "workspace_id": "w1", "template": "vars", "name": " Gala "}
    assert "Projet créé" in client.post("/create", data=data).text
    assert json.loads(route.calls.last.request.content)["title"] == "Bilan Gala"


def test_preview_label_colors(client, templates_dir):
    html = client.get("/preview", params={"template": "evenement"}).text
    assert 'fill="#f59e0b"' in html
    (templates_dir / "grey.yml").write_text(
        "version: 1\nname: G\ntasks:\n  - title: A\n    labels: [plain]\n"
    )
    assert 'fill="#6b7280"' in client.get("/preview", params={"template": "grey"}).text


def test_preview_priority_icons(client):
    html = client.get("/preview", params={"template": "evenement"}).text
    assert "Priorité : Haute" in html
    assert "Priorité : Moyenne" in html


def test_preview_kaneo_default_columns(client, templates_dir):
    (templates_dir / "plain.yml").write_text(
        "version: 1\nname: Plain\ntasks:\n  - title: A\n    column: In Review\n"
        "  - title: B\n    column: nowhere\n"
    )
    html = client.get("/preview", params={"template": "plain"}).text
    columns = board_columns(html)
    assert list(columns) == ["To Do", "In Progress", "In Review", "Done"]
    assert columns["In Review"] == ["A"]
    assert columns["To Do"] == ["B"]
    assert "colonne introuvable" in html
    assert "colonnes par défaut de Kaneo" in html


def test_preview_unknown_or_traversal_template(client):
    for template in ["../evenement", "evenement.yml", "/etc/passwd", "nope", ""]:
        response = client.get("/preview", params={"template": template})
        assert response.status_code == 200
        assert "Réserver la salle" not in response.text


def test_preview_escapes_template_content(client, templates_dir):
    (templates_dir / "xss.yml").write_text(
        'version: 1\nname: X\ntasks:\n  - title: "<script>alert(1)</script>"\n'
    )
    html = client.get("/preview", params={"template": "xss"}).text
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_template_fields(client):
    response = client.get("/template-fields", params={"template": "onboarding"})
    assert "Date d&#39;arrivée" in response.text
    assert "facultatif" in response.text
    assert response.headers["HX-Trigger"] == "refresh-preview"


def test_template_fields_required_date(client):
    html = client.get("/template-fields", params={"template": "evenement"}).text
    assert "obligatoire" in html
    assert "required" in html


def test_template_fields_unknown_template(client):
    response = client.get("/template-fields", params={"template": "../x"})
    assert response.status_code == 200
    assert "end_date" not in response.text


# Service mode: creation


def test_create_requires_csrf(client, kaneo):
    form = {"workspace_id": "w1", "template": "evenement", "name": "X", "end_date": "2030-06-14"}
    response = client.post("/create", data={**form, "csrf_token": "forged"})
    assert "Session expirée" in response.text
    assert not kaneo.calls or all(c.request.method == "GET" for c in kaneo.calls)


def test_create_requires_csrf_cookie(client, kaneo):
    form = {"workspace_id": "w1", "template": "evenement", "name": "X", "csrf_token": ""}
    assert "Session expirée" in client.post("/create", data=form).text


def test_create_validates_inputs(client, kaneo):
    token = csrf(client)
    response = client.post(
        "/create",
        data={"csrf_token": token, "workspace_id": "w9", "template": "../evenement", "name": " "},
    )
    text = response.text
    assert "Choisissez un workspace" in text
    assert "Modèle inconnu" in text
    assert "nom du projet est obligatoire" in text
    assert all(c.request.method == "GET" for c in kaneo.calls)


def test_create_rejects_too_long_name(client, kaneo):
    token = csrf(client)
    data = {"csrf_token": token, "workspace_id": "w1", "template": "onboarding", "name": "x" * 201}
    assert "nom du projet est obligatoire" in client.post("/create", data=data).text


def test_create_requires_end_date(client, kaneo):
    token = csrf(client)
    response = client.post(
        "/create",
        data={"csrf_token": token, "workspace_id": "w1", "template": "evenement", "name": "X"},
    )
    assert "date de fin est obligatoire" in response.text


def test_create_rejects_bad_date(client, kaneo):
    token = csrf(client)
    data = {
        "csrf_token": token,
        "workspace_id": "w1",
        "template": "onboarding",
        "name": "X",
        "end_date": "31/12/2030",
    }
    assert "Date de fin invalide" in client.post("/create", data=data).text
    assert all(c.request.method == "GET" for c in kaneo.calls)


def mock_simple_creation(kaneo):
    kaneo.get("/project").respond(json=[])
    kaneo.post("/project").respond(json={"id": "p1", "workspaceId": "w1", "name": "X", "slug": "X"})
    kaneo.get("/column/p1").respond(
        json=[{"id": "c1", "name": "To Do", "slug": "to-do", "position": 0, "isFinal": False}]
    )
    return kaneo.post("/task/p1").respond(json={"id": "t1", "title": "A", "status": "to-do"})


def write_simple(templates_dir):
    (templates_dir / "simple.yml").write_text("version: 1\nname: Simple\ntasks:\n  - title: A\n")


def test_create_runs_creator(client, kaneo, templates_dir):
    mock_simple_creation(kaneo)
    write_simple(templates_dir)
    token = csrf(client)
    response = client.post(
        "/create",
        data={"csrf_token": token, "workspace_id": "w1", "template": "simple", "name": "X"},
    )
    assert "Projet créé" in response.text
    assert "https://kaneo.test/dashboard/workspace/w1/project/p1/board" in response.text
    assert "1 tâches créées" in response.text
    assert "project" not in response.text.split("Projet créé")[1].split("</h2>")[0]
    assert set(auth_headers(kaneo)) == {f"Bearer {SERVICE_TOKEN}"}


def test_create_report_counts_columns_and_labels(client, kaneo, templates_dir):
    mock_simple_creation(kaneo)
    kaneo.post("/column/p1").respond(
        json={"id": "c2", "name": "Done", "slug": "done", "position": 1, "isFinal": True}
    )
    kaneo.put("/column/c1").respond(
        json={"id": "c1", "name": "Todo", "slug": "to-do", "position": 0, "isFinal": False}
    )
    kaneo.put("/column/reorder/p1").respond(json={})
    kaneo.get("/label/workspace/w1").respond(json=[])
    kaneo.post("/label").respond(json={"id": "l1", "name": "x", "color": "#6b7280"})
    kaneo.put("/label/l1/task").respond(
        json={"id": "l2", "name": "x", "color": "#6b7280", "taskId": "t1"}
    )
    (templates_dir / "cols.yml").write_text(
        "version: 1\nname: Cols\ncolumns: [To Do, Done]\ntasks:\n  - title: A\n    labels: [x]\n"
    )
    data = {"csrf_token": csrf(client), "workspace_id": "w1", "template": "cols", "name": "X"}
    text = client.post("/create", data=data).text
    assert "Projet créé" in text
    assert "2 colonnes configurées · 1 labels créés" in text


def test_create_partial_failure_report(client, kaneo, templates_dir):
    mock_simple_creation(kaneo).respond(500, json={"message": "boom"})
    write_simple(templates_dir)
    token = csrf(client)
    data = {"csrf_token": token, "workspace_id": "w1", "template": "simple", "name": "X"}
    text = client.post("/create", data=data).text
    assert "Création interrompue" in text
    assert "boom" in text
    assert "Ouvrir dans Kaneo" in text


def test_create_token_rejected(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/workspace").respond(json=WORKSPACES)
        client = make_client(templates_dir)
        token = csrf(client)
        route.respond(401, json={"message": "no"})
        data = {"csrf_token": token, "workspace_id": "w1", "template": "onboarding", "name": "X"}
        assert "KANEO_TOKEN" in client.post("/create", data=data).text


def test_create_kaneo_unreachable(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/workspace").respond(json=WORKSPACES)
        client = make_client(templates_dir)
        token = csrf(client)
        route.mock(side_effect=httpx.ConnectError("down"))
        data = {"csrf_token": token, "workspace_id": "w1", "template": "onboarding", "name": "X"}
        assert "Impossible de lire les workspaces" in client.post("/create", data=data).text


# Basic Auth


def test_basic_auth(templates_dir, kaneo):
    client = make_client(templates_dir, BASIC_AUTH_USER="admin", BASIC_AUTH_PASSWORD="s3cret")
    assert client.get("/").status_code == 401
    assert client.get("/healthz").status_code == 200
    bad = base64.b64encode(b"admin:wrong").decode()
    assert client.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"admin:s3cret").decode()
    response = client.get("/", headers={"Authorization": f"Basic {good}"})
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"


def test_basic_auth_challenge_and_headers(templates_dir, kaneo):
    client = make_client(templates_dir, BASIC_AUTH_USER="admin", BASIC_AUTH_PASSWORD="s3cret")
    response = client.get("/preview")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_basic_auth_protects_static_and_post(templates_dir, kaneo):
    client = make_client(templates_dir, BASIC_AUTH_USER="admin", BASIC_AUTH_PASSWORD="s3cret")
    assert client.get("/static/app.css").status_code == 401
    assert client.post("/create").status_code == 401


def test_service_mode_without_basic_auth_is_open(client):
    assert client.get("/").status_code == 200


@pytest.mark.parametrize("path", ["/login/token", "/login/device", "/login/device/poll", "/logout"])
def test_sign_in_routes_absent_in_service_mode(client, path):
    assert client.post(path, data={"csrf_token": csrf(client)}).status_code == 404


# User mode: pages


def test_user_mode_without_cookie_shows_sign_in(user_client, kaneo):
    response = user_client.get("/")
    html = response.text
    assert "Connexion à Kaneo" in html
    assert 'hx-post="login/device"' in html
    assert 'hx-post="login/token"' in html
    assert not kaneo.calls
    assert auth.CSRF_COOKIE in response.cookies


def test_user_mode_tampered_cookie_shows_sign_in(user_client, kaneo):
    user_client.cookies.set(auth.SESSION_COOKIE, "garbage")
    assert "Connexion à Kaneo" in user_client.get("/").text
    assert not kaneo.calls


def test_user_mode_cookie_from_other_secret_is_rejected(user_client, kaneo):
    other = auth.CookieCipher("o" * 40)
    user_client.cookies.set(auth.SESSION_COOKIE, other.seal({"token": "x"}))
    assert "Connexion à Kaneo" in user_client.get("/").text


def test_user_mode_signed_in_uses_user_token(user_client, kaneo):
    sign_in(user_client)
    html = user_client.get("/").text
    assert "Main" in html
    assert 'hx-post="logout"' in html
    assert auth_headers(kaneo) == ["Bearer user-tok"]
    assert "user-tok" not in html


def test_user_mode_expired_session_clears_cookie(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").respond(401, json={"message": "expired"})
        client = make_client(templates_dir, **USER_ENV)
        sign_in(client)
        response = client.get("/")
    assert "Votre session Kaneo a expiré" in response.text
    assert is_cleared(response, auth.SESSION_COOKIE)


@pytest.mark.parametrize("path", ["/preview", "/template-fields"])
def test_user_mode_fragments_require_sign_in(user_client, path):
    response = user_client.get(path, params={"template": "evenement"})
    assert response.headers["HX-Refresh"] == "true"
    assert "Réserver" not in response.text


def test_user_mode_preview_when_signed_in(user_client):
    sign_in(user_client)
    assert "Réserver la salle" in user_client.get("/preview", params={"template": "evenement"}).text


# User mode: creation


def test_user_mode_create_without_session_reloads(user_client, kaneo):
    token = csrf(user_client)
    data = {"csrf_token": token, "workspace_id": "w1", "template": "onboarding", "name": "X"}
    response = user_client.post("/create", data=data)
    assert response.headers["HX-Refresh"] == "true"
    assert not kaneo.calls


def test_user_mode_create_uses_user_token(user_client, kaneo, templates_dir):
    mock_simple_creation(kaneo)
    write_simple(templates_dir)
    sign_in(user_client, "alice")
    token = csrf(user_client)
    data = {"csrf_token": token, "workspace_id": "w1", "template": "simple", "name": "X"}
    assert "Projet créé" in user_client.post("/create", data=data).text
    assert set(auth_headers(kaneo)) == {"Bearer alice"}


def test_user_mode_create_with_expired_session(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        route = mock.get("/workspace").respond(json=WORKSPACES)
        client = make_client(templates_dir, **USER_ENV)
        sign_in(client)
        token = csrf(client)
        route.respond(401, json={"message": "expired"})
        data = {"csrf_token": token, "workspace_id": "w1", "template": "onboarding", "name": "X"}
        response = client.post("/create", data=data)
    assert response.headers["HX-Refresh"] == "true"
    assert is_cleared(response, auth.SESSION_COOKIE)


def test_user_mode_session_lost_during_creation(user_client, kaneo, templates_dir):
    mock_simple_creation(kaneo).respond(401, json={"message": "expired"})
    write_simple(templates_dir)
    sign_in(user_client)
    token = csrf(user_client)
    data = {"csrf_token": token, "workspace_id": "w1", "template": "simple", "name": "X"}
    response = user_client.post("/create", data=data)
    assert "Création interrompue" in response.text
    assert "Votre session Kaneo a expiré" in response.text
    assert is_cleared(response, auth.SESSION_COOKIE)


# User mode: pasted token


def test_login_token_requires_csrf(user_client, kaneo):
    response = user_client.post("/login/token", data={"csrf_token": "x", "token": "t"})
    assert "Session expirée" in response.text
    assert not kaneo.calls


def test_login_token_empty(user_client, kaneo):
    response = user_client.post(
        "/login/token", data={"csrf_token": csrf(user_client), "token": " "}
    )
    assert "Saisissez un token" in response.text
    assert not kaneo.calls


def test_login_token_too_long(user_client, kaneo):
    data = {"csrf_token": csrf(user_client), "token": "x" * 5000}
    assert "Saisissez un token" in user_client.post("/login/token", data=data).text


def test_login_token_rejected(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").respond(401, json={"message": "bad"})
        client = make_client(templates_dir, **USER_ENV)
        response = client.post("/login/token", data={"csrf_token": csrf(client), "token": "bad"})
    assert "Kaneo a refusé ce token" in response.text
    assert auth.SESSION_COOKIE not in response.cookies


def test_login_token_kaneo_down(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").mock(side_effect=httpx.ConnectError("down"))
        client = make_client(templates_dir, **USER_ENV)
        response = client.post("/login/token", data={"csrf_token": csrf(client), "token": "t"})
    assert "Kaneo a renvoyé une erreur" in response.text
    assert "cannot reach Kaneo" in response.text


def test_login_token_success_sets_secure_cookie(user_client, kaneo):
    response = user_client.post(
        "/login/token", data={"csrf_token": csrf(user_client), "token": " api-key "}
    )
    assert response.headers["HX-Refresh"] == "true"
    assert auth_headers(kaneo)[-1] == "Bearer api-key"
    header = cookie_header(response, auth.SESSION_COOKIE)
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header
    assert "api-key" not in header
    sealed = response.cookies[auth.SESSION_COOKIE]
    assert user_client.app.state.cipher.open(sealed, 60) == {"token": "api-key"}
    # The next page load is signed in.
    assert 'hx-post="logout"' in user_client.get("/").text


# User mode: device flow


DEVICE_CODE = {
    "device_code": "dev-123",
    "user_code": "ABCD1234",
    "verification_uri": "https://kaneo.test/device",
    "verification_uri_complete": "https://kaneo.test/device?user_code=ABCD1234",
    "expires_in": 1800,
    "interval": 5,
}


def start_device(client, kaneo, **overrides):
    kaneo.post("/auth/device/code").respond(json={**DEVICE_CODE, **overrides})
    token = csrf(client)
    return token, client.post("/login/device", data={"csrf_token": token})


def test_device_start(user_client, kaneo):
    _, response = start_device(user_client, kaneo)
    html = response.text
    assert 'data-testid="user-code">ABCD1234' in html
    assert 'href="https://kaneo.test/device?user_code=ABCD1234"' in html
    assert 'hx-trigger="load delay:5s"' in html
    assert "dev-123" not in html
    sent = json.loads(kaneo.calls.last.request.content)
    assert sent == {"client_id": "kaneo-templates"}
    assert auth.DEVICE_COOKIE in response.cookies
    assert "Secure" in cookie_header(response, auth.DEVICE_COOKIE)


def test_device_start_custom_client_id(templates_dir, kaneo):
    client = make_client(templates_dir, DEVICE_CLIENT_ID="my-app", **USER_ENV)
    start_device(client, kaneo)
    assert json.loads(kaneo.calls.last.request.content) == {"client_id": "my-app"}


def test_device_start_without_complete_uri(user_client, kaneo):
    _, response = start_device(user_client, kaneo, verification_uri_complete=None)
    assert 'href="https://kaneo.test/device"' in response.text


def test_device_start_requires_csrf(user_client, kaneo):
    response = user_client.post("/login/device", data={"csrf_token": "forged"})
    assert "Session expirée" in response.text
    assert not kaneo.calls


def test_device_client_not_allowed(user_client, kaneo):
    kaneo.post("/auth/device/code").respond(
        400, json={"error": "invalid_client", "error_description": "Invalid client ID"}
    )
    response = user_client.post("/login/device", data={"csrf_token": csrf(user_client)})
    assert "DEVICE_AUTH_CLIENT_IDS" in response.text
    assert "<code>kaneo-templates</code>" in response.text


def test_device_start_kaneo_error(user_client, kaneo):
    kaneo.post("/auth/device/code").respond(500, json={"error": "server_error"})
    response = user_client.post("/login/device", data={"csrf_token": csrf(user_client)})
    assert "Kaneo a renvoyé une erreur" in response.text


def poll(client, kaneo, token, **reply):
    kaneo.post("/auth/device/token").respond(**reply)
    return client.post("/login/device/poll", data={"csrf_token": token})


def test_device_poll_pending(user_client, kaneo):
    token, _ = start_device(user_client, kaneo)
    response = poll(
        user_client, kaneo, token, status_code=400, json={"error": "authorization_pending"}
    )
    assert 'hx-trigger="load delay:5s"' in response.text
    sent = json.loads(kaneo.calls.last.request.content)
    assert sent["device_code"] == "dev-123"
    assert sent["client_id"] == "kaneo-templates"


def test_device_poll_slow_down_increases_interval(user_client, kaneo):
    token, _ = start_device(user_client, kaneo)
    response = poll(user_client, kaneo, token, status_code=400, json={"error": "slow_down"})
    assert 'hx-trigger="load delay:10s"' in response.text
    # The new interval is remembered for the next poll.
    response = poll(
        user_client, kaneo, token, status_code=400, json={"error": "authorization_pending"}
    )
    assert 'hx-trigger="load delay:10s"' in response.text


@pytest.mark.parametrize(
    "error, message",
    [("access_denied", "La connexion a été refusée"), ("expired_token", "Le code a expiré")],
)
def test_device_poll_final_errors(user_client, kaneo, error, message):
    token, _ = start_device(user_client, kaneo)
    response = poll(user_client, kaneo, token, status_code=400, json={"error": error})
    assert message in response.text
    assert "Recommencer" in response.text
    assert is_cleared(response, auth.DEVICE_COOKIE)


def test_device_poll_unexpected_error(user_client, kaneo):
    token, _ = start_device(user_client, kaneo)
    response = poll(
        user_client,
        kaneo,
        token,
        status_code=400,
        json={"error": "invalid_grant", "error_description": "Invalid device code"},
    )
    assert "Invalid device code" in response.text
    assert is_cleared(response, auth.DEVICE_COOKIE)


def test_device_poll_approved(user_client, kaneo):
    token, _ = start_device(user_client, kaneo)
    response = poll(
        user_client,
        kaneo,
        token,
        json={"access_token": "session-xyz", "token_type": "Bearer", "expires_in": 604800},
    )
    assert response.headers["HX-Refresh"] == "true"
    assert is_cleared(response, auth.DEVICE_COOKIE)
    sealed = response.cookies[auth.SESSION_COOKIE]
    assert user_client.app.state.cipher.open(sealed, 60) == {"token": "session-xyz"}


def test_device_poll_without_pending_flow(user_client, kaneo):
    response = user_client.post("/login/device/poll", data={"csrf_token": csrf(user_client)})
    assert "Le code a expiré" in response.text
    assert not kaneo.calls


def test_device_poll_after_expiry_does_not_call_kaneo(user_client, kaneo):
    state = auth.DeviceState("dev", "CODE", "https://kaneo.test/device", 5, time.time() - 1)
    user_client.cookies.set(auth.DEVICE_COOKIE, user_client.app.state.cipher.seal(state.__dict__))
    response = user_client.post("/login/device/poll", data={"csrf_token": csrf(user_client)})
    assert "Le code a expiré" in response.text
    assert not [c for c in kaneo.calls if c.request.method == "POST"]


def test_device_poll_requires_csrf(user_client, kaneo):
    start_device(user_client, kaneo)
    response = user_client.post("/login/device/poll", data={"csrf_token": "forged"})
    assert "Session expirée" in response.text


# User mode: sign out


def test_logout_clears_session(user_client, kaneo):
    # Sign in through the app so that the cookie is the one the logout deletes.
    user_client.post("/login/token", data={"csrf_token": csrf(user_client), "token": "k"})
    assert 'hx-post="logout"' in user_client.get("/").text
    response = user_client.post("/logout", data={"csrf_token": csrf(user_client)})
    assert response.headers["HX-Refresh"] == "true"
    assert is_cleared(response, auth.SESSION_COOKIE)
    assert "Connexion à Kaneo" in user_client.get("/").text


def test_logout_requires_csrf(user_client):
    sign_in(user_client)
    response = user_client.post("/logout", data={"csrf_token": "forged"})
    assert "Session expirée" in response.text
    assert cookie_header(response, auth.SESSION_COOKIE) is None


def test_user_mode_cookies_follow_root_path(templates_dir, kaneo):
    client = make_client(templates_dir, ROOT_PATH="/tools", **USER_ENV)
    client.cookies.set(auth.CSRF_COOKIE, "c" * 43)
    response = client.post("/login/token", data={"csrf_token": "c" * 43, "token": "api-key"})
    assert "Path=/tools" in cookie_header(response, auth.SESSION_COOKIE)
