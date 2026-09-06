"""Pattern: replicate a reference-data feed and resolve records against it.

A feed (a security master, an account cross-reference) arrives as full
snapshots on a schedule. Replication brings a local replica in line with
the newest snapshot and keeps the delta visible:

  inserted   a key in the snapshot that the replica did not have
  updated    a key in both whose values differ
  deleted    a key in the replica that the snapshot no longer carries
  unchanged  a key in both with equal values

Every run is recorded with its row counts; every change is recorded with
the values before and after, so the delta since any run can be read back.
A key that appears more than once in a snapshot is a conflict: those rows
are left out, counted and reported with the feed's conflict code, and the
replica keeps what it had for that key.

Resolution is set-based: a record names a row by the feed's key, or by one
of its alternate identifiers tried in order (CUSIP, then ISIN, ...). One
row is a match; none is the feed's not-found code; several is its
ambiguous code. The rendered SQL (S3.2.4) joins the replica the same way;
this module is what it is checked against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable
from uuid import uuid4

from astra_knowledge.reference_data import Feed

INSERTED, UPDATED, DELETED = "inserted", "updated", "deleted"


@dataclass(frozen=True)
class Change:
    run_id: str
    change: str  # inserted, updated, deleted
    key: tuple
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True)
class Conflict:
    run_id: str
    key: tuple
    rows: int
    code: str


@dataclass
class ReplicationRun:
    feed_id: str
    run_id: str
    snapshot_date: date
    started_at: datetime
    status: str = "succeeded"  # succeeded, failed, skipped
    rows_source: int = 0
    rows_conflict: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_deleted: int = 0
    rows_unchanged: int = 0
    rows_total: int = 0
    error: str | None = None

    @property
    def rows_changed(self) -> int:
        return self.rows_inserted + self.rows_updated + self.rows_deleted


@dataclass(frozen=True)
class ReplicaRow:
    key: tuple
    values: dict[str, Any]
    run_id: str
    replicated_at: datetime


@dataclass(frozen=True)
class Resolution:
    status: str  # found, not_found, ambiguous
    rows: tuple[ReplicaRow, ...] = ()
    by: str | None = None  # the column that matched
    code: str | None = None  # rejection code when not found or ambiguous

    @property
    def row(self) -> ReplicaRow | None:
        return self.rows[0] if self.status == "found" else None


class ReplicationError(ValueError):
    """The snapshot cannot be applied: it does not carry the feed's columns."""


@dataclass
class Replica:
    """A feed's replica with its run history and change log."""

    feed: Feed
    rows: dict[tuple, ReplicaRow] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    runs: list[ReplicationRun] = field(default_factory=list)

    def _key(self, values: dict[str, Any]) -> tuple:
        return tuple(values.get(name) for name in self.feed.key)

    def _compare(self, values: dict[str, Any]) -> tuple:
        return tuple(values.get(c.name) for c in self.feed.columns)

    def replicate(self, snapshot: Iterable[dict[str, Any]], snapshot_date: date, run_at: datetime, run_id: str | None = None) -> ReplicationRun:
        """Apply a full snapshot. Returns the run with its counts; the run, the changes and any conflicts are kept."""
        run = ReplicationRun(self.feed.id, run_id or uuid4().hex, snapshot_date, run_at)
        rows = list(snapshot)
        run.rows_source = len(rows)
        expected = {c.name for c in self.feed.columns}
        for row in rows:
            missing = expected - set(row)
            if missing:
                run.status = "failed"
                run.error = f"snapshot row lacks column{'s' if len(missing) > 1 else ''} {', '.join(sorted(missing))}"
                self.runs.append(run)
                raise ReplicationError(run.error)

        # Rows whose key repeats are conflicts and stay out; the replica keeps what it had.
        by_key: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            by_key.setdefault(self._key(row), []).append(row)
        clean: dict[tuple, dict[str, Any]] = {}
        for key, group in by_key.items():
            if any(v is None for v in key) or len(group) > 1:
                run.rows_conflict += len(group)
                self.conflicts.append(Conflict(run.run_id, key, len(group), self.feed.rejections.conflict))
            else:
                clean[key] = {c.name: group[0].get(c.name) for c in self.feed.columns}

        conflicted = {c.key for c in self.conflicts if c.run_id == run.run_id}
        for key, existing in list(self.rows.items()):
            if key not in clean and key not in conflicted:
                self.changes.append(Change(run.run_id, DELETED, key, dict(existing.values), None))
                del self.rows[key]
                run.rows_deleted += 1
        for key, values in clean.items():
            existing = self.rows.get(key)
            if existing is None:
                self.changes.append(Change(run.run_id, INSERTED, key, None, dict(values)))
                self.rows[key] = ReplicaRow(key, values, run.run_id, run_at)
                run.rows_inserted += 1
            elif self._compare(existing.values) != self._compare(values):
                self.changes.append(Change(run.run_id, UPDATED, key, dict(existing.values), dict(values)))
                self.rows[key] = ReplicaRow(key, values, run.run_id, run_at)
                run.rows_updated += 1
            else:
                run.rows_unchanged += 1
        run.rows_total = len(self.rows)
        self.runs.append(run)
        return run

    def changes_since(self, run_id: str | None = None) -> list[Change]:
        """The delta after the given run (or everything when None): what the last runs changed, in order."""
        if run_id is None:
            return list(self.changes)
        seen = False
        result: list[Change] = []
        for run in self.runs:
            if seen:
                result.extend(c for c in self.changes if c.run_id == run.run_id)
            if run.run_id == run_id:
                seen = True
        return result

    def last_delta(self) -> list[Change]:
        """The changes of the most recent run."""
        if not self.runs:
            return []
        return [c for c in self.changes if c.run_id == self.runs[-1].run_id]

    def resolve(self, **identifiers: Any) -> Resolution:
        """Find the one row a record names, by key columns or by the feed's alternate identifiers in order."""
        given = {name: value for name, value in identifiers.items() if value is not None and str(value).strip() != ""}
        if all(name in given for name in self.feed.key):
            key = tuple(given[name] for name in self.feed.key)
            row = self.rows.get(key)
            return Resolution("found", (row,), ", ".join(self.feed.key)) if row else Resolution("not_found", code=self.feed.rejections.not_found)
        for name in self.feed.resolves.identifiers:
            value = given.get(name)
            if value is None:
                continue
            matches = tuple(r for r in self.rows.values() if r.values.get(name) == value)
            if len(matches) == 1:
                return Resolution("found", matches, name)
            if len(matches) > 1:
                return Resolution("ambiguous", matches, name, self.feed.rejections.ambiguous)
        return Resolution("not_found", code=self.feed.rejections.not_found)
