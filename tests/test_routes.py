import base64
import shutil
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kaneo_templates.config import load_settings
from kaneo_templates.kaneo_client import KaneoClient
from kaneo_templates.main import create_app

BASE = "https://kaneo.test/api"
EXAMPLES = Path(__file__).parent.parent / "templates-examples"
WORKSPACES = [{"id": "w1", "name": "Main"}, {"id": "w2", "name": "Other"}]


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
        {"KANEO_BASE_URL": BASE, "KANEO_TOKEN": "tok", "TEMPLATES_DIR": str(templates_dir), **env}
    )
    app = create_app(settings)
    app.state.client_factory = lambda s: KaneoClient(s.kaneo_base_url, s.kaneo_token)
    return TestClient(app)


@pytest.fixture
def client(templates_dir, kaneo):
    return make_client(templates_dir)


def csrf(client):
    response = client.get("/")
    return response.cookies.get("kt_csrf") or client.cookies.get("kt_csrf")


def test_index_lists_workspaces_and_templates(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Main" in html
    assert "Organisation d&#39;un événement" in html
    assert "broken" in html
    assert 'src="static/htmx.min.js"' in html
    assert "kt_csrf" in response.cookies
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_static_htmx_served_locally(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "htmx" in response.text[:200]


def test_workspace_preselected(templates_dir, kaneo):
    html = make_client(templates_dir, KANEO_WORKSPACE_ID="w2").get("/").text
    assert '<option value="w2" selected>' in html


def test_token_rejected_message(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").respond(401, json={"message": "no", "code": "UNAUTHORIZED"})
        html = make_client(templates_dir).get("/").text
    assert "KANEO_TOKEN" in html


def test_preview(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-14"}).text
    assert "15/05/2030" in html
    assert "Relire le brouillon" in html
    assert "À faire" in html


def test_preview_weekend_flag(client):
    html = client.get("/preview", params={"template": "evenement", "end_date": "2030-06-12"}).text
    assert "week-end" in html


def test_preview_unknown_or_traversal_template(client):
    for template in ["../evenement", "evenement.yml", "/etc/passwd", "nope"]:
        response = client.get("/preview", params={"template": template})
        assert response.status_code == 200
        assert "Réserver la salle" not in response.text


def test_template_fields(client):
    response = client.get("/template-fields", params={"template": "onboarding"})
    assert "Date d&#39;arrivée" in response.text
    assert "facultatif" in response.text
    assert response.headers["HX-Trigger"] == "refresh-preview"


def test_create_requires_csrf(client, kaneo):
    form = {"workspace_id": "w1", "template": "evenement", "name": "X", "end_date": "2030-06-14"}
    response = client.post("/create", data={**form, "csrf_token": "forged"})
    assert "Session expirée" in response.text
    assert not kaneo.calls or all(c.request.method == "GET" for c in kaneo.calls)


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


def test_create_requires_end_date(client, kaneo):
    token = csrf(client)
    response = client.post(
        "/create",
        data={"csrf_token": token, "workspace_id": "w1", "template": "evenement", "name": "X"},
    )
    assert "date de fin est obligatoire" in response.text


def test_create_runs_creator(client, kaneo):
    kaneo.get("/project").respond(json=[])
    kaneo.post("/project").respond(json={"id": "p1", "workspaceId": "w1", "name": "X", "slug": "X"})
    kaneo.get("/column/p1").respond(
        json=[{"id": "c1", "name": "To Do", "slug": "to-do", "position": 0, "isFinal": False}]
    )
    kaneo.post("/task/p1").respond(json={"id": "t1", "title": "A", "status": "to-do"})
    template_dir = client.app.state.settings.templates_dir
    (template_dir / "simple.yml").write_text("version: 1\nname: Simple\ntasks:\n  - title: A\n")
    token = csrf(client)
    response = client.post(
        "/create",
        data={"csrf_token": token, "workspace_id": "w1", "template": "simple", "name": "X"},
    )
    assert "Projet créé" in response.text
    assert "https://kaneo.test/dashboard/workspace/w1/project/p1/board" in response.text
    assert "1 tâches créées" in response.text


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


def test_service_mode_without_basic_auth_is_open(client):
    assert client.get("/").status_code == 200


def test_user_mode_not_available_yet(templates_dir):
    client = make_client(templates_dir, AUTH_MODE="user", SECRET_KEY="x" * 32, KANEO_TOKEN="")
    with respx.mock(base_url=BASE) as mock:
        html = client.get("/").text
        assert not mock.calls
    assert "AUTH_MODE=user" in html


def test_root_path_uses_relative_urls(templates_dir, kaneo):
    client = make_client(templates_dir, ROOT_PATH="/tools")
    html = client.get("/").text
    assert 'hx-get="preview"' in html
    assert 'href="static/app.css"' in html


def test_kaneo_unreachable(templates_dir):
    with respx.mock(base_url=BASE) as mock:
        mock.get("/workspace").mock(side_effect=httpx.ConnectError("down"))
        html = make_client(templates_dir).get("/").text
    assert "Impossible de lire les workspaces" in html
