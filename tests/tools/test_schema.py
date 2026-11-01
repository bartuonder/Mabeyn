import pytest
from pydantic import ValidationError

from mabeyn.tools.schema import (
    ToolSchemaError,
    build_args_model,
    openai_tool_spec,
    validate_schema,
)

REFUND = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string", "pattern": "^KS-[0-9]+$", "description": "Order number"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "reason": {"type": "string", "minLength": 3, "maxLength": 50},
        "speed": {"type": "string", "enum": ["standard", "fast"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "note": {"type": "string"},
    },
    "required": ["order_id", "amount", "reason"],
}


def model():
    return build_args_model("request_refund", REFUND)


def test_valid_arguments_pass_and_optional_fields_default_to_none():
    parsed = model().model_validate({"order_id": "KS-1001", "amount": 10, "reason": "broken"})
    assert parsed.amount == 10.0 and parsed.note is None and parsed.speed is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"amount": 10, "reason": "broken"},  # missing required
        {"order_id": "1001", "amount": 10, "reason": "broken"},  # pattern
        {"order_id": "KS-1", "amount": 0, "reason": "broken"},  # exclusive minimum
        {"order_id": "KS-1", "amount": 5, "reason": "ab"},  # too short
        {"order_id": "KS-1", "amount": 5, "reason": "broken", "speed": "instant"},  # enum
        {"order_id": "KS-1", "amount": 5, "reason": "broken", "tags": ["a", "b", "c"]},
        {"order_id": "KS-1", "amount": "lots", "reason": "broken"},  # wrong type
    ],
)
def test_invalid_arguments_are_rejected(arguments):
    with pytest.raises(ValidationError):
        model().model_validate(arguments)


def test_unknown_arguments_are_rejected():
    with pytest.raises(ValidationError):
        model().model_validate(
            {"order_id": "KS-1", "amount": 5, "reason": "broken", "customer_id": "someone-else"}
        )


def test_nested_objects_and_arrays_are_supported():
    schema = {
        "type": "object",
        "properties": {
            "address": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "zip": {"type": "string"}},
                "required": ["city"],
            },
            "items": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        },
        "required": ["address"],
    }
    built = build_args_model("nested", schema)
    parsed = built.model_validate({"address": {"city": "Izmir"}, "items": [1, 2]})
    assert parsed.address.city == "Izmir"
    with pytest.raises(ValidationError):
        built.model_validate({"address": {"city": "Izmir", "extra": 1}})
    with pytest.raises(ValidationError):
        built.model_validate({"address": {"city": "Izmir"}, "items": [0]})


@pytest.mark.parametrize("reserved", ["customer_id", "tenant_id"])
def test_schemas_may_not_expose_platform_context(reserved):
    schema = {"type": "object", "properties": {reserved: {"type": "string"}}}
    with pytest.raises(ToolSchemaError, match="supplied by the platform"):
        validate_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {"a": {"type": "null"}}},
        {"type": "object", "properties": {"a": {"$ref": "#/defs/x", "type": "string"}}},
        {"type": "object", "properties": {"a": {"anyOf": [], "type": "string"}}},
        {"type": "object", "properties": {"a": {"type": "string", "pattern": "("}}},
        {"type": "object", "properties": {"a": {"type": "array"}}},
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]},
    ],
)
def test_unsupported_schemas_are_rejected(schema):
    with pytest.raises(ToolSchemaError):
        validate_schema(schema)


def test_deeply_nested_schemas_are_rejected():
    schema: dict = {"type": "string"}
    for _ in range(6):
        schema = {"type": "object", "properties": {"x": schema}}
    with pytest.raises(ToolSchemaError, match="nested too deeply"):
        validate_schema(schema)


def test_models_are_cached_per_schema():
    assert build_args_model("request_refund", REFUND) is build_args_model("request_refund", REFUND)


def test_openai_spec_forbids_additional_properties():
    spec = openai_tool_spec("request_refund", "Refund an order", REFUND)
    assert spec["type"] == "function"
    assert spec["function"]["parameters"]["additionalProperties"] is False
    assert spec["function"]["name"] == "request_refund"
