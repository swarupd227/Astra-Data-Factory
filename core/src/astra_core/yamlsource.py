"""YAML loading that remembers where things came from.

Validation messages must point at a file and line so that a failing pull
request shows the problem in place. PyYAML drops positions, so this loader
returns dict and list subclasses that carry the line of every key and item,
rejects duplicate keys instead of silently keeping the last one, and keeps
dates as the text that was written so the schema can check their shape.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import yaml


class SourceError(ValueError):
    """The file could not be parsed. Carries the line when known."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


class LineDict(dict):
    """A mapping that knows the line of each key and of itself."""

    line: int = 0
    key_lines: dict[str, int]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.key_lines = {}


class LineList(list):
    """A sequence that knows the line of each item and of itself."""

    line: int = 0
    item_lines: list[int]

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.item_lines = []


class _Loader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _Loader, node: yaml.MappingNode):
    mapping = LineDict()
    mapping.line = node.start_mark.line + 1
    yield mapping
    seen: dict[Any, int] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise SourceError(f"duplicate key '{key}' (first defined on line {seen[key]})", key_node.start_mark.line + 1)
        seen[key] = key_node.start_mark.line + 1
        mapping[key] = loader.construct_object(value_node, deep=True)
        mapping.key_lines[key] = key_node.start_mark.line + 1


def _construct_sequence(loader: _Loader, node: yaml.SequenceNode):
    sequence = LineList()
    sequence.line = node.start_mark.line + 1
    yield sequence
    for item_node in node.value:
        sequence.append(loader.construct_object(item_node, deep=True))
        sequence.item_lines.append(item_node.start_mark.line + 1)


def _construct_timestamp_as_text(loader: _Loader, node: yaml.ScalarNode) -> str:
    return str(node.value)


_Loader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)
_Loader.add_constructor("tag:yaml.org,2002:seq", _construct_sequence)
_Loader.add_constructor("tag:yaml.org,2002:timestamp", _construct_timestamp_as_text)


def load(text: str) -> Any:
    """Parse one YAML document. Raises SourceError with a line on failure."""
    try:
        documents = list(yaml.load_all(text, Loader=_Loader))
    except SourceError:
        raise
    except yaml.MarkedYAMLError as exc:
        line = exc.problem_mark.line + 1 if exc.problem_mark else None
        raise SourceError(f"{exc.problem or exc.context or 'invalid YAML'}".strip(), line) from exc
    except yaml.YAMLError as exc:
        raise SourceError(str(exc)) from exc
    if len(documents) != 1:
        raise SourceError(f"expected exactly one YAML document, found {len(documents)}", 1)
    return documents[0]


def line_of(data: Any, path: deque | list | tuple) -> int | None:
    """Best line for a path into loaded data.

    Descends as far as the path allows and returns the line of the deepest
    element found: the key's line inside a mapping, the item's line inside a
    sequence, or the container's own line when the path is empty.
    """
    current = data
    line = getattr(data, "line", None)
    for step in path:
        if isinstance(current, LineDict) and step in current:
            line = current.key_lines.get(step, line)
            current = current[step]
        elif isinstance(current, LineList) and isinstance(step, int) and 0 <= step < len(current):
            line = current.item_lines[step] if step < len(current.item_lines) else line
            current = current[step]
        else:
            break
    return line
