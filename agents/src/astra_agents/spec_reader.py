"""Spec Reader: a layout document turned into a Source Spec with page citations (S5.1.1, ADR 0041, product spec Section 6).

Given a custodian's layout document (PDF today; Word via the same
interface), the agent extracts every record type, every field's
position, picture and type, and the page it is defined on, into the
same `source-spec-v0` shape `specs/<id>/<version>.yaml` already uses
(`knowledge/src/astra_knowledge/schemas/source-spec-v0.schema.json`) —
the registry never sees a second, agent-specific format. A passage the
model cannot confidently support with a citation is named in
`unparsed`, not guessed into a field: the guardrail the backlog asks
for is enforced by the tool's own schema (a field without a citation is
not a valid call) and restated in the prompt.

The model call sits behind `LlmClient`, a two-method interface `extract`
implements against the real Anthropic API and a fake implements for
every test in this module — the same shape every Snowflake-touching
tool in `astra_verification` already uses against a fake executor. The
agent never writes into the registry itself: `run` writes a *draft* spec
next to a report (fields extracted, citations, what was left unparsed)
for a person to review before it is ever promoted into `specs/`, exactly
the product's own principle: agents propose, humans approve.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

from astra_core.problems import Problem
from astra_core.schema import describe_error, load_validator, sorted_errors

SPEC_SCHEMA = "source-spec-v0.schema.json"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000

EXTRACTION_TOOL_NAME = "extract_source_spec"


class SpecReaderError(RuntimeError):
    pass


# -- reading the document into page-labelled text -------------------------------


@dataclass(frozen=True)
class Page:
    number: int | None  # 1-based; None when the format has no real page concept
    text: str


def extract_pdf_pages(path: Path) -> list[Page]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise SpecReaderError("reading a PDF needs pdfplumber; pip install 'astra-agents[pdf]'") from exc
    with pdfplumber.open(path) as pdf:
        return [Page(i + 1, page.extract_text() or "") for i, page in enumerate(pdf.pages)]


def extract_docx_pages(path: Path) -> list[Page]:
    try:
        import docx
    except ImportError as exc:
        raise SpecReaderError("reading a Word document needs python-docx; pip install 'astra-agents[docx]'") from exc
    document = docx.Document(str(path))
    text = "\n".join(p.text for p in document.paragraphs)
    return [Page(None, text)]  # Word has no reliable page boundary outside a renderer; citations are page-less


def extract_pages(path: Path) -> list[Page]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_pages(path)
    if suffix == ".docx":
        return extract_docx_pages(path)
    raise SpecReaderError(f"{path}: unsupported document type '{suffix}'; expected .pdf or .docx")


# -- the extraction tool: what the model is asked to call, and how ------------------


EXTRACTION_TOOL: dict[str, Any] = {
    "name": EXTRACTION_TOOL_NAME,
    "description": (
        "Record every record type, field and citation this layout document defines, in the shape a fixed-width or "
        "delimited file spec needs. A passage you cannot confidently support with a page citation belongs in "
        "`unparsed`, named with whatever citation you do have and why — never invent a field, a position or a "
        "citation you are not sure of."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["file", "records", "unparsed"],
        "properties": {
            "file": {
                "type": "object",
                "additionalProperties": False,
                "required": ["format"],
                "properties": {
                    "format": {"enum": ["fixed_width", "delimited"]},
                    "record_length": {"type": "integer", "minimum": 1, "description": "Required for fixed_width."},
                    "delimiter": {"type": "string", "minLength": 1, "maxLength": 1, "description": "Required for delimited."},
                },
            },
            "records": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "fields"],
                    "properties": {
                        "type": {"enum": ["header", "detail", "trailer"]},
                        "name": {"type": "string", "description": "Required when the document names more than one detail record type, for example 'a' and 'b'."},
                        "description": {"type": "string"},
                        "match": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["value"],
                            "properties": {
                                "start": {"type": "integer", "minimum": 1, "description": "Fixed-width: 1-based start of the record-type token."},
                                "length": {"type": "integer", "minimum": 1, "description": "Fixed-width: length of the record-type token."},
                                "value": {"type": "string", "minLength": 1, "description": "The literal value that identifies a line as this record type."},
                            },
                            "description": "How a line is recognised as this record type.",
                        },
                        "fields": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["name", "citation"],
                                "properties": {
                                    "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,62}$", "description": "snake_case; 'filler' for a 'Not Used' span."},
                                    "start": {"type": "integer", "minimum": 1},
                                    "length": {"type": "integer", "minimum": 1},
                                    "picture": {"type": "string", "description": "The COBOL-style picture exactly as printed, for example X(10) or 9(13)V9(05)."},
                                    "type": {"enum": ["string", "integer", "decimal", "date", "time", "code", "boolean"]},
                                    "format": {"type": "string", "description": "For a date or time field, for example YYYYMMDD."},
                                    "sign_field": {"type": "string", "description": "Name of the field in the same record carrying this number's sign, when the document shows one as a separate position."},
                                    "required": {"type": "boolean"},
                                    "description": {"type": "string"},
                                    "citation": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["page"],
                                        "properties": {"page": {"type": "integer", "minimum": 1}, "line": {"type": "integer", "minimum": 1}},
                                    },
                                    "codes": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["value", "meaning"],
                                            "properties": {
                                                "value": {"type": "string"},
                                                "meaning": {"type": "string"},
                                                "sign": {"enum": ["positive", "negative", "unknown"], "description": "On the codes of a sign field: what this code means for the number that references it."},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "unparsed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["reason"],
                    "properties": {
                        "citation": {"type": "object", "properties": {"page": {"type": "integer"}, "line": {"type": "integer"}}},
                        "reason": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}


def system_prompt(file_type: str, custodians: list[str]) -> str:
    return (
        "You are the Spec Reader agent of Astra Data Factory. You read a custodian's file layout document, page by "
        "page, and extract a machine-readable Source Spec by calling the extract_source_spec tool exactly once.\n\n"
        "Rules:\n"
        "- Every field needs a citation naming the page it is defined on. A field, a position or a value you cannot "
        "support with a citation does not belong in `records` — list the passage in `unparsed` with what you do "
        "know and why you stopped, instead.\n"
        "- Field names are snake_case, derived from the document's own field label. A 'Not Used' or filler span is "
        "named `filler`.\n"
        "- A quantity or amount field printed next to its own sign position (a separate '+ / - / blank' field) is "
        "one field with `sign_field` naming the sign position, not two independent fields — give the sign "
        "position's own `codes` a `sign` of positive, negative or unknown to match.\n"
        "- A record-type token (for example the first few characters of every line) becomes that record's `match`, "
        "not a field of it, unless the document also gives it a citation as a field in its own right.\n"
        "- Copy every picture exactly as printed (X(10), 9(13)V9(05), and so on); do not normalise or guess a "
        "picture the document does not state.\n\n"
        f"The file type is {file_type}; it is delivered by: {', '.join(custodians)}."
    )


# -- the LLM client interface, and the real implementation -----------------------


@dataclass(frozen=True)
class Extraction:
    file: dict
    records: list[dict]
    unparsed: list[dict]


class LlmClient(Protocol):
    def extract(self, *, system: str, pages: list[Page]) -> Extraction: ...


def _document_text(pages: list[Page]) -> str:
    return "\n\n".join(f"--- page {p.number if p.number is not None else '?'} ---\n{p.text}" for p in pages)


def _extraction_from_tool_input(data: dict) -> Extraction:
    return Extraction(file=data["file"], records=data["records"], unparsed=data.get("unparsed", []))


class AnthropicClient:
    """The real client. Needs ANTHROPIC_API_KEY in the environment (or api_key given) and `pip install 'astra-agents[llm]'`."""

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, max_tokens: int = MAX_TOKENS) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key

    def extract(self, *, system: str, pages: list[Page]) -> Extraction:
        try:
            import anthropic
        except ImportError as exc:
            raise SpecReaderError("calling the model needs the anthropic package; pip install 'astra-agents[llm]'") from exc
        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": _document_text(pages)}],
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        )
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == EXTRACTION_TOOL_NAME:
                return _extraction_from_tool_input(block.input)
        raise SpecReaderError(f"the model did not call {EXTRACTION_TOOL_NAME}; stop reason {getattr(message, 'stop_reason', 'unknown')}")


# -- assembling and validating the draft spec ------------------------------------


def _translate_match(raw: dict | None) -> dict | None:
    if not raw:
        return None
    match = {"value": raw["value"]}
    if "start" in raw and "length" in raw:
        match["position"] = {"start": raw["start"], "length": raw["length"]}
    return match


def _translate_field(raw: dict) -> dict:
    out = {"name": raw["name"]}
    if "start" in raw and "length" in raw:
        out["position"] = {"start": raw["start"], "length": raw["length"]}
    for key in ("picture", "type", "format", "sign_field", "required", "description"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    if raw.get("citation"):
        out["citation"] = raw["citation"]
    if raw.get("codes"):
        out["codes"] = raw["codes"]
    return out


def _translate_record(raw: dict) -> dict:
    out: dict = {"type": raw["type"], "fields": [_translate_field(f) for f in raw["fields"]]}
    if raw.get("name"):
        out["name"] = raw["name"]
    if raw.get("description"):
        out["description"] = raw["description"]
    match = _translate_match(raw.get("match"))
    if match:
        out["match"] = match
    return out


@dataclass(frozen=True)
class DraftSpec:
    data: dict
    unparsed: tuple[dict, ...]
    problems: tuple[Problem, ...]

    @property
    def field_count(self) -> int:
        return sum(len(r["fields"]) for r in self.data.get("records", []))

    @property
    def valid(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            "spec": self.data,
            "unparsed": list(self.unparsed),
            "problems": [p.format() for p in self.problems],
            "field_count": self.field_count,
            "valid": self.valid,
        }


def build_draft(
    extraction: Extraction,
    *,
    spec_id: str,
    version: str,
    effective_from: str,
    file_type: str,
    custodians: list[str],
    document_title: str,
    document_reference: str,
    document_sha256: str | None = None,
    document_pages: int | None = None,
    family: str | None = None,
    provider: str | None = None,
    description: str | None = None,
) -> DraftSpec:
    spec: dict[str, Any] = {"id": spec_id, "version": version, "effective_from": effective_from, "file_type": file_type, "custodians": custodians}
    if family:
        spec["family"] = family
    if provider:
        spec["provider"] = provider
    if description:
        spec["description"] = description

    document: dict[str, Any] = {"title": document_title, "reference": document_reference}
    if document_sha256:
        document["sha256"] = document_sha256
    if document_pages:
        document["pages"] = document_pages

    data = {
        "spec_version": 0,
        "spec": spec,
        "document": document,
        "file": extraction.file,
        "records": [_translate_record(r) for r in extraction.records],
    }
    problems = validate_draft(data)
    return DraftSpec(data=data, unparsed=tuple(extraction.unparsed), problems=tuple(problems))


def validate_draft(data: dict) -> list[Problem]:
    validator = load_validator("astra_knowledge.schemas", SPEC_SCHEMA)
    return [Problem("draft", None, describe_error(e)) for e in sorted_errors(validator, data)]


# -- the run -----------------------------------------------------------------


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    document: Path,
    client: LlmClient,
    *,
    spec_id: str,
    version: str,
    effective_from: str,
    file_type: str,
    custodians: list[str],
    document_title: str | None = None,
    family: str | None = None,
    provider: str | None = None,
    description: str | None = None,
    clock=lambda: datetime.now(timezone.utc),
) -> DraftSpec:
    document = Path(document)
    pages = extract_pages(document)
    system = system_prompt(file_type, custodians)
    extraction = client.extract(system=system, pages=pages)
    return build_draft(
        extraction,
        spec_id=spec_id,
        version=version,
        effective_from=effective_from,
        file_type=file_type,
        custodians=custodians,
        document_title=document_title or document.stem,
        document_reference=document.name,
        document_sha256=sha256_of(document),
        document_pages=len(pages) if pages and pages[0].number is not None else None,
        family=family,
        provider=provider,
        description=description,
    )


# -- the report --------------------------------------------------------------


def render_markdown(draft: DraftSpec) -> str:
    spec = draft.data["spec"]
    out = [f"# Spec Reader draft: {spec['id']} {spec['version']}", ""]
    out.append(f"{len(draft.data.get('records', []))} record(s), {draft.field_count} field(s) extracted. Valid against the registry schema: {'yes' if draft.valid else 'no'}.")
    out.append("")
    if draft.problems:
        out.append("## Schema problems")
        out.append("")
        for p in draft.problems:
            out.append(f"- {p.format()}")
        out.append("")
    out.append("## Records")
    out.append("")
    out.append("| Record | Fields | Citations |")
    out.append("|---|---|---|")
    for r in draft.data.get("records", []):
        cited = sum(1 for f in r["fields"] if f.get("citation"))
        out.append(f"| {r.get('name', r['type'])} ({r['type']}) | {len(r['fields'])} | {cited}/{len(r['fields'])} |")
    out.append("")
    out.append("## Unparsed")
    out.append("")
    if draft.unparsed:
        out.append("| Citation | Reason |")
        out.append("|---|---|")
        for u in draft.unparsed:
            citation = u.get("citation") or {}
            where = f"p{citation['page']}" + (f"l{citation['line']}" if citation.get("line") else "") if citation.get("page") else "-"
            out.append(f"| {where} | {u['reason']} |")
    else:
        out.append("Nothing was left unparsed.")
    out.append("")
    return "\n".join(out)


def write_draft(draft: DraftSpec, out: Path) -> tuple[Path, Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    spec_path = out / "spec.yaml"
    report_path = out / "report.md"
    data_path = out / "report.json"
    spec_path.write_text(yaml.safe_dump(draft.data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    report_path.write_text(render_markdown(draft), encoding="utf-8", newline="\n")
    data_path.write_text(json.dumps(draft.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return spec_path, report_path, data_path
