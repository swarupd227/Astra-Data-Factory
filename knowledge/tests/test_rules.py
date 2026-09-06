"""The rule catalog store (S2.4.1)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.cli import main
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog, StatusError, lineage, load_rule_file, render_rule, set_status

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "rules"
CONFIGS = REPO / "configs"
SPECS = REPO / "specs"


@pytest.fixture(scope="module")
def registry() -> Registry:
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == []
    return registry


@pytest.fixture(scope="module")
def catalog(registry) -> Catalog:
    catalog, problems = Catalog.load(RULES, REPO, registry)
    assert problems == [], [p.format() for p in problems]
    return catalog


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "rules"
    shutil.copytree(RULES, root)
    return root


# -------------------------------------------------------------- the store


def test_every_rule_has_id_text_citation_class_owner_and_status(catalog):
    assert catalog.ids() == ["drip_transaction_example.cancel_correct", "drip_transaction_example.drip_split", "pershing_gcus.quantity_sign", "pershing_gcus.refresh_mode"]
    for rule in catalog.rules:
        assert rule.text and rule.class_ in ("ingestion", "business", "normalisation") and rule.owner.email and rule.status in ("recovered", "confirmed", "rejected", "legacy_defect")
        assert rule.citation.kind == "spec" and rule.citation.page
        assert rule.history[-1].status == rule.status and all(h.by for h in rule.history)
    rule = catalog.get("pershing_gcus.quantity_sign")
    assert (rule.group, rule.name, rule.class_, rule.status) == ("pershing_gcus", "quantity_sign", "normalisation", "confirmed")
    assert rule.citation.text == "spec pershing_gcus 2017-07-25 page 13 line 6"
    assert [h.status for h in rule.history] == ["recovered", "confirmed"] and rule.last_change.by == "steward@example.com"
    assert rule.custodians == ("pershing",) and rule.entities == ("Position",)


def test_rules_can_cite_code_and_documents(tmp_path):
    root = _copy(tmp_path)
    (root / "legacy").mkdir()
    (root / "legacy" / "trailer_count.yaml").write_text(
        'rule_version: 0\nrule:\n  id: legacy.trailer_count\n  text: "The Loader compares the trailer count with the detail records it read and rejects the file on a mismatch."\n'
        '  class: ingestion\n  owner: { name: Steward, email: steward@example.com }\n  status: recovered\n  citation:\n    code: { repository: splitter-loader, file: src/Loader.java, line: 412, end_line: 430 }\n'
        'history:\n  - { status: recovered, by: rule-recovery, at: "2026-09-06T12:00:00Z" }\n',
        encoding="utf-8",
    )
    (root / "legacy" / "holiday_files.yaml").write_text(
        'rule_version: 0\nrule:\n  id: legacy.holiday_files\n  text: "Files delivered on an exchange holiday are held and loaded with the next business day."\n'
        '  class: business\n  owner: { name: Steward, email: steward@example.com }\n  status: confirmed\n  citation:\n    document: { name: Operations runbook, page: 7, section: Holidays }\n'
        'history:\n  - { status: recovered, by: rule-recovery, at: "2026-09-06T12:00:00Z" }\n  - { status: confirmed, by: steward@example.com, at: "2026-09-06T13:00:00Z" }\n',
        encoding="utf-8",
    )
    catalog, problems = Catalog.load(root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    assert catalog.get("legacy.trailer_count").citation.text == "splitter-loader src/Loader.java:412-430"
    assert catalog.get("legacy.holiday_files").citation.text == "Operations runbook page 7 (Holidays)"


def _write(root: Path, group: str, name: str, text: str) -> Path:
    (root / group).mkdir(exist_ok=True)
    path = root / group / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASE = (
    'rule_version: 0\nrule:\n  id: {id}\n  text: "A rule with enough words to be a rule."\n  class: business\n'
    "  owner: {{ name: Steward, email: steward@example.com }}\n  status: {status}\n  citation:\n    spec: {{ id: {spec}, version: \"{version}\", page: {page} }}\n"
    "history:\n{history}"
)


def _rule(root: Path, group="pershing_gcus", name="new_rule", status="recovered", spec="pershing_gcus", version="2017-07-25", page=4, history='  - { status: recovered, by: agent, at: "2026-09-06T10:00:00Z" }\n', rule_id=None) -> Path:
    return _write(root, group, name, BASE.format(id=rule_id or f"{group}.{name}", status=status, spec=spec, version=version, page=page, history=history))


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(rule_id="pershing_gcus.other"), "rule.id 'pershing_gcus.other' must match the file's place in the catalog: pershing_gcus.new_rule"),
        (dict(status="confirmed"), "rule.status 'confirmed' is not the last recorded status 'recovered'; a status changes only through the history"),
        (dict(history='  - { status: recovered, by: agent, at: "2026-09-06T10:00:00Z" }\n  - { status: recovered, by: agent, at: "2026-09-06T11:00:00Z" }\n'), "history[1] repeats the status 'recovered' of history[0]"),
        (dict(status="confirmed", history='  - { status: recovered, by: agent, at: "2026-09-06T10:00:00Z" }\n  - { status: confirmed, by: steward@example.com, at: "2026-09-06T09:00:00Z" }\n'), "history[1] is not after history[0]"),
        (dict(history='  - { status: recovered, by: agent, at: "yesterday" }\n'), "history[0].at"),
        (dict(history=""), "history"),
        (dict(version="2001-01-01"), "citation names spec pershing_gcus 2001-01-01, which is not in the spec registry; known versions: 2017-07-25, 2026-01-01"),
        (dict(spec="nowhere"), "citation names spec nowhere 2017-07-25, which is not in the spec registry; no such spec in the registry"),
        (dict(page=999), "citation page 999 is beyond the"),
    ],
)
def test_the_validator_rejects_rules_that_do_not_hold_together(tmp_path, registry, kwargs, expected):
    root = _copy(tmp_path)
    path = _rule(root, **kwargs)
    _, problems = load_rule_file(path, tmp_path, registry)
    assert any(expected in p.message for p in problems), [p.message for p in problems]


def test_rules_must_sit_one_directory_down(tmp_path):
    root = _copy(tmp_path)
    (root / "stray.yaml").write_text("rule_version: 0\n", encoding="utf-8")
    _, problems = Catalog.load(root, tmp_path)
    assert [p.message for p in problems] == ["rules live one directory down: rules/<group>/<name>.yaml"]


# ------------------------------------------------------------ set-status


def test_changing_a_status_records_who_and_when(tmp_path, registry):
    root = _copy(tmp_path)
    catalog, _ = Catalog.load(root, tmp_path, registry)
    rule = catalog.get("pershing_gcus.refresh_mode")
    when = datetime(2026, 9, 8, 14, 5, 0, tzinfo=timezone.utc)
    changed = set_status(rule, "confirmed", "steward@example.com", "Checked against three sample files.", at=when)
    assert changed.status == "confirmed" and changed.last_change == changed.history[-1]
    assert (changed.last_change.by, changed.last_change.at, changed.last_change.note) == ("steward@example.com", when, "Checked against three sample files.")

    reloaded, problems = Catalog.load(root, tmp_path, registry)
    assert problems == []
    again = reloaded.get("pershing_gcus.refresh_mode")
    assert again.status == "confirmed" and [h.status for h in again.history] == ["recovered", "confirmed"]
    assert again.text == rule.text and again.citation == rule.citation and again.custodians == rule.custodians and again.tags == rule.tags
    text = again.path.read_text(encoding="utf-8")
    assert 'at: "2026-09-08T14:05:00Z"' in text and "status: confirmed" in text and "astra-spec rules set-status" in text


def test_a_status_change_must_be_a_change_by_someone_later_than_the_last(tmp_path, registry):
    root = _copy(tmp_path)
    catalog, _ = Catalog.load(root, tmp_path, registry)
    rule = catalog.get("pershing_gcus.quantity_sign")
    with pytest.raises(StatusError, match="already confirmed"):
        set_status(rule, "confirmed", "steward@example.com")
    with pytest.raises(StatusError, match="who is changing the status"):
        set_status(rule, "rejected", "  ")
    with pytest.raises(StatusError, match="later than the last recorded change"):
        set_status(rule, "rejected", "steward@example.com", at=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc))
    with pytest.raises(StatusError, match="is not a status"):
        set_status(rule, "approved", "steward@example.com")
    assert rule.path.read_text(encoding="utf-8") == (RULES / "pershing_gcus" / "quantity_sign.yaml").read_text(encoding="utf-8")


def test_render_round_trips_every_shipped_rule(catalog, tmp_path, registry):
    for rule in catalog.rules:
        path = tmp_path / rule.group / f"{rule.name}.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(render_rule(rule), encoding="utf-8")
        again, problems = load_rule_file(path, tmp_path, registry)
        assert problems == [] and again is not None
        assert (again.id, again.text, again.class_, again.owner, again.status, again.citation, again.history, again.custodians, again.entities, again.tags) == (rule.id, rule.text, rule.class_, rule.owner, rule.status, rule.citation, rule.history, rule.custodians, rule.entities, rule.tags)


# --------------------------------------------------------------- lineage


def test_lineage_lists_the_configs_that_use_each_rule(catalog):
    usage = lineage(catalog, sorted(CONFIGS.rglob("*.yaml")), REPO)
    assert usage.configs_for("pershing_gcus.quantity_sign") == ["configs/examples/pershing_position.yaml"]
    assert usage.configs_for("drip_transaction_example.drip_split") == []
    assert usage.unknown == [] and usage.rejected == []


def test_lineage_reports_unknown_and_rejected_references(tmp_path, registry):
    root = _copy(tmp_path)
    _rule(root, name="old_rule", status="rejected", history='  - { status: recovered, by: agent, at: "2026-09-06T10:00:00Z" }\n  - { status: rejected, by: steward@example.com, at: "2026-09-06T11:00:00Z", note: "Not how it works." }\n')
    catalog, problems = Catalog.load(root, tmp_path, registry)
    assert problems == []
    config = tmp_path / "configs" / "x.yaml"
    config.parent.mkdir()
    config.write_text("config_version: 0\nrules: [pershing_gcus.old_rule, pershing_gcus.nowhere]\nmappings:\n  - { target: a.b, source: X, rule: pershing_gcus.quantity_sign }\n", encoding="utf-8")
    usage = lineage(catalog, [config], tmp_path)
    assert usage.configs_for("pershing_gcus.quantity_sign") == ["configs/x.yaml"]
    assert usage.unknown == [("configs/x.yaml", "pershing_gcus.nowhere")]
    assert usage.rejected == [("configs/x.yaml", "pershing_gcus.old_rule")]


# ---------------------------------------------------------------------- CLI


def _args(root: Path = REPO) -> list[str]:
    return ["--root", str(root), "--specs", str(root / "specs"), "--rules", str(root / "rules")]


def test_cli_validate_reports_the_catalog_and_config_references(capsys):
    assert main([*_args(), "rules", "validate", "--configs", str(CONFIGS)]) == 0
    out = capsys.readouterr().out
    assert "checked 4 rules (3 recovered, 1 confirmed): no problems; 1 config references only known, unrejected rules" in out
    assert "note: pershing_gcus.refresh_mode is recovered but no config uses it" in out


def test_cli_validate_fails_on_a_config_that_references_a_rejected_rule(tmp_path, capsys):
    root = tmp_path
    shutil.copytree(RULES, root / "rules")
    shutil.copytree(SPECS, root / "specs")
    (root / "configs").mkdir()
    config = root / "configs" / "x.yaml"
    config.write_text("config_version: 0\nrules: [pershing_gcus.quantity_sign]\n", encoding="utf-8")
    assert main([*_args(root), "rules", "set-status", "pershing_gcus.quantity_sign", "--status", "rejected", "--by", "steward@example.com", "--note", "Superseded by the sign convention rule."]) == 0
    assert "pershing_gcus.quantity_sign: confirmed -> rejected by steward@example.com at " in capsys.readouterr().out
    assert main([*_args(root), "--format", "github", "rules", "validate", "--configs", str(root / "configs")]) == 1
    out = capsys.readouterr().out
    assert "::error file=configs/x.yaml,title=Rule catalog::references rule 'pershing_gcus.quantity_sign', which its owner has rejected" in out


def test_cli_list_show_and_set_status(tmp_path, capsys):
    root = tmp_path
    shutil.copytree(RULES, root / "rules")
    shutil.copytree(SPECS, root / "specs")
    shutil.copytree(CONFIGS, root / "configs")
    assert main([*_args(root), "rules", "list", "--configs", str(root / "configs")]) == 0
    out = capsys.readouterr().out
    assert "pershing_gcus.quantity_sign              normalisation  confirmed      steward@example.com  spec pershing_gcus 2017-07-25 page 13 line 6  used by 1 config" in out
    assert out.strip().endswith("4 rules")

    assert main([*_args(root), "rules", "list", "--status", "confirmed", "--class", "normalisation"]) == 0
    assert capsys.readouterr().out.strip().endswith("1 rule")
    assert main([*_args(root), "rules", "list", "--group", "nowhere"]) == 1

    assert main([*_args(root), "rules", "show", "pershing_gcus.quantity_sign", "--configs", str(root / "configs")]) == 0
    out = capsys.readouterr().out
    assert "pershing_gcus.quantity_sign  normalisation  confirmed  owner Data steward, custodial <steward@example.com>  rules/pershing_gcus/quantity_sign.yaml" in out
    assert "citation: spec pershing_gcus 2017-07-25 page 13 line 6" in out
    assert "2026-09-06 11:30 UTC  confirmed      by steward@example.com  Blank sign is unknown, not zero" in out
    assert "used by: configs/examples/pershing_position.yaml" in out

    assert main([*_args(root), "rules", "set-status", "pershing_gcus.refresh_mode", "--status", "legacy_defect", "--by", "steward@example.com"]) == 0
    assert main([*_args(root), "rules", "set-status", "pershing_gcus.refresh_mode", "--status", "legacy_defect", "--by", "steward@example.com"]) == 1
    assert "already legacy_defect" in capsys.readouterr().err
    assert main([*_args(root), "--format", "json", "rules", "show", "pershing_gcus.refresh_mode"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "legacy_defect" and [h["status"] for h in payload["history"]] == ["recovered", "legacy_defect"]

    assert main([*_args(root), "rules", "show", "nowhere.rule"]) == 1
    assert "no rule nowhere.rule" in capsys.readouterr().err
