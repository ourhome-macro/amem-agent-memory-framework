from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from agent_memory_runtime.agent.models import OutputContract


@dataclass(frozen=True)
class StructuredOutputResult:
    valid: bool
    value: Any = None
    reason: str | None = None
    path: str | None = None


def validate_structured_output(
    content: str,
    contract: OutputContract,
) -> StructuredOutputResult:
    try:
        Draft202012Validator.check_schema(contract.schema)
    except SchemaError:
        return StructuredOutputResult(False, reason="invalid_output_schema")
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return StructuredOutputResult(False, reason="invalid_json")
    try:
        Draft202012Validator(contract.schema).validate(value)
    except ValidationError as error:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        return StructuredOutputResult(
            False,
            reason="schema_validation_failed",
            path=path,
        )
    return StructuredOutputResult(True, value=value)


def output_contract_instruction(contract: OutputContract) -> str:
    schema = json.dumps(contract.schema, ensure_ascii=False, sort_keys=True)
    return (
        "Return the final answer only as JSON matching this output contract. "
        "Do not wrap it in Markdown fences. Tool calls are still allowed before the final answer. "
        f"contract={contract.name}; schema={schema}"
    )


def output_repair_instruction(
    contract: OutputContract,
    result: StructuredOutputResult,
) -> str:
    location = f" at {result.path}" if result.path else ""
    return (
        "The previous final answer was rejected by the trusted output validator: "
        f"{result.reason}{location}. Return corrected JSON only for contract "
        f"{contract.name}. Do not repeat or explain the invalid output."
    )
