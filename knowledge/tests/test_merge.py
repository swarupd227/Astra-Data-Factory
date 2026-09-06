from datetime import date
from decimal import Decimal
from pathlib import Path

from astra_knowledge.patterns import SilverState, merge, parse
from astra_knowledge.patterns.merge import merge_mode
from astra_knowledge.registry import Registry
from tests.test_fixed_width import dtl, hdr, trl

REPO = Path(__file__).resolve().parents[2]
SPECS = REPO / "specs"


def gcus():
    registry, problems = Registry.load(SPECS, REPO)
    assert problems == [], [p.message for p in problems]
    return registry.get("pershing_gcus", "2017-07-25")


def file(flag: str, file_date: str, details: list[str], remote: str = "RMT0000001"):
    spec = gcus()
    parsed = parse(spec, [hdr(file_date=file_date, remote=remote, flag=flag), *details, trl(str(len(details)).zfill(9))])
    assert parsed.ok, [p.text() for p in parsed.problems]
    return spec, parsed


def positions(state: SilverState, remote: str = "RMT0000001") -> dict:
    return {row.key: row.values["quantity"] for row in state.in_scope((remote,)).values()}


def test_the_gcus_spec_declares_the_merge_rule():
    rule = gcus().merge
    assert rule.mode_field == "refresh_flag" and rule.modes == {"R": "refresh", "U": "update"}
    assert rule.scope == ("remote_id",) and rule.business_date_field == "file_date" and rule.keys == ("account_number", "cusip")
    assert rule.mode_for("R") == "refresh" and rule.mode_for("U ") == "update" and rule.mode_for("X") is None


def test_refresh_replaces_all_positions_for_the_remote_id_as_of_the_business_date():
    state = SilverState()
    spec, first = file("R", "20260901", [dtl(account="ACC0000001"), dtl(account="ACC0000002"), dtl(account="ACC0000003")])
    result = merge(state, spec, first, "day1.dat")
    assert result.applied and (result.mode, result.inserted, result.updated, result.carried, result.retired) == ("refresh", 3, 0, 0, 0)
    assert result.business_date == date(2026, 9, 1) and result.scope == ("RMT0000001",)

    spec, second = file("R", "20260902", [dtl(account="ACC0000001", qty="000000000020000000"), dtl(account="ACC0000004")])
    result = merge(state, spec, second, "day2.dat")
    assert (result.inserted, result.updated, result.carried, result.retired) == (1, 1, 0, 2)
    assert sorted(positions(state)) == [("ACC0000001", "123456789"), ("ACC0000004", "123456789")]
    assert positions(state)[("ACC0000001", "123456789")] == Decimal("200.00000")
    row = state.rows[("RMT0000001", "ACC0000001", "123456789")]
    assert row.business_date == date(2026, 9, 2) and row.first_file == "day1.dat" and row.last_file == "day2.dat"


def test_update_merges_on_keys_and_carries_untouched_rows_forward():
    state = SilverState()
    spec, full = file("R", "20260901", [dtl(account="ACC0000001"), dtl(account="ACC0000002"), dtl(account="ACC0000003")])
    merge(state, spec, full, "day1.dat")

    spec, delta = file("U", "20260902", [dtl(account="ACC0000002", qty="000000000099900000"), dtl(account="ACC0000009")])
    result = merge(state, spec, delta, "day2.dat")
    assert result.mode == "update" and (result.inserted, result.updated, result.carried, result.retired) == (1, 1, 2, 0)
    current = positions(state)
    assert sorted(current) == [("ACC0000001", "123456789"), ("ACC0000002", "123456789"), ("ACC0000003", "123456789"), ("ACC0000009", "123456789")]
    assert current[("ACC0000002", "123456789")] == Decimal("999.00000") and current[("ACC0000001", "123456789")] == Decimal("123.45678")
    assert state.rows[("RMT0000001", "ACC0000001", "123456789")].business_date == date(2026, 9, 1)


def test_a_refresh_only_touches_its_own_scope():
    state = SilverState()
    spec, a = file("R", "20260901", [dtl(account="ACC0000001")], remote="RMT0000001")
    merge(state, spec, a, "a.dat")
    spec, b = file("R", "20260901", [dtl(account="ACC0000001")], remote="RMT0000002")
    result = merge(state, spec, b, "b.dat")
    assert result.retired == 0 and len(state.rows) == 2
    assert positions(state, "RMT0000001") and positions(state, "RMT0000002")


def test_unknown_mode_missing_header_and_out_of_order_files_are_not_merged():
    state = SilverState()
    spec, good = file("R", "20260905", [dtl()])
    merge(state, spec, good, "day5.dat")

    # The parser already flags 'X' as an undeclared code; the merge then refuses the file.
    unknown = parse(spec, [hdr(file_date="20260906", flag="X"), dtl(), trl("000000001")])
    assert [p.field for p in unknown.problems] == ["refresh_flag"] and not unknown.rejected
    result = merge(state, spec, unknown, "bad.dat")
    assert not result.applied and result.problems[0].code == "MERGE_MODE_UNKNOWN"
    assert result.problems[0].message == "merge mode 'X' in header field refresh_flag is not one of 'R' = refresh, 'U' = update"

    spec, older = file("U", "20260904", [dtl(account="ACC0000002")])
    result = merge(state, spec, older, "old.dat")
    assert not result.applied and result.problems[0].code == "MERGE_OUT_OF_ORDER"
    assert len(state.rows) == 1

    headless = parse(spec, [dtl(), trl("000000001")])
    mode, problem = merge_mode(spec, headless)
    assert mode is None and problem.code == "MERGE_MODE_UNKNOWN" and problem.level == "file"


def test_duplicate_and_blank_keys_within_a_file_are_reported():
    state = SilverState()
    spec, parsed = file("R", "20260905", [dtl(account="ACC0000001"), dtl(account="ACC0000001", qty="000000000000000100")])
    result = merge(state, spec, parsed, "dup.dat")
    assert result.applied and result.inserted == 1
    assert result.problems[0].code == "MERGE_DUPLICATE_KEY" and result.problems[0].line_number == 3
    assert positions(state)[("ACC0000001", "123456789")] == Decimal("123.45678")


def test_log_entry_is_what_the_platform_records():
    state = SilverState()
    spec, parsed = file("R", "20260905", [dtl(), dtl(account="ACC0000002")])
    entry = merge(state, spec, parsed, "GCUS_20260905.dat").log_entry(spec, "GCUS_20260905.dat")
    assert entry.source == "pershing_gcus 2017-07-25" and entry.scope == "remote_id=RMT0000001"
    assert entry.mode == "refresh" and entry.business_date == date(2026, 9, 5)
    assert (entry.inserted, entry.updated, entry.carried, entry.retired) == (2, 0, 0, 0) and entry.file_name == "GCUS_20260905.dat"


def test_registry_validates_the_merge_block(tmp_path):
    folder = tmp_path / "specs" / "pershing_gcus"
    folder.mkdir(parents=True)
    text = (SPECS / "pershing_gcus" / "2017-07-25.yaml").read_text(encoding="utf-8")

    def check(transform, expected: str) -> None:
        (folder / "2017-07-25.yaml").write_text(transform(text), encoding="utf-8")
        _, problems = Registry.load(tmp_path / "specs", tmp_path)
        assert any(expected in p.message for p in problems), [p.message for p in problems]

    check(lambda t: t.replace("mode_field: refresh_flag", "mode_field: refresh_mode"), "merge.mode_field 'refresh_mode' is not a field of the header record")
    check(lambda t: t.replace("modes: { R: refresh, U: update }", "modes: { R: refresh, X: update }"), "merge.modes code 'X' is not a declared code of 'refresh_flag'")
    check(lambda t: t.replace("modes: { R: refresh, U: update }", "modes: { R: refresh, U: refresh }"), "merge.modes must map codes to both refresh and update")
    check(lambda t: t.replace("scope: [remote_id]", "scope: [branch_id]"), "merge.scope field 'branch_id' is not a field of the header record")
    check(lambda t: t.replace("business_date_field: file_date", "business_date_field: remote_id"), "merge.business_date_field 'remote_id' must be a date field")
    check(lambda t: t.replace("keys: [account_number, cusip]\n\nrecords:", "keys: [account_number, isin]\n\nrecords:"), "merge key 'isin' is not a field of 'detail'")
