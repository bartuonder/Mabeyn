import json
import re
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

# Supplied by the executor from the verified session; the model must never set them.
RESERVED_ARGUMENTS = frozenset({"customer_id", "tenant_id"})
MAX_PROPERTIES = 20
MAX_DEPTH = 3
SUPPORTED_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
UNSUPPORTED_KEYWORDS = {"$ref", "oneOf", "anyOf", "allOf", "not", "if", "patternProperties"}


class ToolSchemaError(ValueError):
    pass


def _check_type(name: str, spec: dict[str, Any], depth: int) -> None:
    if UNSUPPORTED_KEYWORDS & spec.keys():
        raise ToolSchemaError(f"{name}: unsupported schema keyword")
    kind = spec.get("type")
    if kind not in SUPPORTED_TYPES:
        raise ToolSchemaError(f"{name}: unsupported or missing type {kind!r}")
    if kind == "array":
        items = spec.get("items")
        if not isinstance(items, dict):
            raise ToolSchemaError(f"{name}: arrays need an items schema")
        _check_type(f"{name}[]", items, depth + 1)
    if kind == "object":
        _check_object(name, spec, depth + 1)
    if "pattern" in spec:
        try:
            re.compile(spec["pattern"])
        except re.error as exc:
            raise ToolSchemaError(f"{name}: invalid pattern") from exc


def _check_object(name: str, schema: dict[str, Any], depth: int) -> None:
    if depth > MAX_DEPTH:
        raise ToolSchemaError(f"{name}: objects are nested too deeply")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ToolSchemaError(f"{name}: object needs at least one property")
    if len(properties) > MAX_PROPERTIES:
        raise ToolSchemaError(f"{name}: too many properties")
    reserved = RESERVED_ARGUMENTS & properties.keys()
    if reserved:
        raise ToolSchemaError(f"{name}: {sorted(reserved)} are supplied by the platform")
    for prop, spec in properties.items():
        if not isinstance(spec, dict):
            raise ToolSchemaError(f"{name}.{prop}: property schema must be an object")
        _check_type(f"{name}.{prop}", spec, depth)
    for required in schema.get("required", []):
        if required not in properties:
            raise ToolSchemaError(f"{name}: required property {required!r} is not defined")


def validate_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object":
        raise ToolSchemaError("the top-level schema must have type object")
    _check_object("arguments", schema, 0)


def _field_type(name: str, spec: dict[str, Any]) -> Any:
    kind = spec["type"]
    if "enum" in spec:
        return Literal[tuple(spec["enum"])]
    if kind == "string":
        return str
    if kind == "integer":
        return int
    if kind == "number":
        return float
    if kind == "boolean":
        return bool
    if kind == "array":
        item_spec = spec["items"]
        inner = _field_type(f"{name}_item", item_spec)
        item: Any = Annotated[inner, Field(**_constraints(item_spec))]
        return list[item]
    return _model(name, spec)


def _constraints(spec: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "minLength": "min_length",
        "maxLength": "max_length",
        "pattern": "pattern",
        "minimum": "ge",
        "maximum": "le",
        "exclusiveMinimum": "gt",
        "exclusiveMaximum": "lt",
        "minItems": "min_length",
        "maxItems": "max_length",
    }
    return {target: spec[key] for key, target in mapping.items() if key in spec}


def _model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for prop, spec in schema["properties"].items():
        annotation = _field_type(f"{name}_{prop}", spec)
        options = {"description": spec.get("description"), **_constraints(spec)}
        if prop in required:
            fields[prop] = (annotation, Field(..., **options))
        else:
            fields[prop] = (annotation | None, Field(spec.get("default"), **options))
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


@lru_cache(maxsize=256)
def _build(name: str, canonical_schema: str) -> type[BaseModel]:
    schema = json.loads(canonical_schema)
    validate_schema(schema)
    return _model(name, schema)


def build_args_model(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Create (and cache) the Pydantic model that validates LLM-produced arguments."""
    return _build(name, json.dumps(schema, sort_keys=True))


def openai_tool_spec(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {**schema, "additionalProperties": False},
        },
    }
