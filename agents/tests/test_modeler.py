from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from astra_knowledge.registry import load_spec_file
from astra_knowledge.rules import Owner
from astra_agents.modeler import (
    CONFIRM_WITH_LOADER,
    Extraction,
    ModelerError,
    build_cdm_change_request,
    build_draft,
    cdm_entities,
    cdm_targets,
    load_domain_pack,
    load_known_rule_ids,
    load_spec,
    render_markdown,
    run,
    write_draft,
)

REPO = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO / "specs" / "pershing_gcus" / "2017-07-25.yaml"
DOMAIN = REPO / "domains" / "custodial"
RULES = REPO / "rules"
OWNER = Owner("Data steward, custodial", "steward@example.com")
CLOCK = lambda: datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def _spec():
    spec, problems = load_spec_file(SPEC_PATH, REPO)
    assert problems == [], problems
    return spec


def _pack():
    return load_domain_pack(DOMAIN)


def _known_rule_ids():
    return load_known_rule_ids(RULES)


class FakeClient:
    def __init__(self, extraction: Extraction | None = None, error: Exception | None = None):
        self.extraction = extraction
        self.error = error
        self.calls: list[dict] = []

    def extract(self, *, system, content, targets, entities):
        self.calls.append({"system": system, "content": content, "targets": targets, "entities": entities})
        if self.error:
            raise self.error
        return self.extraction


# The seven mappings, the resolution block and one DQ suggestion a good run should recover,
# matching configs/examples/pershing_position.yaml (the real, already-reviewed config for this
# same spec) field for field.
GOOD_EXTRACTION = Extraction(
    mappings=[
        {"target": "position.account_number", "source_field": "account_number"},
        {"target": "position.custodian_security_id", "source_field": "cusip"},
        {"target": "position.as_of_date", "source_field": "as_of_date"},
        {"target": "position.quantity", "source_field": "quantity", "transform": "signed_implied_decimal(13, 5)", "existing_rule": "pershing_gcus.quantity_sign"},
        {"target": "position.price", "source_field": "price", "transform": "implied_decimal(6)"},
        {"target": "position.position_type", "constant": "LONG"},
        {"target": "position.currency", "constant": "USD"},
    ],
    cdm_change_requests=[],
    unmapped=[{"source_field": "security_type", "reason": "Position has no field for the custodian's raw security-type code; Security.SECURITY_TYPE covers this once resolution links to the security master."}],
    resolution={"account": {"source": "account_number"}, "security": {"by": [{"identifier": "CUSIP", "source": "cusip"}]}},
    dq_suggestions=[{"kind": "not_null", "field": "cusip", "check": "every detail record names a security"}],
)


def _draft(extraction: Extraction = GOOD_EXTRACTION, **kwargs):
    defaults = dict(spec=_spec(), model=_pack().latest, custodian="pershing", tier="medium", group="pershing_gcus", owner=OWNER, known_rule_ids=_known_rule_ids(), clock=CLOCK)
    defaults.update(kwargs)
    return build_draft(extraction, **defaults)


# ---------------------------------------------------------------- CDM vocabulary


def test_cdm_targets_include_the_real_position_columns():
    targets = cdm_targets(_pack().latest)
    assert "position.account_number" in targets
    assert "position.quantity" in targets
    assert "position.custodian_security_id" in targets


def test_cdm_targets_exclude_lineage_columns():
    targets = cdm_targets(_pack().latest)
    assert not any(t.split(".")[1] in ("source_system", "loaded_at", "config_version") for t in targets)


def test_cdm_entities_lists_every_entity_name():
    assert set(cdm_entities(_pack().latest)) >= {"Account", "Security", "Position", "Lot", "Transaction", "Price"}


# ---------------------------------------------------------------- build_draft / mappings


def test_build_draft_translates_a_complete_extraction_matching_the_real_config():
    draft = _draft()
    assert draft.valid is True, [(m.target, m.problems) for m in draft.mappings]
    assert len(draft.mappings) == 7
    quantity = next(m for m in draft.mappings if m.target == "position.quantity")
    assert quantity.transform == "signed_implied_decimal(13, 5)"
    assert quantity.existing_rule == "pershing_gcus.quantity_sign"
    assert quantity.new_rule_id is None


def test_build_draft_flags_a_target_not_in_the_cdm():
    extraction = Extraction(mappings=[{"target": "position.not_a_real_column", "source_field": "account_number"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.valid is False
    assert "not a column" in draft.mappings[0].problems[0]


def test_build_draft_flags_a_mapping_with_neither_source_nor_constant():
    extraction = Extraction(mappings=[{"target": "position.account_number"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.valid is False


def test_build_draft_flags_an_unparseable_transform_independently_of_the_tool_schema():
    extraction = Extraction(mappings=[{"target": "position.quantity", "source_field": "quantity", "transform": "not_a_real_transform(1)"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.valid is False
    assert "not_a_real_transform" in draft.mappings[0].problems[0]


def test_build_draft_drops_an_existing_rule_that_is_not_in_the_catalog():
    extraction = Extraction(mappings=[{"target": "position.account_number", "source_field": "account_number", "existing_rule": "nonexistent.rule"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.valid is False
    assert "not in the rule catalog" in draft.mappings[0].problems[0]


def test_build_draft_accepts_a_real_existing_rule():
    extraction = Extraction(mappings=[{"target": "position.quantity", "source_field": "quantity", "existing_rule": "pershing_gcus.quantity_sign"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.valid is True
    assert draft.mappings[0].existing_rule == "pershing_gcus.quantity_sign"


# ---------------------------------------------------------------- new rules / CONFIRM_WITH_LOADER


def test_build_draft_proposes_a_new_rule_with_recovered_status_never_confirmed():
    extraction = Extraction(
        mappings=[
            {
                "target": "position.position_type",
                "source_field": "security_type",
                "new_rule": {"name": "long_or_short_from_sign", "text": "A negative quantity means a short position; positive or zero means long.", "class": "business", "confirm_with_loader": False},
            }
        ],
        cdm_change_requests=[],
        unmapped=[],
    )
    draft = _draft(extraction)
    assert draft.valid is True
    assert len(draft.new_rules) == 1
    rule = draft.new_rules[0]
    assert rule.status == "recovered"
    assert rule.id == "pershing_gcus.long_or_short_from_sign"
    assert rule.citation.kind == "spec" and rule.citation.spec_id == "pershing_gcus"
    assert CONFIRM_WITH_LOADER not in rule.tags
    assert draft.confirm_with_loader == ()


def test_build_draft_tags_a_new_rule_confirm_with_loader_when_asked():
    extraction = Extraction(
        mappings=[
            {
                "target": "position.quantity",
                "source_field": "quantity",
                "new_rule": {"name": "ambiguous_sign_is_unknown", "text": "A blank sign character means the sign is unknown, not that the quantity is zero.", "class": "normalisation", "confirm_with_loader": True},
            }
        ],
        cdm_change_requests=[],
        unmapped=[],
    )
    draft = _draft(extraction)
    assert draft.valid is True
    rule = draft.new_rules[0]
    assert CONFIRM_WITH_LOADER in rule.tags
    assert draft.confirm_with_loader == (rule,)
    assert draft.ok is False  # a rule needing SME confirmation blocks "ready to review: yes"


def test_build_draft_new_rule_cites_the_source_fields_own_spec_page():
    extraction = Extraction(mappings=[{"target": "position.quantity", "source_field": "quantity", "new_rule": {"name": "x", "text": "a rule text that is at least twenty characters long", "class": "business", "confirm_with_loader": False}}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    quantity_field = next(f for r in _spec().records for f in r.fields if f.name == "quantity")
    assert draft.new_rules[0].citation.page == quantity_field.citation.page


def test_build_draft_dedupes_repeated_new_rule_names():
    proposal = {"target": "position.account_number", "source_field": "account_number", "new_rule": {"name": "dup", "text": "a rule text that is at least twenty characters long", "class": "business", "confirm_with_loader": False}}
    extraction = Extraction(mappings=[dict(proposal), dict(proposal, target="position.custodian_security_id", source_field="cusip")], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    ids = [r.id for r in draft.new_rules]
    assert ids == ["pershing_gcus.dup", "pershing_gcus.dup_2"]


# ---------------------------------------------------------------- CDM change requests


def test_build_cdm_change_request_classifies_an_optional_column_as_additive():
    request = build_cdm_change_request(_pack().latest, {"entity": "Position", "column_name": "SETTLEMENT_DATE", "type": "date", "required": False, "reason": "the spec has a settlement date with nowhere to go", "source_field": "settle_date"})
    assert request.breaking is False
    assert request.column.name == "SETTLEMENT_DATE"


def test_build_cdm_change_request_classifies_a_required_column_as_breaking():
    request = build_cdm_change_request(_pack().latest, {"entity": "Position", "column_name": "SETTLEMENT_DATE", "type": "date", "required": True, "reason": "always present in this custodian's files", "source_field": "settle_date"})
    assert request.breaking is True


def test_build_cdm_change_request_raises_for_an_unknown_entity():
    with pytest.raises(ModelerError, match="Position2"):
        build_cdm_change_request(_pack().latest, {"entity": "Position2", "column_name": "X", "type": "string", "required": False, "reason": "r", "source_field": "f"})


def test_build_draft_never_writes_into_the_domain_pack_for_a_change_request():
    """Nothing about building or reporting a CDM change request touches domains/custodial/cdm/
    on disk; this proves it by checking the real files are untouched, not just by omission."""
    before = (DOMAIN / "cdm" / "1.0.yaml").read_text(encoding="utf-8")
    extraction = Extraction(mappings=[], cdm_change_requests=[{"entity": "Position", "column_name": "SETTLEMENT_DATE", "type": "date", "required": True, "reason": "r", "source_field": "settle_date"}], unmapped=[])
    draft = _draft(extraction)
    assert draft.breaking_change_requests and draft.ok is False
    after = (DOMAIN / "cdm" / "1.0.yaml").read_text(encoding="utf-8")
    assert before == after


# ---------------------------------------------------------------- resolution / DQ / canonical items


def test_canonical_items_cover_mappings_resolution_and_dq():
    items = _draft().canonical_items()
    assert "map:position.account_number<-account_number" in items
    assert "map:position.quantity<-quantity~signed_implied_decimal(13, 5)" in items
    assert "map:position.position_type<-const:LONG" in items
    assert "resolution:account.source=account_number" in items
    assert "resolution:security.by=CUSIP:cusip" in items
    assert "dq:not_null:cusip" in items
    assert len(items) == 10


def test_canonical_items_exclude_an_invalid_mapping():
    extraction = Extraction(mappings=[{"target": "position.not_real", "source_field": "x"}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    assert draft.canonical_items() == ()


# ---------------------------------------------------------------- run() / loaders


def test_run_calls_the_client_and_defaults_tier_from_pattern_matcher():
    client = FakeClient(extraction=GOOD_EXTRACTION)
    draft = run(_spec(), _pack(), client, custodian="pershing", group="pershing_gcus", owner_name=OWNER.name, owner_email=OWNER.email, known_rule_ids=_known_rule_ids())
    assert draft.tier == "medium"  # matches configs/examples/pershing_position.yaml's own tier
    assert len(client.calls) == 1
    assert "position.account_number" in client.calls[0]["targets"]


def test_run_raises_the_clients_error():
    client = FakeClient(error=ModelerError("boom"))
    with pytest.raises(ModelerError, match="boom"):
        run(_spec(), _pack(), client, custodian="pershing", group="pershing_gcus", owner_name=OWNER.name, owner_email=OWNER.email)


def test_load_spec_reads_the_real_registry_spec():
    spec = load_spec(SPEC_PATH)
    assert spec.id == "pershing_gcus"


def test_load_domain_pack_reads_the_real_custodial_pack():
    pack = load_domain_pack(DOMAIN)
    assert pack.name == "custodial"


def test_load_known_rule_ids_includes_the_real_quantity_sign_rule():
    assert "pershing_gcus.quantity_sign" in load_known_rule_ids(RULES)


def test_load_spec_raises_a_clear_error_for_a_missing_file(tmp_path):
    with pytest.raises(ModelerError):
        load_spec(tmp_path / "missing.yaml")


# ---------------------------------------------------------------- report and files


def test_render_markdown_reports_mappings_and_change_requests():
    text = render_markdown(_draft())
    assert "position.account_number" in text
    assert "None proposed." in text  # no CDM change requests in the clean fixture


def test_render_markdown_flags_confirm_with_loader():
    extraction = Extraction(mappings=[{"target": "position.quantity", "source_field": "quantity", "new_rule": {"name": "x", "text": "a rule text that is at least twenty characters long", "class": "business", "confirm_with_loader": True}}], cdm_change_requests=[], unmapped=[])
    text = render_markdown(_draft(extraction))
    assert "CONFIRM_WITH_LOADER" in text


def test_write_draft_writes_report_and_new_rule_files(tmp_path):
    extraction = Extraction(mappings=[{"target": "position.quantity", "source_field": "quantity", "new_rule": {"name": "x", "text": "a rule text that is at least twenty characters long", "class": "business", "confirm_with_loader": True}}], cdm_change_requests=[], unmapped=[])
    draft = _draft(extraction)
    report_path, data_path = write_draft(draft, tmp_path / "out")
    assert report_path.exists()
    rule_path = tmp_path / "out" / "rules" / "pershing_gcus" / "x.yaml"
    assert rule_path.exists() and "status: recovered" in rule_path.read_text(encoding="utf-8")
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["ok"] is False


# ---------------------------------------------------------------- CLI


def test_cli_runs_with_a_fake_client(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr(cli, "ModelerClient", lambda model=None, max_tokens=None: FakeClient(extraction=GOOD_EXTRACTION))
    out = tmp_path / "out"
    code = cli.main([
        "modeler", "run",
        "--spec", str(SPEC_PATH), "--domain", str(DOMAIN), "--rules", str(RULES),
        "--custodian", "pershing", "--owner-name", OWNER.name, "--owner-email", OWNER.email,
        "--out", str(out),
    ])
    assert code == 0, capsys.readouterr()
    assert (out / "pershing_gcus" / "2017-07-25" / "report.md").exists()
    assert "ready to review: yes" in capsys.readouterr().out


def test_cli_exits_nonzero_for_a_breaking_change_request(tmp_path, monkeypatch):
    import astra_agents.cli as cli

    breaking = Extraction(mappings=[], cdm_change_requests=[{"entity": "Position", "column_name": "SETTLEMENT_DATE", "type": "date", "required": True, "reason": "r", "source_field": "settle_date"}], unmapped=[])
    monkeypatch.setattr(cli, "ModelerClient", lambda model=None, max_tokens=None: FakeClient(extraction=breaking))
    code = cli.main([
        "modeler", "run",
        "--spec", str(SPEC_PATH), "--domain", str(DOMAIN), "--rules", str(RULES),
        "--custodian", "pershing", "--owner-name", OWNER.name, "--owner-email", OWNER.email,
        "--out", str(tmp_path / "out"),
    ])
    assert code == 1


def test_cli_reports_a_start_error(tmp_path, monkeypatch, capsys):
    import astra_agents.cli as cli

    monkeypatch.setattr(cli, "ModelerClient", lambda model=None, max_tokens=None: FakeClient(error=ModelerError("boom")))
    code = cli.main([
        "modeler", "run",
        "--spec", str(SPEC_PATH), "--domain", str(DOMAIN), "--rules", str(RULES),
        "--custodian", "pershing", "--owner-name", OWNER.name, "--owner-email", OWNER.email,
        "--out", str(tmp_path / "out"),
    ])
    assert code == 2
    assert "boom" in capsys.readouterr().err


# ---------------------------------------------------------------- the story's own acceptance criteria


def test_the_committed_example_satisfies_the_story_acceptance_criteria():
    """S5.5.1's three acceptance criteria: mapping precision/recall is proven by the eval gold set
    (agents/examples/modeler); here, a rule needing SME confirmation is tagged CONFIRM_WITH_LOADER,
    and a breaking CDM change is raised as a request rather than written into the domain pack."""
    confirm = Extraction(
        mappings=[{"target": "position.quantity", "source_field": "quantity", "new_rule": {"name": "ambiguous_sign", "text": "a rule text that is at least twenty characters long", "class": "normalisation", "confirm_with_loader": True}}],
        cdm_change_requests=[{"entity": "Position", "column_name": "SETTLEMENT_DATE", "type": "date", "required": True, "reason": "always present in this custodian's files", "source_field": "settle_date"}],
        unmapped=[],
    )
    draft = _draft(confirm)
    assert CONFIRM_WITH_LOADER in draft.confirm_with_loader[0].tags
    assert draft.breaking_change_requests[0].breaking is True
    assert draft.ok is False  # both conditions correctly block "ready to review"
    assert not (DOMAIN / "cdm" / "2.0.yaml").exists()  # never applied as a real version bump
