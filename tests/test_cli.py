import io
import logging
import shutil
from pathlib import Path

import pytest

from kaneo_templates import main

EXAMPLES = Path(__file__).parent.parent / "templates-examples"


def run_check(directory):
    out = io.StringIO()
    return main.check(directory, out), out.getvalue()


def test_check_examples_are_valid():
    status, out = run_check(EXAMPLES)
    assert status == 0
    assert "ok     evenement: 5 tasks, 2 subtasks" in out
    assert "error" not in out


def test_check_reports_invalid_templates(tmp_path):
    shutil.copy(EXAMPLES / "evenement.yml", tmp_path)
    (tmp_path / "broken.yml").write_text("version: 1\nname: [\n")
    (tmp_path / "typo.yml").write_text("version: 1\nname: X\ntasks:\n  - title: A\n    dues: -1d\n")
    (tmp_path / "bad name.yml").write_text("version: 1\nname: X\n")
    status, out = run_check(tmp_path)
    assert status == 1
    assert "ok     evenement" in out
    assert "error  broken: invalid YAML" in out
    assert "error  typo: tasks[0].dues" in out
    assert "error  bad name.yml" in out


def test_check_missing_directory(tmp_path):
    status, out = run_check(tmp_path / "nope")
    assert status == 2
    assert "is not a directory" in out


def test_check_empty_directory(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    status, out = run_check(tmp_path)
    assert status == 1
    assert "no .yml or .yaml template" in out


def test_check_does_not_log_and_restores_logging(tmp_path, caplog):
    (tmp_path / "broken.yml").write_text("version: 1\nname: [\n")
    with caplog.at_level(logging.WARNING):
        run_check(tmp_path)
    assert not caplog.records
    assert logging.root.manager.disable == logging.NOTSET


def test_run_check_exit_status(tmp_path):
    (tmp_path / "broken.yml").write_text("nope")
    with pytest.raises(SystemExit) as exc:
        main.run(["check", str(tmp_path)])
    assert exc.value.code == 1
    with pytest.raises(SystemExit) as exc:
        main.run(["check", str(EXAMPLES)])
    assert exc.value.code == 0


def test_run_check_defaults_to_templates_dir(monkeypatch, capsys):
    monkeypatch.setenv("TEMPLATES_DIR", str(EXAMPLES))
    with pytest.raises(SystemExit) as exc:
        main.run(["check"])
    assert exc.value.code == 0
    assert "ok     onboarding" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["serve"]])
def test_run_serves_by_default(monkeypatch, argv):
    calls = []
    monkeypatch.setattr(main, "serve", lambda: calls.append(True))
    main.run(argv)
    assert calls == [True]


def test_version(capsys):
    with pytest.raises(SystemExit):
        main.run(["--version"])
    assert capsys.readouterr().out.startswith("kaneo-templates ")
