"""JSON Schema for structured model output, portable across providers.

Pydantic's schema uses ``$defs`` references, titles, defaults and length limits; providers
accept different subsets. This produces the common core every provider's strict mode takes:
objects with every property required and no extra properties, ``anyOf`` with ``null`` for
optional values, ``enum`` for choices, ``items`` for arrays. Limits are enforced by our own
validation afterwards, never trusted to the provider.

Properties marked ``x-column`` become an ``enum`` of this table's real column names, so the
model can only refer to columns that exist.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any, Final

from pydantic import BaseModel

#: Above this, an enum of names costs more prompt than it saves in rejected items.
MAX_COLUMN_ENUM: Final = 300
_DROP: Final = frozenset(
    {
        "title",
        "default",
        "examples",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
        "discriminator",
    }
)


def response_schema(model: type[BaseModel], columns: Sequence[str] | None = None) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    column_schema: dict[str, Any] = (
        {"type": "string", "enum": list(columns)}
        if columns is not None and 0 < len(columns) <= MAX_COLUMN_ENUM
        else {"type": "string"}
    )
    out = _convert(raw, defs, column_schema)
    assert isinstance(out, dict)  # noqa: S101  (an object schema converts to an object)
    return out


def _convert(node: Any, defs: dict[str, Any], column: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_convert(item, defs, column) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
        return _convert(merged, defs, column)
    if node.get("x-column"):
        description = node.get("description")
        if "anyOf" in node:  # optional column: keep the null branch
            replaced: dict[str, Any] = {"anyOf": [dict(column), {"type": "null"}]}
        else:
            replaced = dict(column)
        if description:
            replaced["description"] = description
        return replaced
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP or key.startswith("x-"):
            continue
        out[key] = _convert(value, defs, column)
    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out
