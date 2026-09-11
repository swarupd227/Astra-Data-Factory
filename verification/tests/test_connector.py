"""Client DQ tool connector (S4.2.4): a connection sheet to the same golden output and lakehouse table parity.yaml already compares, and a place for the tool's own result."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from astra_data.bundle import Target

from astra_verification.cli import main
from astra_verification.connector import (
    ConnectorError,
    check,
    describe,
    discover,
    load_connector,
    render_markdown,
    store_result,
    write_descriptor,
)
from astra_verification.golden import LocalStore
from astra_verification.parity import load_parity

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "golden"
CONNECTOR = GOLDEN / "pershing" / "connector.yaml"
PARITY = GOLDEN / "pershing" / "parity.yaml"
TEXT = CONNECTOR.read_text(encoding="utf-8")


def _connector():
    c, problems = load_connector(CONNECTOR, REPO)
    assert problems == [], [p.format() for p in problems]
    return c


def _mapping():
    m, problems = load_parity(PARITY, REPO)
    assert problems == [], [p.format() for p in problems]
    return m


# ---------------------------------------------------------------- loading


def test_the_committed_connector_config_loads_clean():
    c = _connector()
    assert c.custodian == "pershing" and c.tool == "icedq" and c.role == "ENVESTNET_ICEDQ_READONLY"
    assert c.result_formats == ("csv", "json")


def test_no_such_file(tmp_path):
    c, problems = load_connector(tmp_path / "missing.yaml", tmp_path)
    assert c is None and "no such file" in problems[0].message


def test_an_unknown_tool_is_refused(tmp_path):
    path = tmp_path / "connector.yaml"
    path.write_text(TEXT.replace("tool: icedq", "tool: excel"), encoding="utf-8")
    c, problems = load_connector(path, tmp_path)
    assert c is None and problems


def test_a_bad_result_format_is_refused(tmp_path):
    path = tmp_path / "connector.yaml"
    path.write_text(TEXT.replace("result_formats: [csv, json]", "result_formats: [docx]"), encoding="utf-8")
    c, problems = load_connector(path, tmp_path)
    assert c is None and problems


def test_result_formats_default_to_csv_alone(tmp_path):
    path = tmp_path / "connector.yaml"
    path.write_text(TEXT.replace("result_formats: [csv, json]\n", ""), encoding="utf-8")
    c, problems = load_connector(path, tmp_path)
    assert problems == []
    assert c.result_formats == ("csv",)


# ---------------------------------------------------------------- discover / check


def test_discover_finds_the_committed_connector():
    assert CONNECTOR in discover(GOLDEN)


def test_check_reports_no_problems_for_the_committed_configs():
    configs, problems = check(GOLDEN, REPO)
    assert problems == []
    assert any(c.custodian == "pershing" for c in configs)


def test_check_flags_a_custodian_directory_mismatch(tmp_path):
    other = tmp_path / "other_custodian"
    other.mkdir()
    (other / "connector.yaml").write_text(TEXT, encoding="utf-8")
    configs, problems = check(tmp_path, tmp_path)
    assert any("must match the directory" in p.message for p in problems)


# ---------------------------------------------------------------- describe


def test_describe_derives_everything_from_parity_yaml_and_the_runtime_store():
    connector, mapping = _connector(), _mapping()
    store = LocalStore(Path("s3-mirror"))
    d = describe(connector, mapping, store, Target("dev"))
    assert d["custodian"] == "pershing" and d["tool"] == "icedq"
    assert d["golden"]["kind"] == "file"
    assert "pershing/{business_date}/v{version}/outputs/positions.csv" in d["golden"]["location"]
    assert d["golden"]["columns_used_by_astra_verify"] == ["AccountNumber", "Cusip", "AsOfDate", "Quantity", "Price", "MarketValue"]
    assert d["lakehouse"] == {
        "kind": "snowflake",
        "database": "ASTRA_DEV",
        "schema": "SILVER",
        "table": "POSITION",
        "role": "ENVESTNET_ICEDQ_READONLY",
        "filter": "CUSTODIAN_ID = 'pershing' AND AS_OF_DATE = '{business_date}'",
        "columns_used_by_astra_verify": ["ACCOUNT_NUMBER", "CUSTODIAN_SECURITY_ID", "AS_OF_DATE", "QUANTITY", "PRICE", "MARKET_VALUE"],
    }
    assert len(d["keys"]) == 3 and len(d["fields"]) == 3
    assert d["result"]["accepted_formats"] == ["csv", "json"]


def test_describe_names_an_s3_store_by_kind():
    connector, mapping = _connector(), _mapping()

    class FakeS3Store:
        uri = "s3://astra-dev-golden-123456789012"

    d = describe(connector, mapping, FakeS3Store(), Target("dev"))
    assert d["golden"]["kind"] == "s3"
    assert d["golden"]["location"].startswith("s3://astra-dev-golden-123456789012/pershing/")


def test_render_markdown_and_write_descriptor(tmp_path):
    connector, mapping = _connector(), _mapping()
    d = describe(connector, mapping, LocalStore(Path("s3-mirror")), Target("dev"))
    text = render_markdown(d)
    assert "# Client DQ tool connector: pershing (icedq)" in text
    assert "ENVESTNET_ICEDQ_READONLY" in text
    assert "astra-verify connector store-result" in text
    markdown, data = write_descriptor(d, tmp_path / "pershing")
    assert markdown.read_text(encoding="utf-8") == text
    assert json.loads(data.read_text(encoding="utf-8"))["custodian"] == "pershing"


# ---------------------------------------------------------------- store_result


def test_store_result_copies_the_file_and_writes_a_hashed_sidecar(tmp_path):
    connector = _connector()
    export = tmp_path / "icedq-export.csv"
    content = b"a,b\n1,2\n"
    export.write_bytes(content)
    out = tmp_path / "pershing-2026-08-29"
    dest, meta_path = store_result(connector, export, date(2026, 8, 29), out)
    assert dest == out / "icedq-result.csv"
    assert dest.read_bytes() == content
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["tool"] == "icedq" and meta["custodian"] == "pershing" and meta["business_date"] == "2026-08-29"
    assert meta["source_file"] == "icedq-export.csv" and meta["bytes"] == len(content)
    import hashlib

    assert meta["sha256"] == hashlib.sha256(content).hexdigest()


def test_store_result_refuses_a_format_the_connector_does_not_accept(tmp_path):
    connector = _connector()
    export = tmp_path / "icedq-export.pdf"
    export.write_bytes(b"%PDF-1.4")
    with pytest.raises(ConnectorError) as excinfo:
        store_result(connector, export, date(2026, 8, 29), tmp_path / "out")
    assert "not an accepted result format" in str(excinfo.value)


def test_store_result_refuses_a_missing_file(tmp_path):
    connector = _connector()
    with pytest.raises(ConnectorError) as excinfo:
        store_result(connector, tmp_path / "missing.csv", date(2026, 8, 29), tmp_path / "out")
    assert "no such file" in str(excinfo.value)


# ---------------------------------------------------------------- CLI


def test_cli_check(capsys):
    code = main(["connector", "check", str(GOLDEN)])
    assert code == 0
    assert "pershing: icedq" in capsys.readouterr().out


def test_cli_describe_needs_no_snowflake_connection(tmp_path, capsys):
    out = tmp_path / "connector"
    code = main(["connector", "describe", "--config", str(CONNECTOR), "--environment", "dev", "--golden", str(GOLDEN), "--store", str(tmp_path / "s3-mirror"), "--out", str(out)])
    assert code == 0
    assert (out / "pershing" / "connector.md").exists()
    text = capsys.readouterr().out
    assert "connector for pershing: icedq" in text


def test_cli_describe_json(tmp_path, capsys):
    out = tmp_path / "connector"
    code = main(["connector", "describe", "--config", str(CONNECTOR), "--environment", "dev", "--golden", str(GOLDEN), "--store", str(tmp_path / "s3-mirror"), "--out", str(out), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["custodian"] == "pershing"


def test_cli_store_result(tmp_path, capsys):
    export = tmp_path / "icedq-export.json"
    export.write_text('{"ok": true}', encoding="utf-8")
    out = tmp_path / "parity"
    code = main(["connector", "store-result", "--config", str(CONNECTOR), "--business-date", "2026-08-29", "--file", str(export), "--out", str(out)])
    assert code == 0
    assert (out / "pershing-2026-08-29" / "icedq-result.json").exists()
    assert "stored icedq's result for pershing 2026-08-29" in capsys.readouterr().out


def test_cli_store_result_rejects_an_unaccepted_format(tmp_path, capsys):
    export = tmp_path / "icedq-export.xml"
    export.write_text("<r/>", encoding="utf-8")
    code = main(["connector", "store-result", "--config", str(CONNECTOR), "--business-date", "2026-08-29", "--file", str(export), "--out", str(tmp_path / "parity")])
    assert code == 2
    assert "not an accepted result format" in capsys.readouterr().err


def test_cli_describe_and_store_result_land_in_the_same_directory_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    export = tmp_path / "icedq-export.csv"
    export.write_text("a,b\n1,2\n", encoding="utf-8")
    code = main(["connector", "store-result", "--config", str(CONNECTOR), "--business-date", "2026-08-29", "--file", str(export)])
    assert code == 0
    assert (tmp_path / "work" / "parity" / "pershing-2026-08-29" / "icedq-result.csv").exists()
