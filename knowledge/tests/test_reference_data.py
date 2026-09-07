"""Reference-data feeds and the replication pattern (S2.3.3)."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from astra_knowledge.cdm import DomainPack, load_pack
from astra_knowledge.cli import main
from astra_knowledge.patterns.reference_data import DELETED, INSERTED, UPDATED, Replica, ReplicationError
from astra_knowledge.reference_data import Feed, load_reference_data

REPO = Path(__file__).resolve().parents[2]
DOMAINS = REPO / "domains"
CUSTODIAL = DOMAINS / "custodial"
T0 = datetime(2026, 9, 6, 2, 0, 0)
T1 = datetime(2026, 9, 7, 2, 0, 0)


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    pack, problems = load_pack(CUSTODIAL, REPO)
    assert problems == [], [p.format() for p in problems]
    assert pack.reference_data is not None
    return pack


@pytest.fixture(scope="module")
def securities(pack) -> Feed:
    return pack.reference_data.feed("security_master")


@pytest.fixture(scope="module")
def accounts(pack) -> Feed:
    return pack.reference_data.feed("account_xref")


# ---------------------------------------------------------------- the feeds


def test_the_pack_declares_the_security_master_and_the_account_cross_reference(pack, securities, accounts):
    assert [f.id for f in pack.reference_data.feeds] == ["security_master", "account_xref"]
    assert (securities.system, securities.table, securities.key) == ("SOS", "SECURITY_MASTER", ("SECURITY_ID",))
    assert securities.resolves.entity == "Security" and securities.resolves.identifiers == ("CUSIP", "ISIN", "SEDOL", "TICKER", "OCC_SYMBOL")
    assert (securities.rejections.not_found, securities.rejections.ambiguous, securities.rejections.conflict) == ("SECURITY_NOT_FOUND", "SECURITY_AMBIGUOUS", "REFERENCE_DATA_CONFLICT")
    assert (accounts.system, accounts.table, accounts.key) == ("CAS", "ACCOUNT_XREF", ("CUSTODIAN_ID", "CUSTODIAN_ACCOUNT_NUMBER"))
    assert accounts.resolves.entity == "Account" and accounts.resolves.identifiers == ()
    assert accounts.column("CUSTODIAN_ACCOUNT_NUMBER").pii == "account_number"
    for feed in pack.reference_data.feeds:
        assert feed.schedule.timezone == "America/New_York" and feed.expected_every_hours == 26
        assert all(feed.column(k).required for k in feed.key)


def _mutated(tmp_path: Path, mutate) -> list[str]:
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    data = yaml.safe_load((root / "reference-data.yaml").read_text(encoding="utf-8"))
    mutate(data)
    (root / "reference-data.yaml").write_text(yaml.safe_dump(data, sort_keys=False, width=1000), encoding="utf-8")
    _, problems = load_pack(root, tmp_path)
    return [p.message for p in problems]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["feeds"][0]["resolves"].update(entity="Instrument"), "feed 'security_master' resolves entity 'Instrument', which is in no model version"),
        (lambda d: d["feeds"][0]["rejections"].update(not_found="SECURITY_GONE"), "feed 'security_master' rejections.not_found names SECURITY_GONE, which is not in the rejection taxonomy"),
        (lambda d: d["feeds"][0]["resolves"].update(identifiers=["CUSIP", "NOWHERE"]), "identifier 'NOWHERE' is not a column of feed 'security_master'"),
        (lambda d: d["feeds"][0]["resolves"].update(identifiers=["SECURITY_ID"]), "identifier 'SECURITY_ID' of feed 'security_master' is a key column"),
        (lambda d: d["feeds"][1]["columns"][1].update(required=False), "key column 'CUSTODIAN_ACCOUNT_NUMBER' of feed 'account_xref' must be required"),
        (lambda d: d["feeds"][1].update(id="security_master"), "feed 'security_master' is defined twice"),
        (lambda d: d["feeds"][1].update(table="SECURITY_MASTER"), "table SECURITY_MASTER is used by both 'security_master' and 'account_xref'"),
        (lambda d: d.update(domain="wealth"), "reference data domain 'wealth' must match the pack directory 'custodial'"),
        (lambda d: d["feeds"][0]["schedule"].update(cron="every night"), "feeds[0].schedule.cron"),
        (lambda d: d["feeds"][0].pop("expected_every_hours"), "missing required field expected_every_hours"),
    ],
)
def test_the_validator_rejects_feeds_that_do_not_hold_together(tmp_path, mutate, expected):
    messages = _mutated(tmp_path, mutate)
    assert any(expected in m for m in messages), messages


def test_a_pack_without_reference_data_is_allowed(tmp_path):
    root = tmp_path / "domains" / "custodial"
    shutil.copytree(CUSTODIAL, root)
    (root / "reference-data.yaml").unlink()
    pack, problems = load_pack(root, tmp_path)
    assert problems == [] and pack.reference_data is None


# ------------------------------------------------------------- replication


def sec(security_id: str, cusip: str | None = None, isin: str | None = None, description: str = "Apple Inc", status: str = "ACTIVE", **extra) -> dict:
    row = {"SECURITY_ID": security_id, "CUSIP": cusip, "ISIN": isin, "SEDOL": None, "TICKER": None, "OCC_SYMBOL": None, "DESCRIPTION": description, "ASSET_CLASS": "EQUITY", "SECURITY_TYPE": None, "ISSUER": None, "CURRENCY": "USD", "PRICE_FACTOR": Decimal("1"), "MATURITY_DATE": None, "STATUS": status, "SOURCE_UPDATED_AT": None}
    row.update(extra)
    return row


def test_the_first_snapshot_inserts_everything_and_records_the_run(securities):
    replica = Replica(securities)
    run = replica.replicate([sec("S1", "037833100"), sec("S2", "594918104", description="Microsoft")], date(2026, 9, 6), T0, run_id="run1")
    assert (run.status, run.rows_source, run.rows_inserted, run.rows_updated, run.rows_deleted, run.rows_unchanged, run.rows_conflict, run.rows_total) == ("succeeded", 2, 2, 0, 0, 0, 0, 2)
    assert replica.runs == [run] and sorted(replica.rows) == [("S1",), ("S2",)]
    assert [(c.change, c.key, c.before) for c in replica.changes] == [(INSERTED, ("S1",), None), (INSERTED, ("S2",), None)]
    assert replica.changes[0].after["CUSIP"] == "037833100"
    assert replica.rows[("S1",)].run_id == "run1" and replica.rows[("S1",)].replicated_at == T0


def test_the_next_snapshot_yields_the_delta_with_before_and_after(securities):
    replica = Replica(securities)
    replica.replicate([sec("S1", "037833100"), sec("S2", "594918104", description="Microsoft"), sec("S3", "88160R101", description="Tesla")], date(2026, 9, 6), T0, run_id="run1")
    run = replica.replicate([sec("S1", "037833100"), sec("S2", "594918104", description="Microsoft Corp"), sec("S4", "023135106", description="Amazon")], date(2026, 9, 7), T1, run_id="run2")
    assert (run.rows_source, run.rows_inserted, run.rows_updated, run.rows_deleted, run.rows_unchanged, run.rows_total) == (3, 1, 1, 1, 1, 3)
    delta = replica.last_delta()
    # Deletions first, then the snapshot in its own order.
    assert [(c.change, c.key) for c in delta] == [(DELETED, ("S3",)), (UPDATED, ("S2",)), (INSERTED, ("S4",))]
    updated = next(c for c in delta if c.change == UPDATED)
    assert updated.before["DESCRIPTION"] == "Microsoft" and updated.after["DESCRIPTION"] == "Microsoft Corp"
    deleted = next(c for c in delta if c.change == DELETED)
    assert deleted.before["SECURITY_ID"] == "S3" and deleted.after is None
    assert replica.changes_since("run1") == delta and len(replica.changes_since(None)) == 6
    assert replica.rows[("S2",)].run_id == "run2" and replica.rows[("S1",)].run_id == "run1"


def test_conflicting_keys_stay_out_and_the_replica_keeps_what_it_had(securities):
    replica = Replica(securities)
    replica.replicate([sec("S1", "037833100"), sec("S2", "594918104")], date(2026, 9, 6), T0, run_id="run1")
    run = replica.replicate([sec("S1", "037833100"), sec("S2", "AAAAAAAAA"), sec("S2", "BBBBBBBBB"), sec(None, "CCCCCCCCC")], date(2026, 9, 7), T1, run_id="run2")
    assert (run.rows_source, run.rows_conflict, run.rows_inserted, run.rows_updated, run.rows_deleted, run.rows_unchanged, run.rows_total) == (4, 3, 0, 0, 0, 1, 2)
    assert [(c.key, c.rows, c.code) for c in replica.conflicts] == [(("S2",), 2, "REFERENCE_DATA_CONFLICT"), ((None,), 1, "REFERENCE_DATA_CONFLICT")]
    assert replica.rows[("S2",)].values["CUSIP"] == "594918104"  # untouched
    assert replica.last_delta() == []


def test_a_snapshot_without_the_feeds_columns_fails_the_run(securities):
    replica = Replica(securities)
    incomplete = {name: value for name, value in sec("S1", "037833100").items() if name not in ("CURRENCY", "STATUS")}
    with pytest.raises(ReplicationError, match="lacks columns CURRENCY, STATUS"):
        replica.replicate([incomplete], date(2026, 9, 6), T0, run_id="run1")
    assert replica.runs[-1].status == "failed" and "lacks" in replica.runs[-1].error and replica.rows == {}


# -------------------------------------------------------------- resolution


def test_securities_resolve_by_identifiers_in_order(securities):
    replica = Replica(securities)
    replica.replicate([sec("S1", "037833100", "US0378331005"), sec("S2", "594918104", "US5949181045"), sec("S3", None, "US88160R1014")], date(2026, 9, 6), T0)
    found = replica.resolve(CUSIP="594918104")
    assert found.status == "found" and found.row.key == ("S2",) and found.by == "CUSIP" and found.code is None
    by_isin = replica.resolve(CUSIP=None, ISIN="US88160R1014")
    assert by_isin.status == "found" and by_isin.row.key == ("S3",) and by_isin.by == "ISIN"
    # CUSIP is tried first and wins even when the ISIN names another row.
    assert replica.resolve(CUSIP="037833100", ISIN="US88160R1014").row.key == ("S1",)
    by_key = replica.resolve(SECURITY_ID="S3")
    assert by_key.status == "found" and by_key.by == "SECURITY_ID"

    missing = replica.resolve(CUSIP="999999999", ISIN=" ")
    assert (missing.status, missing.row, missing.code) == ("not_found", None, "SECURITY_NOT_FOUND")
    assert replica.resolve().status == "not_found"


def test_an_identifier_shared_by_two_rows_is_ambiguous(securities):
    replica = Replica(securities)
    replica.replicate([sec("S1", "037833100"), sec("S1B", "037833100", description="Apple (dup)")], date(2026, 9, 6), T0)
    result = replica.resolve(CUSIP="037833100")
    assert result.status == "ambiguous" and result.code == "SECURITY_AMBIGUOUS" and {r.key for r in result.rows} == {("S1",), ("S1B",)} and result.row is None


def test_accounts_resolve_by_their_key(accounts):
    replica = Replica(accounts)
    row = {"CUSTODIAN_ID": "pershing", "CUSTODIAN_ACCOUNT_NUMBER": "ACC0000001", "ACCOUNT_ID": "A-1", "FIRM_ID": "F-1", "ACCOUNT_NAME": None, "STATUS": "OPEN", "OPENED_ON": None, "CLOSED_ON": None, "SOURCE_UPDATED_AT": None}
    replica.replicate([row], date(2026, 9, 6), T0)
    found = replica.resolve(CUSTODIAN_ID="pershing", CUSTODIAN_ACCOUNT_NUMBER="ACC0000001")
    assert found.status == "found" and found.row.values["ACCOUNT_ID"] == "A-1"
    missing = replica.resolve(CUSTODIAN_ID="pershing", CUSTODIAN_ACCOUNT_NUMBER="ACC0000009")
    assert missing.status == "not_found" and missing.code == "ACCOUNT_NOT_FOUND"
    assert replica.resolve(CUSTODIAN_ID="pershing").status == "not_found"


# ---------------------------------------------------------------------- CLI


def test_cli_lists_the_feeds(capsys):
    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "reference", "list"]) == 0
    out = capsys.readouterr().out
    assert "security_master  Security master (SOS)  REFERENCE.SECURITY_MASTER  key SECURITY_ID" in out
    assert "resolves Security by CUSIP, ISIN, SEDOL, TICKER, OCC_SYMBOL; not found SECURITY_NOT_FOUND" in out
    assert "account_xref  Account cross-reference (CAS)  REFERENCE.ACCOUNT_XREF  key CUSTODIAN_ID, CUSTODIAN_ACCOUNT_NUMBER" in out
    assert "resolves Account by the key" in out and "runs 0 2 * * * America/New_York; expected at least every 26 hours" in out

    assert main(["--root", str(REPO), "--domains", str(DOMAINS), "--format", "json", "reference", "list"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [f["id"] for f in payload] == ["security_master", "account_xref"] and payload[0]["domain"] == "custodial"


def test_load_reference_data_reports_a_bad_file(tmp_path):
    path = tmp_path / "reference-data.yaml"
    path.write_text("reference_data_version: 1\n", encoding="utf-8")
    _, problems = load_reference_data(path, tmp_path)
    assert [p.message for p in problems] == ["reference_data_version must be 0; found 1"]
