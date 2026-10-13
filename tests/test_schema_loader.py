import logging
import textwrap

import pytest

from kaneo_templates.loader import TemplateError, load_templates, parse_template
from kaneo_templates.scheduling import Offset
from kaneo_templates.schema import column_slug

VALID = textwrap.dedent(
    """\
    version: 1
    name: Organisation d'un événement
    description: Tâches planifiées autour de la date de l'événement
    end_date:
      required: true
      label: Date de l'événement
    columns:
      - À faire
      - En cours
      - Terminé
    tasks:
      - title: Réserver la salle
        due: -30d
        priority: high
        column: À faire
        description: Vérifier la capacité et l'accès
        labels: [logistique]
      - title: Envoyer les convocations
        due: -7d
        priority: medium
      - title: Préparer l'ordre du jour
        due: -5b
      - title: Rédiger le compte rendu
        due: +3d
        subtasks:
          - title: Relire le brouillon
            due: +5d
          - title: Diffuser
      - title: Faire le point avec l'équipe
    """
)


def tpl(body: str) -> str:
    return textwrap.dedent(body)


def test_valid_template():
    t = parse_template(VALID)
    assert t.name == "Organisation d'un événement"
    assert t.end_date_required
    assert t.end_date.label == "Date de l'événement"
    assert t.columns == ["À faire", "En cours", "Terminé"]
    assert [task.due for task in t.tasks] == [
        Offset(-30),
        Offset(-7),
        Offset(-5, "b"),
        Offset(3),
        None,
    ]
    assert t.tasks[0].priority == "high"
    assert t.tasks[0].labels == ["logistique"]
    assert [s.title for s in t.tasks[3].subtasks] == ["Relire le brouillon", "Diffuser"]
    assert t.tasks[3].subtasks[1].due is None
    paths = [p for p, _ in t.iter_items()]
    assert paths[3:6] == ["tasks[3]", "tasks[3].subtasks[0]", "tasks[3].subtasks[1]"]
    assert t.all_labels == ["logistique"]


def test_minimal_template():
    t = parse_template("version: 1\nname: Vide\n")
    assert t.tasks == []
    assert t.columns is None
    assert t.end_date is None
    assert not t.end_date_required


def test_end_date_defaults_and_optional():
    t = parse_template(
        tpl("""\
        version: 1
        name: X
        end_date:
        tasks:
          - title: A
            due: J-7
        """)
    )
    assert t.end_date is not None
    assert t.end_date.required is True
    t = parse_template("version: 1\nname: X\nend_date:\n  required: false\n")
    assert not t.end_date_required


@pytest.mark.parametrize("version", ["2", "0", "'1'", "true", "1.5"])
def test_unknown_version(version):
    with pytest.raises(TemplateError, match="version"):
        parse_template(f"version: {version}\nname: X\n")


def test_missing_version_and_name():
    with pytest.raises(TemplateError) as exc:
        parse_template("tasks: []\n")
    assert "version" in str(exc.value)
    assert "name" in str(exc.value)


def test_unknown_column():
    with pytest.raises(TemplateError, match=r"tasks\[0\]: column is not listed"):
        parse_template(
            tpl("""\
            version: 1
            name: X
            columns: [A faire, Fait]
            tasks:
              - title: T
                column: En cours
            """)
        )


def test_unknown_column_in_subtask():
    with pytest.raises(TemplateError, match=r"tasks\[0\]\.subtasks\[0\]: column"):
        parse_template(
            tpl("""\
            version: 1
            name: X
            columns: [A faire]
            tasks:
              - title: T
                subtasks:
                  - title: S
                    column: Ailleurs
            """)
        )


def test_column_matched_by_kaneo_slug():
    t = parse_template(
        tpl("""\
        version: 1
        name: X
        columns: [À faire]
        tasks:
          - title: T
            column: "à  FAIRE"
        """)
    )
    assert t.tasks[0].column == "à  FAIRE"


def test_column_without_columns_section_is_resolved_later():
    t = parse_template("version: 1\nname: X\ntasks:\n  - title: T\n    column: In Progress\n")
    assert t.tasks[0].column == "In Progress"


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        ("[]", "must not be empty"),
        ("['---']", "at least one letter"),
        ("[Planned]", "reserved"),
        ("[À faire, à-faire]", "same Kaneo column"),
    ],
)
def test_invalid_columns(columns, message):
    with pytest.raises(TemplateError, match=message):
        parse_template(f"version: 1\nname: X\ncolumns: {columns}\n")


def test_due_without_end_date():
    with pytest.raises(TemplateError, match=r"tasks\[0\]: 'due' requires an 'end_date'"):
        parse_template("version: 1\nname: X\ntasks:\n  - title: T\n    due: -7d\n")


def test_subtask_due_without_end_date():
    with pytest.raises(TemplateError, match=r"subtasks\[0\]: 'due' requires"):
        parse_template(
            tpl("""\
            version: 1
            name: X
            tasks:
              - title: T
                subtasks:
                  - title: S
                    due: +1d
            """)
        )


@pytest.mark.parametrize("due", ["-7", "7d", "-7w", "soon"])
def test_invalid_due(due):
    with pytest.raises(TemplateError, match="due"):
        parse_template(
            f"version: 1\nname: X\nend_date: {{}}\ntasks:\n  - title: T\n    due: {due}\n"
        )


def test_invalid_priority():
    with pytest.raises(TemplateError, match="priority"):
        parse_template("version: 1\nname: X\ntasks:\n  - title: T\n    priority: critical\n")


def test_subtasks_only_one_level_deep():
    with pytest.raises(TemplateError, match="subtasks"):
        parse_template(
            tpl("""\
            version: 1
            name: X
            tasks:
              - title: T
                subtasks:
                  - title: S
                    subtasks:
                      - title: SS
            """)
        )


def test_unknown_key_rejected():
    with pytest.raises(TemplateError, match="dues"):
        parse_template("version: 1\nname: X\ntasks:\n  - title: T\n    dues: -7d\n")


def test_empty_title_rejected():
    with pytest.raises(TemplateError, match="title"):
        parse_template("version: 1\nname: X\ntasks:\n  - title: '  '\n")


def test_duplicate_titles_tolerated():
    t = parse_template("version: 1\nname: X\ntasks:\n  - title: T\n  - title: T\n")
    assert [task.title for task in t.tasks] == ["T", "T"]


def test_broken_yaml():
    with pytest.raises(TemplateError, match="invalid YAML at line"):
        parse_template("version: 1\nname: [unclosed\n")


def test_yaml_not_a_mapping():
    with pytest.raises(TemplateError, match="mapping"):
        parse_template("- a\n- b\n")
    with pytest.raises(TemplateError, match="mapping"):
        parse_template("")


def test_error_messages_do_not_leak_content():
    secret = "Projet confidentiel Zorglub"
    with pytest.raises(TemplateError) as exc:
        parse_template(
            f"version: 1\nname: X\ncolumns: [A]\n"
            f"tasks:\n  - title: {secret}\n    column: {secret}\n"
        )
    assert "Zorglub" not in str(exc.value)
    with pytest.raises(TemplateError) as exc:
        parse_template(f"version: 1\nname: X\ntasks:\n  - title: T\n    priority: {secret}\n")
    assert "Zorglub" not in str(exc.value)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("To Do", "to-do"),
        ("À faire", "à-faire"),
        ("  En   cours ! ", "en-cours"),
        ("Terminé", "terminé"),
        ("v2.0 release", "v2-0-release"),
        ("---", ""),
        ("\uff21\uff22\uff23", "abc"),
    ],
)
def test_column_slug(name, slug):
    assert column_slug(name) == slug


def write(directory, name, content):
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_templates_directory(tmp_path, caplog):
    write(tmp_path, "evenement.yml", VALID)
    write(tmp_path, "simple.yaml", "version: 1\nname: Simple\n")
    write(tmp_path, "broken.yml", "version: 1\nname: [\n")
    write(tmp_path, "future.yml", "version: 9\nname: Future\n")
    write(tmp_path, "notes.txt", "ignored")
    write(tmp_path, ".hidden.yml", "version: 1\nname: Hidden\n")
    write(tmp_path, "bad name.yml", "version: 1\nname: X\n")
    write(tmp_path, "simple.yml", "version: 1\nname: Dup\n")

    with caplog.at_level(logging.WARNING):
        result = load_templates(tmp_path)

    assert sorted(result.templates) == ["evenement", "simple"]
    assert result.templates["simple"].name == "Simple"
    assert set(result.errors) == {"broken", "future", "bad name.yml", "simple.yml"}
    assert "YAML" in result.errors["broken"]
    assert "version" in result.errors["future"]
    assert "already defines" in result.errors["simple.yml"]
    assert "Template broken ignored" in caplog.text


def test_load_templates_missing_directory(tmp_path):
    result = load_templates(tmp_path / "nope")
    assert result.templates == {}
    assert result.errors == {}


def test_load_templates_invalid_utf8(tmp_path):
    (tmp_path / "latin.yml").write_bytes("version: 1\nname: Été\n".encode("latin-1"))
    result = load_templates(tmp_path)
    assert "UTF-8" in result.errors["latin"]


def test_load_templates_too_large(tmp_path):
    write(tmp_path, "big.yml", "version: 1\nname: X\n" + "#" * 300_000)
    assert "larger" in load_templates(tmp_path).errors["big"]


def test_get_never_resolves_paths(tmp_path):
    write(tmp_path, "evenement.yml", VALID)
    result = load_templates(tmp_path)
    assert result.get("evenement") is not None
    for attempt in ["../evenement", "evenement.yml", "/etc/passwd", "..", ""]:
        assert result.get(attempt) is None


# Label colors


def test_label_colors_are_normalized():
    t = parse_template(
        tpl(
            """\
            version: 1
            name: X
            labels:
              logistique: "#F59E0B"
              team: " #abc "
            tasks:
              - title: A
                labels: [logistique]
            """
        )
    )
    assert t.labels == {"logistique": "#f59e0b", "team": "#abc"}
    assert t.label_color("logistique") == "#f59e0b"
    assert t.label_color("other") is None


@pytest.mark.parametrize("color", ["red", "#12", "#1234567", "f59e0b", "123", "[1]", "#ggg"])
def test_invalid_label_color(color):
    with pytest.raises(TemplateError, match="labels"):
        parse_template(f"version: 1\nname: X\nlabels:\n  a: {color!r}\n")


def test_label_colors_without_tasks_are_valid():
    t = parse_template('version: 1\nname: X\nlabels:\n  a: "#000000"\n')
    assert t.all_labels == []


def test_labels_section_must_be_a_mapping():
    with pytest.raises(TemplateError, match="labels"):
        parse_template("version: 1\nname: X\nlabels: [a, b]\n")
