import json
from pathlib import Path

from astra_knowledge.cli import main

REPO = Path(__file__).resolve().parents[2]


def run(*argv: str) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(["--root", str(REPO), "--specs", str(REPO / "specs"), *argv])
    return code, buffer.getvalue()


def test_validate_reports_the_shipped_registry_clean():
    code, out = run("validate")
    assert code == 0
    assert "checked 5 spec versions across 4 specs: no problems" in out


def test_list_shows_every_version_with_its_effective_date():
    code, out = run("list")
    assert code == 0
    assert "pershing_gcus 2017-07-25  in force from 2017-07-25  position  custodians: pershing" in out
    assert "pershing_gcus 2026-01-01  in force from 2026-01-01" in out


def test_resolve_names_the_version_in_force_and_fails_when_none():
    code, out = run("resolve", "--custodian", "pershing", "--file-type", "position", "--date", "2025-06-30")
    assert code == 0 and "pershing_gcus 2017-07-25 is in force for pershing position on 2025-06-30 (from 2017-07-25)" in out

    code, out = run("--format", "json", "resolve", "--custodian", "pershing", "--file-type", "position", "--date", "2026-09-06")
    assert code == 0 and json.loads(out)["in_force"]["version"] == "2026-01-01"

    code, out = run("resolve", "--custodian", "pershing", "--file-type", "position", "--date", "2010-01-01")
    assert code == 1 and "no spec in force for pershing position on 2010-01-01; the earliest version comes into force on 2017-07-25" in out

    code, out = run("resolve", "--custodian", "schwab", "--file-type", "position", "--date", "2026-09-06")
    assert code == 1 and "no spec lists this custodian and file type" in out


def test_show_prints_fields_with_position_picture_and_citation():
    code, out = run("show", "--id", "pershing_gcus", "--version", "2026-01-01")
    assert code == 0
    assert "lot_id" in out and "67-78" in out and "X(12)" in out and "page 14, line 6" in out

    code, out = run("--format", "json", "show", "--id", "pershing_gcus", "--version", "2017-07-25")
    payload = json.loads(out)
    detail = next(r for r in payload["record_details"] if r["type"] == "detail")
    quantity = next(f for f in detail["fields"] if f["name"] == "quantity")
    assert quantity == {"name": "quantity", "position": [23, 18], "column": None, "picture": "9(13)V9(5)", "type": "decimal", "format": None, "citation": "page 13, line 2", "codes": []}


def test_search_ranks_and_flags_from_the_command_line(tmp_path, capsys):
    from tests.test_registry import search_registry

    search_registry(tmp_path)
    specs = str(tmp_path / "specs")

    code = main(["--root", str(tmp_path), "--specs", specs, "search", "--custodian", "acme", "--family", "pershing_gcus", "--file-type", "position"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0].startswith("1. acme_positions 2024-01-01  matched by custodian  latest version  position  custodians: acme  family: pershing_gcus")
    assert out.splitlines()[1].startswith("2. pershing_gcus 2026-01-01  matched by family")

    code = main(["--root", str(tmp_path), "--specs", specs, "--format", "json", "search", "--file-type", "position", "--date", "2025-06-01"])
    hits = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [(h["id"], h["version"], h["rank"], h["flags"]) for h in hits] == [
        ("acme_positions", "2024-01-01", 3, []),
        ("other_positions", "2020-05-01", 3, ["no family; needs classification"]),
        ("pershing_gcus", "2017-07-25", 3, []),
    ]

    code = main(["--root", str(tmp_path), "--specs", specs, "search", "--custodian", "nobody"])
    assert code == 1 and "no matching specs" in capsys.readouterr().out

    code = main(["--root", str(tmp_path), "--specs", specs, "unclassified"])
    assert code == 0 and "other_positions 2020-05-01  position  custodians: other  needs classification" in capsys.readouterr().out

    code = main(["--root", str(tmp_path), "--specs", specs, "--format", "github", "validate"])
    out = capsys.readouterr().out
    assert code == 0
    assert "::warning file=specs/other_positions/2020-05-01.yaml,title=Spec registry::other_positions 2020-05-01 has no family; needs classification" in out


def test_validate_prints_github_annotations_for_a_broken_registry(tmp_path, capsys):
    root = tmp_path / "specs" / "bad_spec"
    root.mkdir(parents=True)
    (root / "v1.yaml").write_text("spec_version: 0\nspec:\n  id: bad_spec\n  version: v1\n", encoding="utf-8")
    code = main(["--root", str(tmp_path), "--specs", str(tmp_path / "specs"), "--format", "github", "validate"])
    out = capsys.readouterr().out
    assert code == 1
    assert "::error file=specs/bad_spec/v1.yaml,line=1,title=Spec registry::top level: missing required fields document, file, records" in out
