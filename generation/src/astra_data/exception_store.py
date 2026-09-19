"""The exception workflow, rendered as a release bundle per domain pack (S7.1.4, ADR 0078).

The per-source exception tables (`EXCEPTIONS.<SOURCE>`, S3.2.5) already hold every rejection with
its workflow columns -- STATUS, RESOLUTION, RESOLVED_BY, RESOLVED_AT -- but nothing enforced how an
exception may move, and nothing recorded who moved it or who owns it. This bundle adds exactly
that, and touches none of those tables' writers:

  ddl/exception_workflow.sql        CONTROL.EXCEPTION_HISTORY, an append-only history of every move
                                    and every assignment, and CONTROL.EXCEPTION_ASSIGNMENTS, the
                                    latest assignment of each exception
  pipeline/exception_workflow.sql   CONTROL.TRANSITION_EXCEPTION and CONTROL.ASSIGN_EXCEPTION: the
                                    only sanctioned way to move or assign an exception, refusing
                                    every invalid move with a named rule before anything is written
  tests/*.sql                       invariants over the history that hold on real data: every move
                                    is an allowed edge, every event has an actor and its own
                                    shape, nothing is assigned after the exception was closed

The states are the domain pack's own `Exception.STATUS` codes and the allowed moves, the rules and
their order all come from `astra_knowledge.patterns.exception_workflow` -- the Python reference
implementation this SQL is checked against -- so the two cannot drift; a pack whose model states
differ from the workflow's is refused rather than rendered. The owner of an exception is the
taxonomy's owner for its code, read at move time from CONTROL.REJECTION_CODES; the assignee is the
latest assignment in the history. Procedures address an exception by its source and id, because
the per-source tables are one per source config and a pack bundle cannot list them; they reach the
right table by name, after checking the name is a plain source id.

The bundle is committed under releases/<pack>-exceptions and checked in CI, like the Gold and
Silver bundles.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from astra_knowledge.cdm import DomainPack, load_packs
from astra_knowledge.patterns.exception_workflow import INITIAL, KINDS, RULES, STATES, TRANSITIONS

from astra_core.problems import Problem

BUNDLE_SUFFIX = "-exceptions"
DB = "{{ DATABASE }}"
CONTROL = f'{DB}."CONTROL"'
HISTORY = f'{CONTROL}."EXCEPTION_HISTORY"'
ASSIGNMENTS = f'{CONTROL}."EXCEPTION_ASSIGNMENTS"'
REJECTION_CODES = f'{CONTROL}."REJECTION_CODES"'

# Rules that only the storage has: a source name that is not a plain source id, and a row that
# changed between the read and the write. Numbered after the shared rules.
STORAGE_RULES = ("source_invalid", "changed_concurrently")

RULE_MESSAGES = {
    "exception_not_found": "no such exception in that source",
    "not_a_state": "the status is not one of the exception states",
    "actor_required": "who is making the change must be given",
    "illegal_transition": "the exception cannot move from its status to that one",
    "resolution_required": "moving out of NEW needs what was done, or why it was dismissed",
    "not_whitelisted": "the rejection code is not whitelisted for auto-resolve",
    "assignee_required": "who the exception is assigned to must be given",
    "not_new": "only a NEW exception can be assigned",
    "already_assigned": "the exception is already assigned to that person",
    "source_invalid": "the source id is not a valid source name",
    "changed_concurrently": "the exception changed while it was being updated; read it again",
}


def rule_number(rule: str) -> int:
    """-20101.. for the shared rules in their order, -20201.. for the storage's own."""
    if rule in RULES:
        return -(20100 + RULES.index(rule) + 1)
    return -(20200 + STORAGE_RULES.index(rule) + 1)


def _lit(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def _q(identifier: str) -> str:
    return f'"{identifier}"'


def _declare(rules: tuple[str, ...]) -> str:
    return "\n".join(f"  {r} EXCEPTION ({rule_number(r)}, {_lit(RULE_MESSAGES[r])});" for r in rules)


def _edges() -> list[tuple[str, str]]:
    return [(a, b) for a in STATES for b in STATES if b in TRANSITIONS[a]]


def _edge_values() -> str:
    return ", ".join(f"({_lit(a)}, {_lit(b)})" for a, b in _edges())


def _state_values() -> str:
    return ", ".join(f"({_lit(s)})" for s in STATES)


# -- the pack -------------------------------------------------------------------


def problems_for(pack: DomainPack, repo_root: Path | None = None) -> list[Problem]:
    """The workflow is defined over the model's own STATUS codes; a pack that says otherwise cannot use it."""
    entity = pack.latest.entity("Exception")
    status = entity.column("STATUS") if entity else None
    if status is None:
        return [Problem(_display(pack.latest.path, repo_root), None, f"the {pack.name} model has no Exception entity with a STATUS column")]
    codes = tuple(c.value for c in status.codes)
    if codes != STATES:
        return [Problem(_display(pack.latest.path, repo_root), None, f"Exception.STATUS codes are {', '.join(codes) or 'none'}, but the exception workflow moves exceptions between {', '.join(STATES)}")]
    return []


def packs_with_exceptions(domains_dir: Path | str, root: Path | None = None, domain: str | None = None) -> tuple[list[DomainPack], list[Problem]]:
    packs, problems = load_packs(Path(domains_dir), root)
    if problems:
        return [], problems
    selected = [p for p in packs if p.latest.entity("Exception") is not None and (domain is None or p.name == domain)]
    problems = [problem for p in selected for problem in problems_for(p, root)]
    return ([] if problems else selected), problems


# -- DDL ------------------------------------------------------------------------


def render_tables(pack: DomainPack) -> str:
    return "\n".join(
        [
            f"-- Exception workflow of the {pack.name} domain pack: every move of an exception between states and every",
            "-- assignment of it to a person, append only. Written by CONTROL.TRANSITION_EXCEPTION and CONTROL.ASSIGN_EXCEPTION;",
            "-- the status itself stays on the exception's own row in EXCEPTIONS.<SOURCE>. Rendered by astra-data exceptions render.",
            "",
            f"CREATE ICEBERG TABLE IF NOT EXISTS {HISTORY} (",
            "  \"EVENT_ID\"       STRING NOT NULL COMMENT 'Identifier of this event',",
            "  \"SOURCE_ID\"      STRING NOT NULL COMMENT 'The source config whose EXCEPTIONS table holds the exception',",
            "  \"EXCEPTION_ID\"   STRING NOT NULL COMMENT 'The exception, in that source',",
            "  \"REJECTION_CODE\" STRING NOT NULL COMMENT 'Its code in the rejection taxonomy',",
            "  \"OWNER\"          STRING COMMENT 'The taxonomy owner of the code when the event happened: custodian, steward, data_engineer or platform',",
            f"  \"KIND\"           STRING NOT NULL COMMENT {_lit('One of ' + ', '.join(KINDS) + ': a move between states, or an assignment to a person')},",
            "  \"FROM_STATUS\"    STRING COMMENT 'Status before a move; null for an assignment',",
            "  \"TO_STATUS\"      STRING COMMENT 'Status after a move; null for an assignment',",
            "  \"ASSIGNEE\"       STRING COMMENT 'The person an assignment gave the exception to; null for a move',",
            "  \"ACTOR\"          STRING NOT NULL COMMENT 'Who made the move or the assignment',",
            "  \"EVENT_AT\"       TIMESTAMP_NTZ(6) NOT NULL COMMENT 'When it happened',",
            "  \"NOTE\"           STRING COMMENT 'For a move: what was done, or why the exception was dismissed'",
            ")",
            "BASE_LOCATION = 'control/exception_history/'",
            "COMMENT = 'Every move and assignment of an exception, append only. Rendered by astra-data exceptions render.';",
            "",
            "-- Who each exception is assigned to now: its latest assignment. A closed exception keeps its last assignee;",
            "-- join the exception's own STATUS to leave those out.",
            f"CREATE OR REPLACE VIEW {ASSIGNMENTS}",
            "COMMENT = 'The latest assignment of each exception. Rendered by astra-data exceptions render.'",
            "AS",
            'SELECT "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "ASSIGNEE", "ACTOR" AS "ASSIGNED_BY", "EVENT_AT" AS "ASSIGNED_AT"',
            f"FROM {HISTORY}",
            "WHERE \"KIND\" = 'assign'",
            'QUALIFY ROW_NUMBER() OVER (PARTITION BY "SOURCE_ID", "EXCEPTION_ID" ORDER BY "EVENT_AT" DESC) = 1;',
            "",
        ]
    )


# -- the procedures ---------------------------------------------------------------


_LOCATE = f"""  IF (NOT REGEXP_LIKE(SOURCE_ID, '^[A-Za-z][A-Za-z0-9_]*$')) THEN
    RAISE source_invalid;
  END IF;
  v_table := '{DB}."EXCEPTIONS"."' || UPPER(SOURCE_ID) || '"';
  SELECT "REJECTION_CODE", "STATUS" INTO :v_code, :v_status FROM IDENTIFIER(:v_table) WHERE "EXCEPTION_ID" = :EXCEPTION_ID;
  IF (v_code IS NULL) THEN
    RAISE exception_not_found;
  END IF;
  SELECT MAX("OWNER") INTO :v_owner FROM {REJECTION_CODES} WHERE "CODE" = :v_code;"""

_HISTORY_COLUMNS = '("EVENT_ID", "SOURCE_ID", "EXCEPTION_ID", "REJECTION_CODE", "OWNER", "KIND", "FROM_STATUS", "TO_STATUS", "ASSIGNEE", "ACTOR", "EVENT_AT", "NOTE")'


def render_procedures(pack: DomainPack) -> str:
    transition_rules = ("source_invalid", "exception_not_found", "not_a_state", "actor_required", "illegal_transition", "resolution_required", "not_whitelisted", "changed_concurrently")
    assign_rules = ("source_invalid", "exception_not_found", "actor_required", "assignee_required", "not_new", "already_assigned", "changed_concurrently")
    return f"""-- Move an exception out of {INITIAL}, refusing every invalid move with a named rule before anything is written. The rules run
-- in this order: the source name, the exception exists, the status is a state, an actor is named, the move is an allowed edge,
-- what was done (or why) is given, and AUTO_RESOLVED only for a code the taxonomy whitelists. The status change on the exception's
-- own row and its history event are written together; the update only applies while the row still has the status that was read.
-- Rendered by astra-data exceptions render.
CREATE OR REPLACE PROCEDURE {CONTROL}."TRANSITION_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "TO_STATUS" STRING, "ACTOR" STRING, "RESOLUTION" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Moves an exception between states ({", ".join(f"{a} to {b}" for a, b in _edges())}); refuses any other move. Rendered by astra-data exceptions render.'
AS
$$
DECLARE
{_declare(transition_rules)}
  v_table STRING;
  v_code STRING;
  v_status STRING;
  v_owner STRING;
  v_ok INTEGER DEFAULT 0;
BEGIN
{_LOCATE}
  SELECT COUNT(*) INTO :v_ok FROM (SELECT * FROM VALUES {_state_values()} AS s ("STATE")) WHERE "STATE" = :TO_STATUS;
  IF (v_ok = 0) THEN
    RAISE not_a_state;
  END IF;
  IF (ACTOR IS NULL OR TRIM(ACTOR) = '') THEN
    RAISE actor_required;
  END IF;
  SELECT COUNT(*) INTO :v_ok FROM (SELECT * FROM VALUES {_edge_values()} AS t ("FROM_STATUS", "TO_STATUS")) WHERE "FROM_STATUS" = :v_status AND "TO_STATUS" = :TO_STATUS;
  IF (v_ok = 0) THEN
    RAISE illegal_transition;
  END IF;
  IF (RESOLUTION IS NULL OR TRIM(RESOLUTION) = '') THEN
    RAISE resolution_required;
  END IF;
  IF (TO_STATUS = 'AUTO_RESOLVED') THEN
    SELECT COUNT(*) INTO :v_ok FROM {REJECTION_CODES} WHERE "CODE" = :v_code AND "AUTO_RESOLVE";
    IF (v_ok = 0) THEN
      RAISE not_whitelisted;
    END IF;
  END IF;

  BEGIN TRANSACTION;
  UPDATE IDENTIFIER(:v_table) SET "STATUS" = :TO_STATUS, "RESOLUTION" = TRIM(:RESOLUTION), "RESOLVED_BY" = TRIM(:ACTOR), "RESOLVED_AT" = SYSDATE(), "UPDATED_AT" = SYSDATE()
  WHERE "EXCEPTION_ID" = :EXCEPTION_ID AND "STATUS" = :v_status;
  IF (SQLROWCOUNT = 0) THEN
    ROLLBACK;
    RAISE changed_concurrently;
  END IF;
  INSERT INTO {HISTORY} {_HISTORY_COLUMNS}
  SELECT UUID_STRING(), :SOURCE_ID, :EXCEPTION_ID, :v_code, :v_owner, 'transition', :v_status, :TO_STATUS, NULL, TRIM(:ACTOR), SYSDATE(), TRIM(:RESOLUTION);
  COMMIT;
  RETURN 'exception ' || EXCEPTION_ID || ' moved from ' || v_status || ' to ' || TO_STATUS || ' by ' || TRIM(ACTOR);
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;

-- Give a {INITIAL} exception to a person, or to a different one. Refused, with a named rule, if the exception does not exist, no actor or
-- assignee is named, the exception is already closed, or it is already assigned to that person. Rendered by astra-data exceptions render.
CREATE OR REPLACE PROCEDURE {CONTROL}."ASSIGN_EXCEPTION"("SOURCE_ID" STRING, "EXCEPTION_ID" STRING, "ASSIGNEE" STRING, "ACTOR" STRING)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Assigns a {INITIAL} exception to a person; refuses a closed one. Rendered by astra-data exceptions render.'
AS
$$
DECLARE
{_declare(assign_rules)}
  v_table STRING;
  v_code STRING;
  v_status STRING;
  v_owner STRING;
  v_current STRING;
  v_ok INTEGER DEFAULT 0;
BEGIN
{_LOCATE}
  IF (ACTOR IS NULL OR TRIM(ACTOR) = '') THEN
    RAISE actor_required;
  END IF;
  IF (ASSIGNEE IS NULL OR TRIM(ASSIGNEE) = '') THEN
    RAISE assignee_required;
  END IF;
  IF (v_status <> {_lit(INITIAL)}) THEN
    RAISE not_new;
  END IF;
  v_current := (SELECT "ASSIGNEE" FROM {HISTORY} WHERE "SOURCE_ID" = :SOURCE_ID AND "EXCEPTION_ID" = :EXCEPTION_ID AND "KIND" = 'assign' ORDER BY "EVENT_AT" DESC LIMIT 1);
  IF (v_current = TRIM(ASSIGNEE)) THEN
    RAISE already_assigned;
  END IF;

  BEGIN TRANSACTION;
  INSERT INTO {HISTORY} {_HISTORY_COLUMNS}
  SELECT UUID_STRING(), :SOURCE_ID, :EXCEPTION_ID, :v_code, :v_owner, 'assign', NULL, NULL, TRIM(:ASSIGNEE), TRIM(:ACTOR), SYSDATE(), NULL;
  SELECT COUNT(*) INTO :v_ok FROM IDENTIFIER(:v_table) WHERE "EXCEPTION_ID" = :EXCEPTION_ID AND "STATUS" = {_lit(INITIAL)};
  IF (v_ok = 0) THEN
    ROLLBACK;
    RAISE changed_concurrently;
  END IF;
  COMMIT;
  RETURN 'exception ' || EXCEPTION_ID || ' assigned to ' || TRIM(ASSIGNEE) || ' by ' || TRIM(ACTOR);
EXCEPTION
  WHEN OTHER THEN
    ROLLBACK;
    RAISE;
END;
$$;
"""


# -- tests ------------------------------------------------------------------------


def render_tests(pack: DomainPack) -> dict[str, str]:
    return {
        "exception_history_transitions_are_legal.sql": "\n".join(
            [
                "-- Every move in the history is an edge the exception workflow allows. Returns moves that are not.",
                'SELECT h."SOURCE_ID", h."EXCEPTION_ID", h."FROM_STATUS", h."TO_STATUS"',
                f"FROM {HISTORY} h",
                f'LEFT JOIN (SELECT * FROM VALUES {_edge_values()} AS t ("FROM_STATUS", "TO_STATUS")) l ON l."FROM_STATUS" = h."FROM_STATUS" AND l."TO_STATUS" = h."TO_STATUS"',
                "WHERE h.\"KIND\" = 'transition' AND l.\"FROM_STATUS\" IS NULL;",
                "",
            ]
        ),
        "exception_history_events_are_well_formed.sql": "\n".join(
            [
                "-- Every event has an actor and the shape of its kind: a move has both statuses and what was done, an assignment has its assignee.",
                "-- Returns events that do not.",
                'SELECT "SOURCE_ID", "EXCEPTION_ID", "KIND"',
                f"FROM {HISTORY}",
                "WHERE TRIM(COALESCE(\"ACTOR\", '')) = ''",
                f"   OR \"KIND\" NOT IN ({', '.join(_lit(k) for k in KINDS)})",
                "   OR (\"KIND\" = 'transition' AND (\"FROM_STATUS\" IS NULL OR \"TO_STATUS\" IS NULL OR TRIM(COALESCE(\"NOTE\", '')) = ''))",
                "   OR (\"KIND\" = 'assign' AND TRIM(COALESCE(\"ASSIGNEE\", '')) = '');",
                "",
            ]
        ),
        "exception_history_no_assignment_after_a_move.sql": "\n".join(
            [
                "-- Nothing is assigned after the exception left NEW. Returns assignments made later than the exception's move.",
                'SELECT a."SOURCE_ID", a."EXCEPTION_ID", a."EVENT_AT" AS "ASSIGNED_AT", t."EVENT_AT" AS "MOVED_AT"',
                f"FROM {HISTORY} a",
                f'JOIN {HISTORY} t ON t."SOURCE_ID" = a."SOURCE_ID" AND t."EXCEPTION_ID" = a."EXCEPTION_ID" AND t."KIND" = \'transition\'',
                "WHERE a.\"KIND\" = 'assign' AND a.\"EVENT_AT\" > t.\"EVENT_AT\";",
                "",
            ]
        ),
    }


# -- the bundle ---------------------------------------------------------------------


def bundle_name(pack: DomainPack) -> str:
    return f"{pack.name.replace('_', '-')}{BUNDLE_SUFFIX}"


def render_bundle(pack: DomainPack) -> dict[str, str]:
    """Every file of the pack's exceptions bundle, keyed by path relative to the bundle directory."""
    problems = problems_for(pack)
    if problems:
        raise ValueError("; ".join(p.message for p in problems))
    files: dict[str, str] = {
        "ddl/exception_workflow.sql": render_tables(pack),
        "pipeline/exception_workflow.sql": render_procedures(pack),
    }
    files.update({f"tests/{name}": text for name, text in render_tests(pack).items()})
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    manifest = [
        f"# Exception workflow of the {pack.name} domain pack: history, assignments and the procedures that enforce the allowed",
        f"# moves. Rendered by astra-data exceptions render from the model's Exception entity (domains/{pack.name}/cdm/{pack.latest.version}.yaml) and",
        "# astra_knowledge.patterns.exception_workflow; the version is a digest of the rendered files. Do not edit.",
        f"bundle: {bundle_name(pack)}",
        f'version: "{digest.hexdigest()[:12]}"',
        f"source: {pack.name}_exceptions",
        "steps:",
        "  - ddl/exception_workflow.sql",
        "  - pipeline/exception_workflow.sql",
        "tests:",
        "  - tests/*.sql",
        "",
    ]
    files["manifest.yaml"] = "\n".join(manifest)
    return files


def write_bundle(pack: DomainPack, releases_dir: Path) -> Path:
    """Write the bundle, removing files it no longer produces. Returns the bundle directory."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                existing.unlink()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


def check_bundle(pack: DomainPack, releases_dir: Path, repo_root: Path | None = None) -> list[Problem]:
    """Problems for every bundle file that is missing, stale or no longer produced."""
    root = Path(releases_dir) / bundle_name(pack)
    files = render_bundle(pack)
    problems: list[Problem] = []
    for name, text in files.items():
        path = root / name
        if not path.is_file():
            problems.append(Problem(_display(path, repo_root), None, "not rendered; run astra-data exceptions render"))
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            problems.append(Problem(_display(path, repo_root), None, "stale: the exception workflow or the model changed since it was rendered; run astra-data exceptions render"))
    if root.is_dir():
        for existing in sorted(p for p in root.rglob("*") if p.is_file()):
            if existing.relative_to(root).as_posix() not in files:
                problems.append(Problem(_display(existing, repo_root), None, "no longer produced; run astra-data exceptions render to remove it"))
    return problems


def _display(path: Path, root: Path | None) -> str:
    try:
        return path.resolve().relative_to((root or Path.cwd()).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
