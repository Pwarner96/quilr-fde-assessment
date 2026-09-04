"""Low-level MCP server implementation for Task 1."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from fde_assessment.common.logging import configure_safe_logger

logger = configure_safe_logger("fde_assessment.task1")

CUSTOMER_ID = Annotated[str, StringConstraints(strict=True, pattern=r"^CUST-[0-9]{5}$")]


class CustomerRecordArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: CUSTOMER_ID


class RefundArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: CUSTOMER_ID
    amount: Annotated[StrictInt | StrictFloat, Field(gt=0, allow_inf_nan=False)]
    reason: Annotated[
        str,
        StringConstraints(strict=True),
        Field(
            min_length=10,
            description="At least 10 characters; runtime trims surrounding whitespace before counting.",
        ),
    ]

    @field_validator("reason")
    @classmethod
    def require_meaningful_reason(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError(
                "reason must contain at least 10 non-whitespace-trimmed characters"
            )
        return value


CUSTOMER_ADAPTER = TypeAdapter(CustomerRecordArguments)
REFUND_ADAPTER = TypeAdapter(RefundArguments)


def _invalid(message: str) -> MCPError:
    return MCPError(code=-32602, message=message)


def _tool(name: str, description: str, model: type[BaseModel]) -> types.Tool:
    schema = model.model_json_schema()
    schema.pop("title", None)
    if name == "trigger_refund":
        # Pydantic 2.13 emits its internal `gt` keyword for this union; publish
        # the equivalent standard JSON Schema constraint.
        amount_schema = schema["properties"]["amount"]
        amount_schema["anyOf"] = [
            {**variant, "exclusiveMinimum": 0} for variant in amount_schema["anyOf"]
        ]
        amount_schema.pop("gt", None)
    return types.Tool(name=name, description=description, input_schema=schema)


TOOLS = [
    _tool(
        "get_customer_record",
        "Return a deterministic synthetic customer record.",
        CustomerRecordArguments,
    ),
    _tool(
        "trigger_refund",
        "Evaluate a deterministic synthetic refund request.",
        RefundArguments,
    ),
]
TOOL_NAMES = frozenset(tool.name for tool in TOOLS)


async def list_tools(_: Any, __: Any) -> types.ListToolsResult:
    return types.ListToolsResult(tools=TOOLS)


async def call_tool(
    _: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name not in TOOL_NAMES:
        raise _invalid(f"Unknown tool: {params.name}")

    arguments = params.arguments
    try:
        if params.name == "get_customer_record":
            customer = CUSTOMER_ADAPTER.validate_python(arguments)
            result: dict[str, Any] = {
                "customer_id": customer.customer_id,
                "name": "Synthetic Customer",
                "status": "active",
            }
        else:
            refund = REFUND_ADAPTER.validate_python(arguments)
            if refund.customer_id == "CUST-99999" or refund.amount > 10000:
                return types.CallToolResult(
                    is_error=True,
                    content=[
                        types.TextContent(
                            text="Synthetic business rule declined the refund."
                        )
                    ],
                )
            result = {
                "customer_id": refund.customer_id,
                "amount": refund.amount,
                "reason": refund.reason,
                "refund_id": "rfnd_synthetic_00001",
                "status": "approved",
            }
    except ValidationError:
        # Pydantic validation is intentionally converted at this protocol boundary.
        raise _invalid("Invalid tool arguments") from None

    logger.info("completed synthetic tool %s", params.name)
    return types.CallToolResult(
        content=[types.TextContent(text=_json(result))], structured_content=result
    )


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True)


app = Server(
    "quilr-task1", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool
)


async def _run() -> None:
    logger.info("starting MCP stdio server")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
