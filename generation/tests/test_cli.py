import json
from pathlib import Path

from astra_data.cli import main
from tests.test_bundle import make_bundle
from tests.test_validate import VALID


def test_validate_reports_problems_as_github_annotations(tmp_path, capsys):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pershing_position.yaml").write_text(VALID.replace("status: recovered", "status: maybe"), encoding="utf-8")

    code = main(["--root", str(tmp_path), "--format", "github", "validate", str(configs)])
    out = capsys.readouterr().out

    assert code == 1
    assert "::error file=configs/pershing_position.yaml,line=" in out
    assert "title=Config validation::rules[0].status: 'maybe' is not one of" in out
    assert "::notice title=Astra Data::1 problem in 1 config file" in out


def test_validate_passes_cleanly_and_json_is_empty_list(tmp_path, capsys):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "pershing_position.yaml").write_text(VALID, encoding="utf-8")

    assert main(["--root", str(tmp_path), "validate", str(configs)]) == 0
    assert capsys.readouterr().out.strip() == "checked 1 config file: no problems"

    assert main(["--root", str(tmp_path), "--format", "json", "validate", str(configs)]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_validate_with_no_configs_is_not_an_error(tmp_path, capsys):
    (tmp_path / "configs").mkdir()
    assert main(["--root", str(tmp_path), "validate", str(tmp_path / "configs")]) == 0
    assert "no config files found" in capsys.readouterr().out


def test_bundles_check_reports_each_bundle(tmp_path, capsys):
    releases = tmp_path / "releases"
    make_bundle(releases)
    assert main(["--root", str(tmp_path), "bundles", "check", str(releases)]) == 0
    out = capsys.readouterr().out
    assert "bundle pershing-position 2026.09: 2 steps, 2 tests" in out
    assert "checked 1 release bundle: no problems" in out


def test_bundles_check_fails_on_a_broken_bundle(tmp_path, capsys):
    releases = tmp_path / "releases"
    root = make_bundle(releases)
    (root / "ddl" / "bronze.sql").unlink()
    assert main(["--root", str(tmp_path), "--format", "github", "bundles", "check", str(releases)]) == 1
    assert "::error file=releases/pershing-position/manifest.yaml,title=Config validation::steps[0] 'ddl/bronze.sql' does not exist" in capsys.readouterr().out


def test_deploy_and_test_with_no_bundles_do_nothing_and_succeed(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    releases = tmp_path / "releases"
    releases.mkdir()
    assert main(["deploy", "--environment", "dev", str(releases)]) == 0
    assert "nothing to deploy to ASTRA_DEV" in capsys.readouterr().out
    assert main(["test", "--environment", "qa", "--prefix", "ENV", str(releases)]) == 0
    assert "no tests to run against ENV_QA" in capsys.readouterr().out


def test_deploy_refuses_a_bad_environment_name(capsys):
    try:
        main(["deploy", "--environment", "Prod"])
    except SystemExit as exc:
        assert exc.code == 2
    assert "must be 2 to 8 lower-case" in capsys.readouterr().err


def test_deploy_requires_connection_settings_when_there_is_a_bundle(tmp_path, monkeypatch):
    for name in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_ORGANIZATION_NAME", "SNOWFLAKE_ACCOUNT_NAME", "SNOWFLAKE_USER"):
        monkeypatch.delenv(name, raising=False)
    releases = tmp_path / "releases"
    make_bundle(releases)
    try:
        main(["--root", str(tmp_path), "deploy", "--environment", "dev", str(releases)])
    except SystemExit as exc:
        assert "SNOWFLAKE_ACCOUNT" in str(exc.code)
    else:
        raise AssertionError("expected a SystemExit naming the missing connection settings")
