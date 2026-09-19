"""Verify the AgentCore Gateway at the MCP protocol level.

Lists the tools the Gateway exposes and calls one tool on each target:
  - order-tracker   (API Gateway REST proxy -> Lambda)   -> get_order
  - refund-processor (direct Lambda)                     -> initiate_refund

Run from the starter folder:  uv run python ../scripts/tests/mcp_verify.py
Output is saved to evidence/mcp-gateway-verification.txt
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "starter"))

from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from strands.tools.mcp.mcp_client import MCPClient  # noqa: E402
from strands.types.content import ContentBlock  # noqa: E402

import main as agent_module  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT = REPO / "evidence" / "mcp-gateway-verification.txt"
lines: list[str] = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def call(client: MCPClient, name: str, arguments: dict) -> str:
    """Call one MCP tool at the protocol level and return its result as text."""
    result = client.call_tool_sync(
        tool_use_id=f"verify-{name}", name=name, arguments=arguments,
    )
    try:
        return json.dumps(result, indent=2, default=str)
    except TypeError:
        return str(result)


say("### Gateway verification over MCP (raw protocol, not the agent)")
say(f"endpoint: {agent_module.GATEWAY_URL}")
say()

client = MCPClient(lambda: streamable_http_client(agent_module.GATEWAY_URL), startup_timeout=30)
try:
    client.start()
    tools = client.list_tools_sync()
    say(f"tools exposed by the Gateway: {len(tools)}")
    for t in tools:
        say(f"  - {t.tool_name}")
    say()

    say("--- tools/call: order-tracker___get_order (API Gateway target) ---")
    say(json.dumps(call(client, "order-tracker___get_order", {"order_id": "ORD-001"}), indent=2)[:1200])
    say()

    say("--- tools/call: refund-processor___initiate_refund (Lambda target) ---")
    say(json.dumps(call(client, "refund-processor___initiate_refund",
                        {"order_id": "ORD-002", "reason": "customer changed mind"}), indent=2)[:1200])
finally:
    client.stop(None, None, None)

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nsaved: {OUT}")
