"""Rule Recovery (the Astra RE Harness): legacy Splitter/Loader Java turned into rule catalog
entries with file:line citations, classified, with candidate tests (S5.4.1, ADR 0044, product
spec Section 6).

The model reads the Java source and proposes catalog entries in the same `rule-v0` shape
`rules/<group>/<name>.yaml` already uses (`astra_knowledge.rules.Rule`, `render_rule`) — no
second, agent-specific format. Every entry needs a `file`+`line` citation to exist at all (the
tool's own schema requires one, the same guardrail Spec Reader's `extract_source_spec` uses); a
passage the model cannot confidently classify goes in `unrecovered`, never guessed into an entry.

Two guardrails are enforced in code, not merely asked for in the prompt:

- **No entry is ever marked confirmed by the agent.** The tool schema does not expose a `status`
  field to the model at all; this module always writes `status: "recovered"` and a matching
  history entry itself. A steward confirms or rejects through `astra-spec rules set-status`
  (ADR 0016) — the same command the workbench will call — never this agent.
- **Every rejection code the source actually defines is traced, or the run reports a gap.**
  `find_rejection_codes` scans the raw Java text itself for the illustrative fixture's own
  `"L###"` convention, independently of what the model reported, and `RecoveryDraft.
  rejection_codes_untraced` names any code no recovered entry claims — a structural check, not
  a claim the model gets to make unverified.
- **T-SQL is routed, not parsed.** `TSQL_MARKERS` is a second, deterministic backstop scanning
  for SQL Server idioms (`GETDATE()`, `TOP n`, `[bracketed]` identifiers, `sp_` procedures) the
  model's own `embedded_sql` citations must cover; an uncovered hit is reported as a routing gap
  rather than silently left for the model to have quietly turned into a rule's `text`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from astra_core.problems import Problem
from astra_core.schema import describe_error, load_validator, sorted_errors
from astra_knowledge.rules import Citation, HistoryEntry, Owner, Rule, render_rule

RULE_SCHEMA = "rule-v0.schema.json"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 32000

EXTRACTION_TOOL_NAME = "extract_rule_catalog"

# Calibrated against this story's own illustrative Splitter/Loader fixture (agents/examples/
# rule_recovery/java), whose rejection codes are string literals like "L001". A real Loader's
# own convention is not yet known (docs/backlog-v0.2.md's own Risks section says so); this
# pattern is the first thing to recalibrate once real source is available.
REJECTION_CODE_PATTERN = re.compile(r'"(L[0-9]{3})"')

# Idioms specific enough to SQL Server / T-SQL that seeing one in a Java string literal is a
# reliable signal, not a guess: GETDATE(), TOP n, bracketed identifiers, sp_ procedures, IDENTITY.
TSQL_MARKERS = (
    re.compile(r"\bGETDATE\s*\(", re.IGNORECASE),
    re.compile(r"\bTOP\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bNVARCHAR\b", re.IGNORECASE),
    re.compile(r"\bIDENTITY\s*\(", re.IGNORECASE),
    re.compile(r"\bsp_\w+", re.IGNORECASE),
    re.compile(r"\[\s*\w+\s*\]\.\[\s*\w+\s*\]"),
)


class RuleRecoveryError(RuntimeError):
    pass


# -- reading the source ---------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    path: str  # as it should be cited: relative, stable across machines
    text: str


def read_sources(paths: list[Path]) -> tuple[SourceFile, ...]:
    return tuple(SourceFile(path=Path(p).name, text=Path(p).read_text(encoding="utf-8")) for p in paths)


def _numbered_text(source: SourceFile) -> str:
    lines = source.text.splitlines()
    width = len(str(len(lines)))
    numbered = "\n".join(f"{i:>{width}}: {line}" for i, line in enumerate(lines, start=1))
    return f"--- {source.path} ---\n{numbered}"


def system_prompt(group: str) -> str:
    return (
        f"You are recovering business rules from legacy Java source for the '{group}' Splitter/Loader into a "
        "rule catalog. Call extract_rule_catalog exactly once with everything you find.\n\n"
        "For every distinct validation, rejection or business rule the code implements, propose one entry: "
        "a short snake_case name, the rule in plain words a business owner would recognize (at least 20 "
        "characters, not a restatement of the code), its class (ingestion: how a file is read and accepted; "
        "normalisation: how a value becomes canonical; business: what the data means or how it is treated), "
        "and a citation naming the exact file and the line (and end_line, when the rule spans more than one "
        "line) where the behavior is implemented. Never propose an entry you cannot cite; a passage you "
        "cannot confidently classify belongs in unrecovered, with a reason, not guessed into an entry.\n\n"
        "If the code raises or returns a rejection code (a constant the Loader uses to say why a record was "
        "refused), copy it exactly as written into that entry's rejection_codes — never invent one and never "
        "paraphrase it. Add one or two candidate_tests per entry: a short description of what the test would "
        "prove, a concrete given (an example input), and the expect it should produce — a starting point for "
        "a person to write a real test, not a runnable one.\n\n"
        "If you find SQL embedded in the source — a string literal or a call built from one — do not "
        "interpret it, translate it into a rule's text, or guess what it means: record it in embedded_sql "
        "with its citation, your best guess at its dialect (for example tsql, ansi), the snippet, and a short "
        "reason. T-SQL and SSIS are routed to SnowConvert AI, not parsed by this agent.\n\n"
        "You have no way to set a rule's status; every entry you propose is recorded as newly recovered, "
        "never confirmed — that decision belongs to the rule's owner, not to you."
    )


EXTRACTION_TOOL: dict[str, Any] = {
    "name": EXTRACTION_TOOL_NAME,
    "description": "Record every rule catalog entry, embedded-SQL routing note and unrecovered passage found in the source.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["entries", "embedded_sql", "unrecovered"],
        "properties": {
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "text", "class", "citation"],
                    "properties": {
                        "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                        "text": {"type": "string", "minLength": 20},
                        "class": {"enum": ["ingestion", "business", "normalisation"]},
                        "citation": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["file", "line"],
                            "properties": {"file": {"type": "string"}, "line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}},
                        },
                        "rejection_codes": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"}},
                        "candidate_tests": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["description", "given", "expect"],
                                "properties": {"description": {"type": "string"}, "given": {"type": "string"}, "expect": {"type": "string"}},
                            },
                        },
                    },
                },
            },
            "embedded_sql": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["citation", "dialect", "reason"],
                    "properties": {
                        "citation": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["file", "line"],
                            "properties": {"file": {"type": "string"}, "line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}},
                        },
                        "dialect": {"type": "string"},
                        "snippet": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "unrecovered": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["citation", "reason"],
                    "properties": {
                        "citation": {"type": "object", "additionalProperties": False, "required": ["file"], "properties": {"file": {"type": "string"}, "line": {"type": "integer", "minimum": 1}}},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
}


@dataclass(frozen=True)
class Extraction:
    entries: list[dict]
    embedded_sql: list[dict]
    unrecovered: list[dict]


class LlmClient(Protocol):
    def extract(self, *, system: str, sources: tuple[SourceFile, ...]) -> Extraction: ...


def _extraction_from_tool_input(data: dict, *, stop_reason: str | None = None) -> Extraction:
    missing = [k for k in ("entries", "embedded_sql", "unrecovered") if k not in data]
    if missing:
        if stop_reason == "max_tokens":
            raise RuleRecoveryError(
                f"the model's response was cut off at the token limit before it finished calling {EXTRACTION_TOOL_NAME} "
                f"(missing {', '.join(missing)}); pass a higher --max-tokens, or split the source into smaller files"
            )
        raise RuleRecoveryError(f"the model's {EXTRACTION_TOOL_NAME} call is missing {', '.join(missing)}; stop reason {stop_reason or 'unknown'}")
    return Extraction(entries=data["entries"], embedded_sql=data["embedded_sql"], unrecovered=data["unrecovered"])


class AnthropicClient:
    """`LlmClient` against the real Anthropic API. See astra_agents.spec_reader.AnthropicClient
    for the reasoning behind streaming and the token budget; the same client, duplicated rather
    than shared, because that reasoning came from real, hard-won debugging against a live account
    and is not worth risking a shared-module refactor could quietly undo."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, max_tokens: int = MAX_TOKENS) -> None:
        self.model = model
        self._api_key = api_key
        self.max_tokens = max_tokens

    def extract(self, *, system: str, sources: tuple[SourceFile, ...]) -> Extraction:
        try:
            import anthropic
        except ImportError as exc:
            raise RuleRecoveryError("calling the model needs the anthropic package; pip install 'astra-agents[llm]'") from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        content = "\n\n".join(_numbered_text(s) for s in sources)
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        ) as stream:
            message = stream.get_final_message()
        stop_reason = getattr(message, "stop_reason", None)
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == EXTRACTION_TOOL_NAME:
                return _extraction_from_tool_input(block.input, stop_reason=stop_reason)
        raise RuleRecoveryError(f"the model did not call {EXTRACTION_TOOL_NAME}; stop reason {stop_reason or 'unknown'}")


# -- deterministic backstops ---------------------------------------------------


def find_rejection_codes(sources: tuple[SourceFile, ...]) -> tuple[str, ...]:
    codes: set[str] = set()
    for s in sources:
        codes.update(REJECTION_CODE_PATTERN.findall(s.text))
    return tuple(sorted(codes))


def find_tsql_lines(sources: tuple[SourceFile, ...]) -> tuple[dict, ...]:
    hits = []
    for s in sources:
        for line_number, line in enumerate(s.text.splitlines(), start=1):
            if any(marker.search(line) for marker in TSQL_MARKERS):
                hits.append({"file": s.path, "line": line_number, "text": line.strip()})
    return tuple(hits)


def _covers(citation: dict, file: str, line: int) -> bool:
    if citation.get("file") != file:
        return False
    start = citation.get("line", line)
    end = citation.get("end_line", start)
    return start <= line <= end


# -- assembling and validating the draft ---------------------------------------


def _dedupe_name(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name}_{n}"
        n += 1
    return candidate


def _translate_entry(raw: dict, *, group: str, owner: Owner, taken: set[str], at: datetime) -> "DraftEntry":
    name = _dedupe_name(raw["name"], taken)
    taken.add(name)
    citation_data = raw["citation"]
    citation = Citation("code", file=citation_data["file"], line=citation_data["line"], end_line=citation_data.get("end_line"))
    rejection_codes = tuple(raw.get("rejection_codes") or ())
    tags = tuple(raw.get("tags") or ()) + tuple(f"rejection-{c.lower()}" for c in rejection_codes)
    rule = Rule(
        id=f"{group}.{name}",
        text=" ".join(raw["text"].split()),
        class_=raw["class"],
        owner=owner,
        status="recovered",
        citation=citation,
        history=(HistoryEntry("recovered", "rule-recovery", at, note=f"Recovered from {citation.text}."),),
        path=Path("rules") / group / f"{name}.yaml",
        tags=tags,
    )
    return DraftEntry(rule=rule, rejection_codes=rejection_codes, candidate_tests=tuple(raw.get("candidate_tests") or ()), problems=tuple(validate_rule(_rule_to_dict(rule))))


def _rule_to_dict(rule: Rule) -> dict:
    r: dict[str, Any] = {
        "id": rule.id,
        "text": rule.text,
        "class": rule.class_,
        "owner": {"name": rule.owner.name, "email": rule.owner.email},
        "status": rule.status,
        "citation": rule.citation.to_mapping(),
    }
    if rule.custodians or rule.entities:
        applies_to: dict[str, Any] = {}
        if rule.custodians:
            applies_to["custodians"] = list(rule.custodians)
        if rule.entities:
            applies_to["entities"] = list(rule.entities)
        r["applies_to"] = applies_to
    if rule.tags:
        r["tags"] = list(rule.tags)
    history = []
    for h in rule.history:
        entry = {"status": h.status, "by": h.by, "at": h.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if h.note:
            entry["note"] = h.note
        history.append(entry)
    return {"rule_version": 0, "rule": r, "history": history}


def validate_rule(data: dict) -> list[Problem]:
    validator = load_validator("astra_knowledge.schemas", RULE_SCHEMA)
    return [Problem(data["rule"]["id"], None, describe_error(e)) for e in sorted_errors(validator, data)]


@dataclass(frozen=True)
class DraftEntry:
    rule: Rule
    rejection_codes: tuple[str, ...]
    candidate_tests: tuple[dict, ...]
    problems: tuple[Problem, ...]

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "id": self.rule.id,
            "text": self.rule.text,
            "class": self.rule.class_,
            "status": self.rule.status,
            "citation": self.rule.citation.to_mapping(),
            "tags": list(self.rule.tags),
            "rejection_codes": list(self.rejection_codes),
            "candidate_tests": list(self.candidate_tests),
            "valid": self.valid,
            "problems": [p.format() for p in self.problems],
        }


@dataclass(frozen=True)
class RecoveryDraft:
    group: str
    entries: tuple[DraftEntry, ...]
    embedded_sql: tuple[dict, ...]
    unrecovered: tuple[dict, ...]
    rejection_codes_found: tuple[str, ...]
    tsql_lines_found: tuple[dict, ...]

    @property
    def rejection_codes_traced(self) -> tuple[str, ...]:
        return tuple(sorted({c for e in self.entries for c in e.rejection_codes}))

    @property
    def rejection_codes_untraced(self) -> tuple[str, ...]:
        traced = set(self.rejection_codes_traced)
        return tuple(c for c in self.rejection_codes_found if c not in traced)

    @property
    def tsql_lines_unrouted(self) -> tuple[dict, ...]:
        return tuple(hit for hit in self.tsql_lines_found if not any(_covers(sql["citation"], hit["file"], hit["line"]) for sql in self.embedded_sql))

    @property
    def valid(self) -> bool:
        return all(e.valid for e in self.entries)

    @property
    def ok(self) -> bool:
        return self.valid and not self.rejection_codes_untraced and not self.tsql_lines_unrouted

    def to_dict(self) -> dict:
        return {
            "group": self.group,
            "valid": self.valid,
            "ok": self.ok,
            "entries": [e.to_dict() for e in self.entries],
            "embedded_sql": list(self.embedded_sql),
            "unrecovered": list(self.unrecovered),
            "rejection_codes_found": list(self.rejection_codes_found),
            "rejection_codes_traced": list(self.rejection_codes_traced),
            "rejection_codes_untraced": list(self.rejection_codes_untraced),
            "tsql_lines_unrouted": list(self.tsql_lines_unrouted),
        }


def build_draft(extraction: Extraction, sources: tuple[SourceFile, ...], *, group: str, owner: Owner, clock=lambda: datetime.now(timezone.utc)) -> RecoveryDraft:
    at = clock()
    taken: set[str] = set()
    entries = tuple(_translate_entry(raw, group=group, owner=owner, taken=taken, at=at) for raw in extraction.entries)
    return RecoveryDraft(
        group=group,
        entries=entries,
        embedded_sql=tuple(extraction.embedded_sql),
        unrecovered=tuple(extraction.unrecovered),
        rejection_codes_found=find_rejection_codes(sources),
        tsql_lines_found=find_tsql_lines(sources),
    )


def run(paths: list[Path], client: LlmClient, *, group: str, owner_name: str, owner_email: str) -> RecoveryDraft:
    sources = read_sources(paths)
    system = system_prompt(group)
    extraction = client.extract(system=system, sources=sources)
    return build_draft(extraction, sources, group=group, owner=Owner(owner_name, owner_email))


# -- the report and the draft files --------------------------------------------


def render_markdown(draft: RecoveryDraft) -> str:
    out = [f"# Rule Recovery draft: {draft.group}", ""]
    out.append(f"{len(draft.entries)} entry(ies) recovered. Valid against the rule catalog schema: {'yes' if draft.valid else 'no'}. Ready to review: {'yes' if draft.ok else 'no'}.")
    out.append("")
    out.append("## Entries")
    out.append("")
    out.append("| Rule | Class | Citation | Rejection codes | Candidate tests |")
    out.append("|---|---|---|---|---|")
    for e in draft.entries:
        mark = "" if e.valid else " **(invalid)**"
        out.append(f"| {e.rule.id}{mark} | {e.rule.class_} | {e.rule.citation.text} | {', '.join(e.rejection_codes) or '-'} | {len(e.candidate_tests)} |")
    out.append("")
    if any(not e.valid for e in draft.entries):
        out.append("### Schema problems")
        out.append("")
        for e in draft.entries:
            for p in e.problems:
                out.append(f"- {p.format()}")
        out.append("")
    out.append("## Loader rejection codes")
    out.append("")
    out.append(f"{len(draft.rejection_codes_traced)}/{len(draft.rejection_codes_found)} traced to at least one entry.")
    if draft.rejection_codes_untraced:
        out.append("")
        out.append("Not traced to any entry: " + ", ".join(draft.rejection_codes_untraced))
    out.append("")
    out.append("## Embedded SQL — routed to SnowConvert AI, not parsed")
    out.append("")
    if draft.embedded_sql:
        out.append("| Citation | Dialect | Reason |")
        out.append("|---|---|---|")
        for s in draft.embedded_sql:
            out.append(f"| {s['citation']['file']}:{s['citation']['line']} | {s.get('dialect', '-')} | {s.get('reason', '-')} |")
    else:
        out.append("None found.")
    if draft.tsql_lines_unrouted:
        out.append("")
        out.append("**Not covered by any embedded_sql citation above** — a T-SQL idiom was found on these lines but the model did not route them:")
        out.append("")
        for hit in draft.tsql_lines_unrouted:
            out.append(f"- {hit['file']}:{hit['line']}: `{hit['text']}`")
    out.append("")
    out.append("## Unrecovered")
    out.append("")
    if draft.unrecovered:
        out.append("| Citation | Reason |")
        out.append("|---|---|")
        for u in draft.unrecovered:
            citation = u.get("citation") or {}
            where = citation.get("file", "-") + (f":{citation['line']}" if citation.get("line") else "")
            out.append(f"| {where} | {u.get('reason', '-')} |")
    else:
        out.append("Nothing was left unrecovered.")
    out.append("")
    return "\n".join(out)


def write_draft(draft: RecoveryDraft, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    for entry in draft.entries:
        rule_path = out / "rules" / draft.group / f"{entry.rule.id.split('.', 1)[1]}.yaml"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text(render_rule(entry.rule), encoding="utf-8", newline="\n")
    if draft.entries:
        tests_path = out / "candidate_tests.yaml"
        lines = ["# Candidate tests, one starting point per rule entry -- not a runnable test.", "tests:"]
        for e in draft.entries:
            for t in e.candidate_tests:
                lines.append(f"  - rule: {e.rule.id}")
                lines.append(f"    description: {t['description']!r}")
                lines.append(f"    given: {t['given']!r}")
                lines.append(f"    expect: {t['expect']!r}")
        tests_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    report_path = out / "report.md"
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    return out, report_path
