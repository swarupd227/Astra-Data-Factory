"""The resolution stage (S3.2.4). Orphan policy and seeded-data reproduction: S7.1.3, ADR 0077."""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from astra_knowledge.cdm import load_packs
from astra_knowledge.registry import Registry
from astra_knowledge.rules import Catalog

from astra_data.compiler import CompileError, PriceResolution, compile_config
from astra_data.render import render_bundle
from astra_data.render.resolve import resolve_price, resolved_price
from tests.test_validate import EXAMPLE, VALID

REPO = EXAMPLE.parents[2]


@pytest.fixture(scope="module")
def inputs():
    registry, problems = Registry.load(REPO / "specs", REPO)
    assert problems == []
    catalog, problems = Catalog.load(REPO / "rules", REPO, registry)
    assert problems == []
    packs, problems = load_packs(REPO / "domains", REPO)
    assert problems == []
    return registry, catalog, packs


@pytest.fixture(scope="module")
def resolve(inputs) -> str:
    registry, catalog, packs = inputs
    return render_bundle(compile_config(EXAMPLE, registry=registry, catalog=catalog, packs=packs, root=REPO))["pipeline/pershing_position_resolve.sql"]


def test_account_resolves_by_join_and_an_unresolved_account_is_an_exception_with_payload(resolve):
    assert 'CREATE OR REPLACE PROCEDURE {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RESOLVE"(RUN_ID STRING)' in resolve
    assert 'LEFT JOIN {{ DATABASE }}."REFERENCE"."ACCOUNT_XREF" a ON a."CUSTODIAN_ID" = \'pershing\' AND a."CUSTODIAN_ACCOUNT_NUMBER" = s."ACCOUNT_NUMBER"' in resolve
    assert "'ACCOUNT_NOT_FOUND', 'record', 'resolution', 'Position', 'pershing', 'account_number'" in resolve
    assert "TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*))" in resolve and 'FROM {{ DATABASE }}."BRONZE"."PERSHING_POSITION_RESOLVED" s WHERE s."R_ACCOUNT_ID" IS NULL;' in resolve
    assert "'ACCOUNT_CLOSED', 'record', 'resolution'" in resolve and "s.\"R_ACCOUNT_STATUS\" = 'CLOSED'" in resolve
    assert "'account_number=' || COALESCE(s.\"ACCOUNT_NUMBER\"::STRING, 'NULL') || ', ' || 'cusip=' || COALESCE(s.\"CUSIP\"::STRING, 'NULL')" in resolve


def test_security_resolves_by_the_configured_identifiers_in_order(resolve):
    assert 'LEFT JOIN (SELECT "IDENTIFIER_VALUE", MIN("SECURITY_ID") AS "SECURITY_ID", COUNT(*) AS "MATCHES" FROM {{ DATABASE }}."REFERENCE"."SECURITY_MASTER_IDENTIFIERS" WHERE "IDENTIFIER_TYPE" = \'CUSIP\' GROUP BY "IDENTIFIER_VALUE") i1 ON i1."IDENTIFIER_VALUE" = s."CUSIP"' in resolve
    assert "CASE WHEN i1.\"MATCHES\" > 1 THEN 'ambiguous' WHEN i1.\"MATCHES\" = 1 THEN 'found' ELSE 'not_found' END AS \"R_SECURITY_STATUS\"" in resolve
    assert "'SECURITY_NOT_FOUND', 'record', 'resolution'" in resolve and "'SECURITY_AMBIGUOUS', 'record', 'resolution'" in resolve
    assert "'SECURITY_INACTIVE', 'record', 'resolution'" in resolve and "s.\"R_SECURITY_MASTER_STATUS\" = 'INACTIVE'" in resolve
    hold = resolve[resolve.index("WHERE NOT ("):]
    assert "s.\"R_SECURITY_STATUS\" = 'not_found' OR s.\"R_SECURITY_STATUS\" = 'ambiguous'" in hold and "INACTIVE" not in hold  # inactive is a warning unless require_active


def test_price_is_looked_up_when_missing_and_rows_project_into_the_canonical_entity(resolve):
    assert 'COALESCE(s."R_SOURCE_PRICE", (SELECT p."PRICE" FROM {{ DATABASE }}."SILVER"."PRICE" p WHERE p."SECURITY_ID" = s."R_SECURITY_ID" AND p."PRICE_TYPE" = \'CLOSE\' AND p."PRICE_DATE" <= s."R_PRICE_AS_OF" AND p."PRICE_DATE" >= DATEADD(\'day\', -5, s."R_PRICE_AS_OF") ORDER BY p."PRICE_DATE" DESC LIMIT 1))' in resolve
    assert "'PRICE_MISSING', 'record', 'resolution'" in resolve
    assert 'MERGE INTO {{ DATABASE }}."SILVER"."POSITION" t' in resolve
    assert 'ON t."CUSTODIAN_ID" = r."CUSTODIAN_ID" AND t."ACCOUNT_NUMBER" = r."ACCOUNT_NUMBER" AND t."SECURITY_ID" = r."SECURITY_ID" AND t."AS_OF_DATE" = r."AS_OF_DATE"' in resolve
    for projected in (
        "'pershing' AS \"CUSTODIAN_ID\"",
        's."ACCOUNT_NUMBER" AS "ACCOUNT_NUMBER"',
        's."R_SECURITY_ID" AS "SECURITY_ID"',
        's."AS_OF_DATE" AS "AS_OF_DATE"',
        's."CUSIP" AS "CUSTODIAN_SECURITY_ID"',
        "'LONG'::STRING AS \"POSITION_TYPE\"",
        's."QUANTITY" AS "QUANTITY"',
        's."R_PRICE" AS "PRICE"',
        "'USD'::STRING AS \"CURRENCY\"",
        "'pershing' AS \"SOURCE_SYSTEM\"",
        's."LAST_FILE" AS "SOURCE_FILE"',
    ):
        assert projected in resolve, projected
    assert '"UPDATED_AT" = SYSDATE()' in resolve and 'WHERE s."RUN_ID" = :RUN_ID AND s."RETIRED_AT" IS NULL' in resolve


def _transaction_config(tmp_path: Path, inputs, tx_map: str = "{ BUY: BUY, SELL: SELL, DIV: DIVIDEND, DRIP: BUY }", unmapped: str = ""):
    registry_root = tmp_path / "specs"
    if not registry_root.exists():
        shutil.copytree(REPO / "specs", registry_root)
        spec_path = registry_root / "drip_transaction_example" / "2026-01-01.yaml"
        text = spec_path.read_text(encoding="utf-8")
        text = text.replace("lifecycle:", "merge:\n  mode_field: refresh_flag\n  modes: { F: refresh, D: update }\n  scope: []\n  business_date_field: file_date\n  keys: [transaction_id]\n\nlifecycle:", 1)
        text = text.replace(
            "      - { name: filler, position: { start: 10, length: 91 }, picture: X(91), citation: { page: 2, line: 7 } }",
            "      - { name: refresh_flag, position: { start: 10, length: 1 }, picture: X(1), type: code, citation: { page: 2, line: 6 }, codes: [{ value: F, meaning: full }, { value: D, meaning: delta }] }\n"
            "      - { name: filler, position: { start: 11, length: 90 }, picture: X(90), citation: { page: 2, line: 7 } }",
        )
        spec_path.write_text(text, encoding="utf-8")
    registry, problems = Registry.load(registry_root, tmp_path)
    assert problems == [], [p.format() for p in problems]
    _, catalog, packs = inputs
    config = f"""config_version: 0
source:
  id: example_transactions
  custodian: example_custodian
  file_type: transaction
  tier: medium
spec:
  id: drip_transaction_example
  version: "2026-01-01"
target_profile: snowflake_iceberg
domain_pack: custodial
effective_from: 2026-01-01
owner:
  name: Data steward, custodial
  email: steward@example.com
mappings:
  - {{ target: transaction.transaction_id, source: transaction_id }}
  - {{ target: transaction.source_transaction_id, source: transaction_id }}
  - {{ target: transaction.account_number, source: account_number }}
  - {{ target: transaction.custodian_security_id, source: cusip }}
  - {{ target: transaction.trade_date, source: trade_date }}
  - {{ target: transaction.quantity, source: quantity, transform: "implied_decimal(4)" }}
  - {{ target: transaction.net_amount, source: amount, transform: "signed_implied_decimal(11, 2)" }}
  - {{ target: transaction.currency, constant: USD }}
  - {{ target: transaction.status, constant: ACTIVE }}
resolution:
  account:
    source: account_number
  security:
    by:
      - {{ identifier: CUSIP, source: cusip }}
      - {{ identifier: OCC_SYMBOL, source: cusip }}
  transaction_code:
    source: transaction_type
    map: {tx_map}
{unmapped}
delivery:
  cutoff_time: "06:00"
  timezone: UTC
  files:
    - pattern: example/TXN_%.dat
"""
    path = tmp_path / "example_transactions.yaml"
    path.write_text(config, encoding="utf-8")
    return compile_config(path, registry=registry, catalog=catalog, packs=packs, root=tmp_path)


def test_a_missing_transaction_code_mapping_raises_the_configured_code(tmp_path, inputs):
    compiled = _transaction_config(tmp_path, inputs)
    sql = render_bundle(compiled)["pipeline/example_transactions_resolve.sql"]
    assert "LEFT JOIN (SELECT * FROM VALUES ('BUY', 'BUY'), ('SELL', 'SELL'), ('DIV', 'DIVIDEND'), ('DRIP', 'BUY') AS v (\"CODE\", \"TYPE\")) tc ON tc.\"CODE\" = s.\"TRANSACTION_TYPE\"" in sql
    assert "'TRANSACTION_CODE_UNMAPPED', 'record', 'resolution', 'Transaction', 'example_custodian', 'transaction_type'" in sql
    assert 's."R_TRANSACTION_TYPE" AS "TRANSACTION_TYPE"' in sql and 's."TRANSACTION_TYPE" AS "CUSTODIAN_TRANSACTION_CODE"' in sql
    # identifiers tried in order: CUSIP, then the OCC symbol
    assert "WHERE \"IDENTIFIER_TYPE\" = 'CUSIP'" in sql and "WHERE \"IDENTIFIER_TYPE\" = 'OCC_SYMBOL'" in sql
    assert "CASE WHEN i1.\"MATCHES\" > 1 THEN 'ambiguous' WHEN i1.\"MATCHES\" = 1 THEN 'found' WHEN i2.\"MATCHES\" > 1 THEN 'ambiguous' WHEN i2.\"MATCHES\" = 1 THEN 'found' ELSE 'not_found' END" in sql
    assert 'MERGE INTO {{ DATABASE }}."SILVER"."TRANSACTION" t' in sql and 'ON t."CUSTODIAN_ID" = r."CUSTODIAN_ID" AND t."TRANSACTION_ID" = r."TRANSACTION_ID"' in sql

    configured = _transaction_config(tmp_path / "other", inputs, unmapped="    unmapped: TRANSACTION_CODE_AMBIGUOUS")
    assert "'TRANSACTION_CODE_AMBIGUOUS', 'record', 'resolution'" in render_bundle(configured)["pipeline/example_transactions_resolve.sql"]


def test_transaction_code_map_must_yield_canonical_types(tmp_path, inputs):
    with pytest.raises(CompileError) as excinfo:
        _transaction_config(tmp_path, inputs, tx_map="{ BUY: PURCHASE }")
    assert any("custodian code 'BUY' maps to 'PURCHASE', which is not a canonical transaction type" in p.message for p in excinfo.value.problems)


# ---------------------------------------------------------------- orphan policy (S7.1.3, ADR 0077)


def test_orphan_policy_defaults_to_24_hours_and_is_rendered_into_the_procedure_comment(resolve):
    assert "past 24h unresolved it is an aged orphan (ADR 0077)" in resolve


def test_orphan_policy_grace_hours_is_configurable_per_custodian(tmp_path, inputs):
    compiled = _transaction_config(tmp_path, inputs)
    assert compiled.resolution.orphan_policy.grace_hours == 24  # the default, unconfigured
    assert compiled.to_dict()["resolution"]["orphan_policy"] == {"grace_hours": 24}

    text = (tmp_path / "example_transactions.yaml").read_text(encoding="utf-8")
    text = text.replace("resolution:\n  account:", "resolution:\n  orphan_policy:\n    grace_hours: 72\n  account:")
    (tmp_path / "example_transactions.yaml").write_text(text, encoding="utf-8")
    registry, problems = Registry.load(tmp_path / "specs", tmp_path)  # the tmp_path registry _transaction_config patched with a merge block
    assert problems == []
    _, catalog, packs = inputs
    configured = compile_config(tmp_path / "example_transactions.yaml", registry=registry, catalog=catalog, packs=packs, root=tmp_path)
    assert configured.resolution.orphan_policy.grace_hours == 72
    sql = render_bundle(configured)["pipeline/example_transactions_resolve.sql"]
    assert "past 72h unresolved it is an aged orphan (ADR 0077)" in sql


def test_orphan_policy_never_changes_which_rows_are_held(resolve):
    """A held row is always held -- the policy only names when it is an aged concern, never
    whether it merges; this is what keeps Loader parity (never silently drop data) unconditional."""
    assert 'WHERE NOT (' in resolve
    hold = resolve[resolve.index("WHERE NOT ("):]
    assert "grace_hours" not in hold and "24" not in hold and "ADR 0077" not in hold


# ---------------------------------------------------------------- rejection codes reproduced on seeded data


def test_transaction_code_reproduces_unmapped_and_every_real_mapping_on_seeded_codes(tmp_path, inputs):
    """The compiled resolution.transaction_code.map IS the reference: it is the exact dict the
    rendered VALUES table is built from (test_a_missing_transaction_code_mapping_raises_the_
    configured_code, above), so seeding real custodian codes through it directly reproduces what
    the rendered SQL's join would resolve each to -- no separate reimplementation needed."""
    compiled = _transaction_config(tmp_path, inputs)
    mapping = compiled.resolution.transaction_code.map
    assert mapping.get("BUY") == "BUY" and mapping.get("SELL") == "SELL" and mapping.get("DIV") == "DIVIDEND" and mapping.get("DRIP") == "BUY"
    assert mapping.get("ZZ") is None  # TRANSACTION_CODE_UNMAPPED: no mapping, held back
    assert compiled.resolution.transaction_code.unmapped == "TRANSACTION_CODE_UNMAPPED"


def test_price_resolves_the_most_recent_within_the_lookback_and_reproduces_price_missing():
    res = PriceResolution(when="missing", lookback_days=5, price_type="CLOSE", missing="PRICE_MISSING")
    as_of = date(2026, 9, 17)
    history = [(date(2026, 9, 15), Decimal("101.50")), (date(2026, 9, 12), Decimal("99.00"))]

    assert resolve_price(history, as_of, res) == Decimal("101.50")  # most recent within the window wins
    assert resolve_price([(date(2026, 9, 10), Decimal("50.00"))], as_of, res) is None  # older than the lookback: PRICE_MISSING
    assert resolve_price([], as_of, res) is None  # no price at all: PRICE_MISSING
    assert resolve_price([(as_of, Decimal("100.00"))], as_of, res) == Decimal("100.00")  # exactly on the boundary date


def test_resolved_price_prefers_the_sources_own_price_only_when_when_is_missing():
    missing = PriceResolution(when="missing", lookback_days=5, price_type="CLOSE", missing="PRICE_MISSING")
    always = PriceResolution(when="always", lookback_days=5, price_type="CLOSE", missing="PRICE_MISSING")
    as_of = date(2026, 9, 17)
    history = [(date(2026, 9, 16), Decimal("200.00"))]

    assert resolved_price(Decimal("199.99"), history, as_of, missing) == Decimal("199.99")  # the custodian's own price wins
    assert resolved_price(None, history, as_of, missing) == Decimal("200.00")  # none sent: fall back to the lookback
    assert resolved_price(Decimal("199.99"), history, as_of, always) == Decimal("200.00")  # "always": the lookback wins regardless
    assert resolved_price(None, [], as_of, missing) is None  # nothing sent, nothing in the lookback: PRICE_MISSING
