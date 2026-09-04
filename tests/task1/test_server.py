from __future__ import annotations

import asyncio

import pytest
from mcp import types
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from fde_assessment.task1_mcp_server.server import (
    TOOLS,
    CustomerRecordArguments,
    RefundArguments,
    call_tool,
)


def test_exact_tools_and_generated_schemas() -> None:
    assert [tool.name for tool in TOOLS] == ["get_customer_record", "trigger_refund"]
    assert TOOLS[0].input_schema["required"] == ["customer_id"]
    assert set(TOOLS[1].input_schema["required"]) == {"customer_id", "amount", "reason"}
    customer = TOOLS[0].input_schema
    refund = TOOLS[1].input_schema
    assert set(customer["properties"]) == {"customer_id"}
    assert customer["additionalProperties"] is False
    assert customer["properties"]["customer_id"]["pattern"] == r"^CUST-[0-9]{5}$"
    assert set(refund["properties"]) == {"customer_id", "amount", "reason"}
    assert refund["additionalProperties"] is False
    assert refund["properties"]["customer_id"]["pattern"] == r"^CUST-[0-9]{5}$"
    assert [
        item["exclusiveMinimum"] for item in refund["properties"]["amount"]["anyOf"]
    ] == [0, 0]
    assert "gt" not in refund["properties"]["amount"]
    assert refund["properties"]["reason"]["minLength"] == 10
    assert "surrounding whitespace" in refund["properties"]["reason"]["description"]


@pytest.mark.parametrize("value", ["CUST-12345", "CUST-00000"])
def test_customer_id_accepts_only_expected_ascii_shape(value: str) -> None:
    assert CustomerRecordArguments(customer_id=value).customer_id == value


@pytest.mark.parametrize(
    "value", ["cust-12345", "CUST-1234", "CUST-１２３４５", 12345, True, None]
)
def test_customer_id_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValidationError):
        CustomerRecordArguments.model_validate({"customer_id": value})


@pytest.mark.parametrize("arguments", [{}, {"customer_id": "CUST-12345", "extra": 1}])
def test_customer_record_requires_exact_fields(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CustomerRecordArguments.model_validate(arguments)


@pytest.mark.parametrize("amount", [1, 1.25, 1000000000000000000000])
def test_amount_accepts_positive_finite_numbers(amount: float) -> None:
    assert RefundArguments(
        customer_id="CUST-12345", amount=amount, reason="customer requested refund"
    )


@pytest.mark.parametrize(
    "amount",
    [True, "1", None, [], {}, 0, -1, float("nan"), float("inf"), -float("inf")],
)
def test_amount_is_strict_and_positive(amount: object) -> None:
    with pytest.raises(ValidationError):
        RefundArguments.model_validate(
            {
                "customer_id": "CUST-12345",
                "amount": amount,
                "reason": "customer requested refund",
            }
        )


@pytest.mark.parametrize("reason", ["short", "         ", "123456789", 123, None])
def test_reason_requires_strict_trimmed_minimum(reason: object) -> None:
    with pytest.raises(ValidationError):
        RefundArguments.model_validate(
            {"customer_id": "CUST-12345", "amount": 1, "reason": reason}
        )


@pytest.mark.parametrize("reason", ["1234567890", "  1234567890  ", "12345678901"])
def test_reason_accepts_ten_trimmed_characters(reason: str) -> None:
    assert (
        RefundArguments(customer_id="CUST-12345", amount=1, reason=reason).reason
        == reason
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {
            "customer_id": "CUST-12345",
            "amount": 1,
            "reason": "valid reason",
            "extra": 1,
        },
    ],
)
def test_refund_requires_exact_fields(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RefundArguments.model_validate(arguments)


def test_handlers_distinguish_success_business_failure_and_protocol_error() -> None:
    async def exercise() -> None:
        success = await call_tool(
            None,
            types.CallToolRequestParams(
                name="get_customer_record", arguments={"customer_id": "CUST-12345"}
            ),
        )
        assert (
            success.is_error is False
            and success.structured_content["customer_id"] == "CUST-12345"
        )
        failure = await call_tool(
            None,
            types.CallToolRequestParams(
                name="trigger_refund",
                arguments={
                    "customer_id": "CUST-99999",
                    "amount": 1,
                    "reason": "customer requested refund",
                },
            ),
        )
        assert failure.is_error is True
        with pytest.raises(MCPError, match="Invalid tool arguments"):
            await call_tool(
                None,
                types.CallToolRequestParams(
                    name="get_customer_record", arguments={"customer_id": "bad"}
                ),
            )
        with pytest.raises(MCPError, match="Unknown tool"):
            await call_tool(
                None, types.CallToolRequestParams(name="other", arguments={})
            )

    asyncio.run(exercise())
