from astra_data.bundle import Target

from astra_verification.pii import MASK, check_access_history, check_bindings, check_masking, run_checks


class FakeExecutor:
    """Answers queries by marker; the mask depends on the role in session."""

    def __init__(self, bindings: list[tuple], tag_value: str | None, raw_line: str | None, access: tuple = (0, None, None)) -> None:
        self.bindings, self.tag_value, self.raw_line, self.access = bindings, tag_value, raw_line, access
        self.role = None
        self.scripts: list[str] = []
        self.queries: list[str] = []

    def execute_script(self, sql: str) -> None:
        self.scripts.append(sql)
        if sql.startswith("USE ROLE"):
            self.role = sql.split('"')[1]

    def query(self, sql: str) -> list[tuple]:
        self.queries.append(sql)
        if "POLICY_REFERENCES" in sql:
            return self.bindings
        if "SYSTEM$GET_TAG" in sql:
            return [(self.tag_value,)]
        if "PII_ACCESS" in sql:
            return [self.access]
        if 'FROM "ASTRA_DEV"."BRONZE"."RAW_LINES"' in sql:
            if self.raw_line is None:
                return []
            return [(MASK if self.role == "ASTRA_DEV_AUDITOR" else self.raw_line,)]
        raise AssertionError(f"unexpected query {sql}")


TARGET = Target("dev")
BOUND = [("PII_STRING", "MASKING_POLICY"), ("PII_NUMBER", "MASKING_POLICY"), ("PII_DATE", "MASKING_POLICY")]


def test_bindings_pass_when_all_three_policies_and_the_column_tag_are_present():
    executor = FakeExecutor(BOUND, "raw_record", None)
    results = check_bindings(executor, TARGET)
    assert [r.passed for r in results] == [True, True]
    assert "PII_DATE, PII_NUMBER, PII_STRING" in results[0].detail
    assert "REF_ENTITY_NAME => 'ASTRA_DEV.CONTROL.PII', REF_ENTITY_DOMAIN => 'TAG'" in executor.queries[0]
    assert "'ASTRA_DEV.BRONZE.RAW_LINES.LINE', 'COLUMN'" in executor.queries[1]


def test_bindings_fail_when_a_policy_or_the_tag_is_missing():
    results = check_bindings(FakeExecutor(BOUND[:2], None, None), TARGET)
    assert [r.passed for r in results] == [False, False]
    assert results[1].detail == "BRONZE.RAW_LINES.LINE PII=None"


def test_masking_compares_the_same_row_under_both_roles():
    executor = FakeExecutor(BOUND, "raw_record", "HDR20260906ACME  0001234567")
    result = check_masking(executor, TARGET, "ASTRA_DEV_ENGINEER", "ASTRA_DEV_AUDITOR")
    assert result.passed
    assert executor.scripts == ['USE ROLE "ASTRA_DEV_ENGINEER"', 'USE ROLE "ASTRA_DEV_AUDITOR"']
    assert "ASTRA_DEV_AUDITOR sees '*****'" in result.detail


def test_masking_fails_when_the_restricted_role_sees_the_value():
    class Leaky(FakeExecutor):
        def query(self, sql: str) -> list[tuple]:
            if 'FROM "ASTRA_DEV"."BRONZE"."RAW_LINES"' in sql:
                return [(self.raw_line,)]
            return super().query(sql)

    result = check_masking(Leaky(BOUND, "raw_record", "secret line"), TARGET, "ASTRA_DEV_ENGINEER", "ASTRA_DEV_AUDITOR")
    assert not result.passed


def test_masking_is_inconclusive_but_not_failed_without_rows():
    result = check_masking(FakeExecutor(BOUND, "raw_record", None), TARGET, "ASTRA_DEV_ENGINEER", "ASTRA_DEV_AUDITOR")
    assert result.passed and "no raw lines to compare yet" in result.detail


def test_access_history_reports_the_window():
    result = check_access_history(FakeExecutor(BOUND, "raw_record", None, access=(12, "2026-09-01 08:00:00", "2026-09-06 07:00:00")), TARGET)
    assert result.passed and result.detail == "12 PII column read(s) in the last 7 days, from 2026-09-01 08:00:00 to 2026-09-06 07:00:00"


def test_run_checks_returns_all_four():
    results = run_checks(FakeExecutor(BOUND, "raw_record", "line"), TARGET, "ASTRA_DEV_ENGINEER", "ASTRA_DEV_AUDITOR")
    assert [r.passed for r in results] == [True, True, True, True]
