from dataclasses import dataclass

from astra_opencatalog.client import OpenCatalogClient
from astra_opencatalog.provision import EnvironmentSpec, provision
from astra_opencatalog.verify import check_access_denied, find_table, run_verification, wait_until_listed
from tests.conftest import FakeOpenCatalog

SPEC = EnvironmentSpec(prefix="astra", environment="dev", base_location="s3://b/", role_arn="arn:aws:iam::1:role/r")


class Clock:
    """Deterministic monotonic clock; sleep advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeProbe:
    fake: FakeOpenCatalog
    database: str = "ASTRA_DEV"
    schema: str = "SILVER"
    name: str = "OC_SYNC_PROBE"
    appear_after_polls: int = 0
    created: bool = False
    dropped: bool = False

    def create(self) -> None:
        self.created = True
        if self.appear_after_polls == 0:
            self.fake.add_table("astra_dev", (self.database, self.schema), self.name)

    def drop(self) -> None:
        self.dropped = True


def _catalog(fake: FakeOpenCatalog, admin: OpenCatalogClient) -> None:
    provision(admin, SPEC)


def test_find_table_searches_every_namespace(fake, admin):
    _catalog(fake, admin)
    fake.add_table("astra_dev", ("ASTRA_DEV", "GOLD"), "POSITION")
    assert find_table(admin, "astra_dev", "POSITION") == ["ASTRA_DEV", "GOLD"]
    assert find_table(admin, "astra_dev", "MISSING") is None


def test_wait_until_listed_passes_when_the_table_appears_in_time(fake, admin):
    _catalog(fake, admin)
    clock = Clock()
    polls = {"n": 0}

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        polls["n"] += 1
        if polls["n"] == 3:
            fake.add_table("astra_dev", ("ASTRA_DEV", "SILVER"), "PROBE")

    result = wait_until_listed(admin, "astra_dev", "PROBE", timeout_seconds=60, poll_seconds=2, clock=clock, sleep=sleep)
    assert result.passed
    assert result.seconds == 6.0
    assert "ASTRA_DEV.SILVER.PROBE listed after 6.0s" in result.detail


def test_wait_until_listed_fails_after_the_deadline(fake, admin):
    _catalog(fake, admin)
    clock = Clock()
    result = wait_until_listed(admin, "astra_dev", "NEVER", timeout_seconds=10, poll_seconds=4, clock=clock, sleep=clock.sleep)
    assert not result.passed
    assert result.seconds >= 10
    assert "not listed within 10s" in result.detail


def test_access_denied_check_creates_and_removes_a_probe_principal(fake, admin, make_client):
    _catalog(fake, admin)
    result = check_access_denied(admin, "astra_dev", make_client=make_client, probe_principal="probe")
    assert result.passed
    assert "403" in result.detail
    assert "probe" not in fake.principals


def test_run_verification_reports_all_three_checks_and_drops_the_probe(fake, admin, make_client):
    _catalog(fake, admin)
    probe = FakeProbe(fake)
    clock = Clock()
    reads: list[tuple[list[str], str]] = []

    def read_rows(namespace: list[str], table: str) -> int:
        reads.append((namespace, table))
        return 1

    results = run_verification(admin, "astra_dev", probe, make_client=make_client, read_rows=read_rows, clock=clock, sleep=clock.sleep)

    assert [r.passed for r in results] == [True, True, True]
    assert reads == [(["ASTRA_DEV", "SILVER"], "OC_SYNC_PROBE")]
    assert probe.created and probe.dropped


def test_run_verification_marks_read_failed_when_the_engine_errors(fake, admin, make_client):
    _catalog(fake, admin)
    probe = FakeProbe(fake)
    clock = Clock()

    def read_rows(namespace: list[str], table: str) -> int:
        raise RuntimeError("catalog endpoint unreachable")

    results = run_verification(admin, "astra_dev", probe, make_client=make_client, read_rows=read_rows, clock=clock, sleep=clock.sleep)
    assert results[1].passed is False
    assert "RuntimeError: catalog endpoint unreachable" in results[1].detail
    assert probe.dropped


def test_run_verification_skips_read_when_not_listed_and_still_drops(fake, admin, make_client):
    _catalog(fake, admin)
    probe = FakeProbe(fake, appear_after_polls=99)
    clock = Clock()

    results = run_verification(admin, "astra_dev", probe, make_client=make_client, read_rows=lambda ns, t: 1, timeout_seconds=5, clock=clock, sleep=clock.sleep)
    assert [r.passed for r in results] == [False, False, True]
    assert "skipped: table was not listed" in results[1].detail
    assert probe.dropped
