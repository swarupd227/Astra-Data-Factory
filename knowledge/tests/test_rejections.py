"""The rejection taxonomy as data (S2.3.2)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from astra_knowledge.cdm import load_pack
from astra_knowledge.cli import main
from astra_knowledge.patterns import LifecycleState, apply_lifecycle, merge, parse
from astra_knowledge.patterns.merge import SilverState
from astra_knowledge.registry import Registry
from astra_knowledge.rejections import Taxonomy, load_loader_reference, load_taxonomy, parity

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
CUSTODIAL = DOMAINS / "custodial"
PATTERNS = REPO / "knowledge" / "src" / "astra_knowledge" / "patterns"


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    taxonomy, problems = load_taxonomy(CUSTODIAL / "rejections.yaml", REPO)
    assert problems == [], [p.format() for p in problems]
    return taxonomy


@pytest.fixture(scope="module")
def registry() -> Registry:
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    return registry


# ------------------------------------------------------------- the taxonomy


def test_the_taxonomy_has_more_than_fifty_codes_with_description_severity_and_owner(taxonomy):
    assert len(taxonomy.codes) >= 50
    assert len({c.code for c in taxonomy.codes}) == len(taxonomy.codes)
    for c in taxonomy.codes:
        assert c.description and c.severity and c.owner and c.resolution, c.code
        assert c.level in ("file", "record", "field")
        assert c.owner in ("custodian", "data_engineer", "steward", "platform")
    assert {c.level for c in taxonomy.codes} == {"file", "record", "field"}
    assert {c.severity for c in taxonomy.codes} == {"critical", "error", "warning"}
    assert {c.owner for c in taxonomy.codes} == {"custodian", "data_engineer", "steward", "platform"}


def test_severity_follows_the_level(taxonomy):
    for c in taxonomy.codes:
        if c.level == "file":
            assert c.severity == "critical", c.code
        else:
            assert c.severity in ("error", "warning"), c.code


def test_codes_name_entities_of_the_model(taxonomy):
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == []
    entities = {e.name for e in pack.latest.entities}
    named = {c.entity for c in taxonomy.codes if c.entity}
    assert named <= entities
    assert {"Account", "Security", "Transaction", "Price", "Position"} <= named
    assert pack.rejections is taxonomy or pack.rejections.codes == taxonomy.codes


def test_auto_resolvable_codes_say_what_the_rule_does(taxonomy):
    automatic = [c for c in taxonomy.codes if c.auto_resolve]
    assert {c.code for c in automatic} == {"PRICE_MISSING", "PRICE_STALE"}
    for c in automatic:
        assert len(c.resolution) > 30


# -------------------------------------------- the pattern library raises them


def test_every_code_the_pattern_library_raises_is_in_the_taxonomy(taxonomy):
    raised: set[str] = set()
    for source in PATTERNS.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        raised |= set(re.findall(r'code="([A-Z_]+)"', text))
        raised |= set(re.findall(r'"([A-Z]+_[A-Z_]+)"\)', text))  # positional codes on Converted(...) and ValueError_(...)
        for kind in ("header", "trailer"):  # code=f"FILE_{kind.upper()}_MISSING"
            if 'f"FILE_{kind.upper()}_MISSING"' in text:
                raised.add(f"FILE_{kind.upper()}_MISSING")
    raised = {code for code in raised if len(code) > 3}
    known = {c.code for c in taxonomy.codes}
    assert raised, "no codes found in the pattern sources"
    assert raised <= known, sorted(raised - known)
    for expected in ("FIELD_NOT_NUMERIC", "FIELD_SIGN_INVALID", "FIELD_CODE_UNKNOWN", "RECORD_TYPE_UNKNOWN", "FILE_TRAILER_MISSING", "FILE_COLUMN_COUNT", "PAIR_INCOMPLETE", "MERGE_OUT_OF_ORDER", "LIFECYCLE_DUPLICATE", "SPLIT_NOT_NUMERIC"):
        assert expected in raised


def _malformed_gcus() -> list[str]:
    from tests.test_fixed_width import dtl, hdr

    good = dtl()
    return [
        hdr(),
        hdr(),  # second header
        good,
        dtl(qty="0000000000ABC45678"),  # non-digits in a number
        "XYZ" + good[3:],  # unknown record type
        good + "X",  # too long
        # no trailer
    ]


def test_every_problem_a_fixed_width_parse_reports_has_a_code(taxonomy, registry):
    spec = registry.get("pershing_gcus", "2026-01-01")
    parsed = parse(spec, _malformed_gcus())
    assert parsed.problems
    known = {c.code for c in taxonomy.codes}
    for problem in parsed.problems:
        assert problem.code in known, problem.text()
    codes = {p.code for p in parsed.problems}
    assert {"RECORD_DUPLICATE_HEADER", "FIELD_NOT_NUMERIC", "RECORD_TYPE_UNKNOWN", "RECORD_TOO_LONG", "FILE_TRAILER_MISSING"} <= codes


def test_every_problem_a_delimited_parse_reports_has_a_code(taxonomy, registry):
    spec = registry.get("csv_price_example", "2026-01-01")
    lines = ["wrong,header,row,for,this,spec", "a,b,c", 'x,"unterminated']
    parsed = parse(spec, lines)
    assert parsed.problems
    known = {c.code for c in taxonomy.codes}
    for problem in parsed.problems:
        assert problem.code in known, problem.text()
    assert "FILE_HEADER_MISMATCH" in {p.code for p in parsed.problems}


def test_merge_and_lifecycle_problems_carry_codes(taxonomy, registry):
    from tests.test_fixed_width import hdr, trl

    known = {c.code for c in taxonomy.codes}
    spec = registry.get("pershing_gcus", "2026-01-01")
    result = merge(SilverState(), spec, parse(spec, [hdr(flag="Z"), trl("000000000")]), "bad.dat")
    assert result.problems and all(p.code in known for p in result.problems)

    drip = registry.get("drip_transaction_example", "2026-01-01")
    from tests.test_split_lifecycle import dtl as txn, hdr as txn_hdr, trl as txn_trl

    lifecycle = apply_lifecycle(LifecycleState(), drip, parse(drip, [txn_hdr(), txn("T9", action="X", original="T404"), txn_trl(1)]))
    assert [p.code for p in lifecycle.problems] == ["LIFECYCLE_ORIGINAL_MISSING"]


# ------------------------------------------------------------------- parity


def _reference(tmp_path: Path, rows: list[tuple[str, str]], header: str = "code,description") -> Path:
    path = tmp_path / "loader-rejections.csv"
    path.write_text("\n".join([header, *(f"{c},{d}" for c, d in rows)]) + "\n", encoding="utf-8")
    return path


def _pack_with(tmp_path: Path, loader_map: dict[str, list[str]], reference_rows: list[tuple[str, str]] | None):
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    data = yaml.safe_load((root / "rejections.yaml").read_text(encoding="utf-8"))
    for item in data["codes"]:
        if item["code"] in loader_map:
            item["loader_codes"] = loader_map[item["code"]]
    (root / "rejections.yaml").write_text(yaml.safe_dump(data, sort_keys=False, width=1000), encoding="utf-8")
    if reference_rows is not None:
        _reference(root, reference_rows)
    return root


def test_parity_reports_reproduced_missing_and_unknown_loader_codes(tmp_path, taxonomy):
    reference, problems = load_loader_reference(_reference(tmp_path, [("L001", "Account not on file"), ("L002", "Bad security"), ("L003", "Trailer count wrong")]))
    assert problems == []
    root = _pack_with(tmp_path, {"ACCOUNT_NOT_FOUND": ["L001"], "SECURITY_NOT_FOUND": ["L002"], "PRICE_ZERO": ["L999"]}, None)
    mapped, _ = load_taxonomy(root / "rejections.yaml")
    report = parity(mapped, reference)
    assert report.mapped == {"L001": "ACCOUNT_NOT_FOUND", "L002": "SECURITY_NOT_FOUND"}
    assert report.unmapped == ("L003",)
    assert report.unknown == ("L999",)
    assert not report.ok


def test_pack_validation_enforces_parity_when_the_reference_is_present(tmp_path):
    root = _pack_with(tmp_path, {"ACCOUNT_NOT_FOUND": ["L001"], "PRICE_ZERO": ["L999"]}, [("L001", "Account not on file"), ("L003", "Trailer count wrong")])
    _, problems = load_pack(root, tmp_path)
    assert [p.message for p in problems] == [
        "Loader code 'L003' (Trailer count wrong) is reproduced by no rejection code; add it to a code's loader_codes",
        "Loader code 'L999' is named in loader_codes but is not in domains/custodial/loader-rejections.csv",
    ]
    assert all(p.path == "domains/custodial/rejections.yaml" for p in problems)

    root2 = _pack_with(tmp_path / "ok", {"ACCOUNT_NOT_FOUND": ["L001"], "FILE_RECORD_COUNT": ["L003"]}, [("L001", "Account not on file"), ("L003", "Trailer count wrong")])
    pack, problems = load_pack(root2, tmp_path / "ok")
    assert problems == [] and pack.loader_reference is not None and len(pack.loader_reference.codes) == 2


def test_a_loader_code_maps_to_one_rejection_code(tmp_path):
    root = _pack_with(tmp_path, {"ACCOUNT_NOT_FOUND": ["L001"], "ACCOUNT_CLOSED": ["L001"]}, None)
    _, problems = load_taxonomy(root / "rejections.yaml", tmp_path)
    assert [p.message for p in problems] == ["code ACCOUNT_CLOSED: Loader code 'L001' is already reproduced by ACCOUNT_NOT_FOUND; a Loader code maps to one code"]


def test_the_loader_reference_needs_code_and_description_columns(tmp_path):
    _, problems = load_loader_reference(_reference(tmp_path, [("L001", "x")], header="id,text"), tmp_path)
    assert [p.message for p in problems] == ["the header row must name the columns code and description; found id, text"]
    _, problems = load_loader_reference(_reference(tmp_path, [("L001", "x"), ("L001", "y"), ("", "z")]), tmp_path)
    assert [p.message for p in problems] == ["Loader code 'L001' appears twice", "the code is blank"]


# --------------------------------------------------------------- validation


def _mutated(tmp_path: Path, mutate) -> list[str]:
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    data = yaml.safe_load((root / "rejections.yaml").read_text(encoding="utf-8"))
    mutate(data)
    (root / "rejections.yaml").write_text(yaml.safe_dump(data, sort_keys=False, width=1000), encoding="utf-8")
    _, problems = load_pack(root, tmp_path)
    return [p.message for p in problems]


def _code(data: dict, code: str) -> dict:
    return next(c for c in data["codes"] if c["code"] == code)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: _code(d, "FILE_EMPTY").update(severity="warning"), "code FILE_EMPTY: a file-level rejection is critical, not warning"),
        (lambda d: _code(d, "PAIR_INCOMPLETE").update(severity="critical"), "code PAIR_INCOMPLETE: a record-level rejection is error or warning, not critical"),
        (lambda d: d["codes"].append(dict(_code(d, "FILE_EMPTY"))), "code FILE_EMPTY is defined twice"),
        (lambda d: _code(d, "PRICE_ZERO").update(entity="Quote"), "code PRICE_ZERO names entity 'Quote', which is in no model version"),
        (lambda d: _code(d, "PRICE_ZERO").update(code="price_zero"), "codes[52].code"),
        (lambda d: _code(d, "PRICE_ZERO").pop("owner"), "missing required field owner"),
        (lambda d: d.update(domain="wealth"), "taxonomy domain 'wealth' must match the pack directory 'custodial'"),
    ],
)
def test_the_validator_rejects_a_taxonomy_that_does_not_hold_together(tmp_path, mutate, expected):
    messages = _mutated(tmp_path, mutate)
    assert any(expected in m for m in messages), messages


def test_a_pack_needs_a_taxonomy(tmp_path):
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    (root / "rejections.yaml").unlink()
    _, problems = load_pack(root, tmp_path)
    assert [p.message for p in problems] == ["domain pack has no rejections.yaml; every pack carries its rejection taxonomy"]


# ---------------------------------------------------------------------- CLI


def test_cli_lists_codes_and_filters_by_level_and_owner(capsys):
    args = ["--root", str(REPO), "--domains", str(DOMAINS), "rejections", "list"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "ACCOUNT_NOT_FOUND" in out and "record" in out and "steward" in out and out.strip().endswith("codes")

    assert main([*args, "--level", "file", "--owner", "custodian"]) == 0
    out = capsys.readouterr().out
    assert "FILE_TRAILER_MISSING" in out and "ACCOUNT_NOT_FOUND" not in out

    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "--format", "json", "rejections", "list", "--level", "field"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(item["level"] == "field" for item in payload) and any(item["code"] == "FIELD_REQUIRED_BLANK" for item in payload)


def test_cli_validate_counts_rejection_codes(capsys):
    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "cdm", "validate"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"checked 1 model version, 15 glossary terms and \d\d rejection codes across 1 domain pack: no problems", out), out


def test_cli_parity_without_a_reference_says_where_to_put_it(tmp_path, capsys):
    root = _pack_with(tmp_path, {}, None)
    assert main(["--root", str(tmp_path), "--domains", str(tmp_path / "domains"), "rejections", "parity", "--domain", "custodial"]) == 1
    assert "add loader-rejections.csv to domains/custodial or pass --reference" in capsys.readouterr().err


def test_cli_parity_reports_against_a_reference(tmp_path, capsys):
    root = _pack_with(tmp_path, {"ACCOUNT_NOT_FOUND": ["L001"], "FILE_RECORD_COUNT": ["L003"]}, None)
    reference = _reference(tmp_path, [("L001", "Account not on file"), ("L002", "Bad security"), ("L003", "Trailer count wrong")])
    args = ["--root", str(tmp_path), "--domains", str(tmp_path / "domains"), "rejections", "parity", "--domain", "custodial", "--reference", str(reference)]
    assert main(args) == 1
    out = capsys.readouterr().out
    assert "custodial: 3 Loader codes in loader-rejections.csv; 2 reproduced, 1 not reproduced, 0 named but not in the reference" in out
    assert "L002          NOT REPRODUCED  Bad security" in out
    assert "L001          -> ACCOUNT_NOT_FOUND" in out

    assert main(["--format", "json", *args]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["unmapped"] == ["L002"] and payload["mapped"]["L003"] == "FILE_RECORD_COUNT"
