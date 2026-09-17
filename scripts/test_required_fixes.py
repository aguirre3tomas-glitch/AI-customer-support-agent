"""Regression tests for the three required fixes.

Runs entirely locally — no AWS credentials, no deployed agent:

  1. Loyalty redemption boundaries (500-point blocks, 50% cap)
  2. The result contract: points_redeemed / tier_discount_pct / remaining_points
     present and numeric in the sandbox path AND the fallback path
  3. Gateway failure handling: a controlled failure degrades the tool set and
     produces a clear message with no internals in it

Usage (from starter/):
    uv run python ../scripts/test_required_fixes.py
"""

from __future__ import annotations

import io
import contextlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "starter"))

import main as agent_module  # noqa: E402

REQUIRED_KEYS = ("points_redeemed", "tier_discount_pct", "remaining_points")
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, PASS if ok else FAIL, detail))


def run_sandbox_code(loyalty_points, tier, order_total, product_category="standard") -> dict:
    """Execute the sandbox calculation locally and capture its JSON result.

    This is the same code string that is sent to the Code Interpreter; running it
    with the plain interpreter gives the arithmetic the same treatment.
    """
    header = (
        f"loyalty_points = {int(loyalty_points)}\n"
        f"tier = {tier!r}\n"
        f"order_total = {float(order_total)}\n"
        f"product_category = {product_category!r}\n"
    )
    source = header + agent_module.LOYALTY_CALCULATION
    buffer = io.StringIO()
    namespace: dict = {}
    with contextlib.redirect_stdout(buffer):
        exec(compile(source, "<loyalty>", "exec"), namespace)
    return json.loads(buffer.getvalue())


# ── 1. Redemption boundaries ────────────────────────────────────────────────
print("=" * 78)
print("1. LOYALTY REDEMPTION RULES — 500-point blocks and the 50% cap")
print("=" * 78)
print(f"{'points':>7} {'order':>8} {'tier':>7} | {'redeemed':>9} {'discount':>9} "
      f"{'50% cap':>8} | {'remaining':>9}")

BOUNDARY_CASES = [
    # (points, order_total, tier, expected points_redeemed)
    (499, 150.00, "Gold", 0),        # just below the first block
    (500, 150.00, "Gold", 500),      # exactly one block (the minimum redemption)
    (501, 150.00, "Gold", 500),      # just above: still one block
    (999, 150.00, "Gold", 500),      # just below the second block
    (1000, 150.00, "Gold", 1000),    # exactly two blocks
    (4250, 150.00, "Gold", 4000),    # the documented example: 4250 -> 4000
    (5000, 45.00, "Gold", 2000),     # cap is 2250 -> floored to 2000 (the bug)
    (5000, 100.00, "Gold", 5000),    # exactly 50% of the order: allowed
    (5000, 10.00, "Gold", 500),      # cap is exactly one block
    (5000, 9.99, "Gold", 0),         # cap is under one block: nothing redeems
]

for points, order_total, tier, expected in BOUNDARY_CASES:
    out = run_sandbox_code(points, tier, order_total)
    redeemed = out["points_redeemed"]
    discount = out["points_discount"]
    cap_value = round(order_total * 0.5, 2)

    print(f"{points:>7} {order_total:>8.2f} {tier:>7} | {redeemed:>9} "
          f"{discount:>9.2f} {cap_value:>8.2f} | {out['remaining_points']:>9}")

    check(
        f"balance {points:>4} / order ${order_total:>7.2f} -> {expected} points redeemed",
        redeemed == expected,
        f"got {redeemed}",
    )
    # Invariants that must hold for every single case:
    check(
        f"balance {points:>4} / order ${order_total:>7.2f} -> whole 500 blocks",
        redeemed % 500 == 0,
        f"{redeemed} is not a multiple of 500",
    )
    check(
        f"balance {points:>4} / order ${order_total:>7.2f} -> within the 50% cap",
        discount <= cap_value + 0.001,
        f"discount {discount} exceeds {cap_value}",
    )

# The documented example from the project brief, end to end.
example = run_sandbox_code(4250, "Gold", 150.00)
check(
    "documented example (Gold, 4250 pts, $150): $40 points + $11 tier = $99",
    (example["points_discount"], example["tier_discount"], example["final_total"])
    == (40.0, 11.0, 99.0),
    json.dumps({k: example[k] for k in ("points_discount", "tier_discount", "final_total")}),
)

# ── 2. Result contract ──────────────────────────────────────────────────────
print()
print("=" * 78)
print("2. RESULT CONTRACT — required fields in both paths")
print("=" * 78)

sandbox_result = run_sandbox_code(4250, "Gold", 150.00)
fallback_result = agent_module._fallback_breakdown(4250, "Gold", 150.00, "standard")

for label, payload in (("sandbox", sandbox_result), ("fallback", fallback_result)):
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    check(f"{label}: required keys present", not missing, f"missing {missing}")
    types = {k: type(payload[k]).__name__ for k in REQUIRED_KEYS if k in payload}
    print(f"  {label:<9} keys={sorted(payload)}")
    print(f"  {label:<9} types={types}")
    numeric = all(
        isinstance(payload.get(k), (int, float)) and not isinstance(payload.get(k), bool)
        for k in REQUIRED_KEYS
    )
    check(f"{label}: required keys are numeric", numeric, str(types))

extra = set(fallback_result) - set(sandbox_result)
check(
    "fallback returns the same key set as the sandbox (plus a note)",
    extra == {"note"},
    f"unexpected difference: {extra}",
)
check(
    "fallback does not redeem points without the sandbox",
    fallback_result["points_redeemed"] == 0
    and fallback_result["remaining_points"] == fallback_result["loyalty_points"]
    + fallback_result["points_earned"],
    json.dumps({k: fallback_result[k] for k in ("points_redeemed", "remaining_points")}),
)

# ── 3. Gateway failure handling ─────────────────────────────────────────────
print()
print("=" * 78)
print("3. GATEWAY FAILURE — controlled failure, clear message, no internals")
print("=" * 78)

# 3a. A real connection to a Gateway that cannot be reached.
original_url = agent_module.GATEWAY_URL
agent_module.GATEWAY_URL = "http://127.0.0.1:9/mcp"  # closed port: connection refused
tools: list = []
started = time.time()
try:
    client, problem = agent_module._attach_gateway_tools(tools, startup_timeout=5)
    raised = None
except Exception as exc:  # pragma: no cover — this is the failure we are testing
    raised = exc
    client, problem = None, None
elapsed = time.time() - started
agent_module.GATEWAY_URL = original_url

print(f"  unreachable Gateway -> problem={problem!r} tools={len(tools)} "
      f"raised={raised!r} ({elapsed:.1f}s)")
check("unreachable Gateway does not raise", raised is None, repr(raised))
check("unreachable Gateway returns a plain-language reason", problem is not None and "connection" in problem)
check("turn continues without the Gateway tools", tools == [] and client is None)

# 3b. The hook cancels order/refund calls when the Gateway is known to be down.
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent  # noqa: E402

hook = agent_module.GatewayErrorHook(gateway_available=False, reason="the connection to the service failed")
before = BeforeToolCallEvent(
    agent=None, selected_tool=None,
    tool_use={"name": "order-tracker___get_order", "toolUseId": "t1", "input": {"order_id": "ORD-001"}},
    invocation_state={},
)
hook._on_before_tool_call(before)
cancel_message = before.cancel_tool
print(f"  cancelled call -> {cancel_message!r}")
check("order call is cancelled with a message", isinstance(cancel_message, str) and bool(cancel_message))
check("message identifies the failed operation", "the order lookup" in str(cancel_message))
check("message offers a next step", agent_module.SUPPORT_LINE in str(cancel_message))

before_other = BeforeToolCallEvent(
    agent=None, selected_tool=None,
    tool_use={"name": "search_knowledge_base", "toolUseId": "t2", "input": {"query": "policy"}},
    invocation_state={},
)
hook._on_before_tool_call(before_other)
check("non-Gateway tools are not cancelled", before_other.cancel_tool is False)

# 3c. A call that reached the Gateway and failed: the raw error is replaced.
raw_error = (
    "ClientError: An error occurred (AccessDenied) when calling the Invoke operation: "
    "User: arn:aws:sts::032189013563:assumed-role/voclabs/user is not authorized. "
    "endpoint=https://wah32tfea3.execute-api.us-east-1.amazonaws.com/prod secret=abc123"
)
after = AfterToolCallEvent(
    agent=None, selected_tool=None,
    tool_use={"name": "refund-processor___initiate_refund", "toolUseId": "t3", "input": {"order_id": "ORD-002"}},
    invocation_state={},
    result={"toolUseId": "t3", "status": "error", "content": [{"text": raw_error}]},
    exception=RuntimeError(raw_error),
)
hook._on_after_tool_call(after)
clean_text = after.result["content"][0]["text"]
print(f"  rewritten result -> {clean_text!r}")
check("failed call keeps error status", after.result["status"] == "error")
check("rewritten message identifies the operation", "the refund request" in clean_text)
check("rewritten message is actionable", agent_module.SUPPORT_LINE in clean_text)
for leaked in ("032189013563", "execute-api", "arn:aws", "secret=abc123", "AccessDenied"):
    check(f"no internals leaked into the reply ({leaked})", leaked not in clean_text)

# 3d. A successful call is left exactly as it was.
good = {"toolUseId": "t4", "status": "success", "content": [{"text": "ORD-001 shipped via UPS"}]}
after_ok = AfterToolCallEvent(
    agent=None, selected_tool=None,
    tool_use={"name": "order-tracker___get_order", "toolUseId": "t4", "input": {}},
    invocation_state={}, result=dict(good),
)
hook._on_after_tool_call(after_ok)
check("successful Gateway call is untouched", after_ok.result == good, str(after_ok.result))

# 3e. Failure classification.
classifications = [
    (TimeoutError("Read timed out"), "the request timed out"),
    (RuntimeError("ThrottlingException: Rate exceeded"), "the service is throttling requests"),
    (RuntimeError("Connection reset by peer"), "the connection to the service failed"),
    (RuntimeError("ResourceNotFoundException: no such order"), "the requested record was not found"),
    (RuntimeError("something else entirely"), "the service returned an error"),
]
for error, expected in classifications:
    check(
        f"failure classified as '{expected}'",
        agent_module._describe_failure(error) == expected,
        agent_module._describe_failure(error),
    )

# 3f. Wiring: the hook must register against a real registry, and the agent must
# still assemble with only the local tools when the Gateway is down.
from strands.hooks import HookRegistry  # noqa: E402

try:
    registry = HookRegistry()
    agent_module.GatewayErrorHook(gateway_available=False, reason="x").register_hooks(registry)
    check("hook registers against a real HookRegistry", True)
except Exception as exc:  # pragma: no cover
    check("hook registers against a real HookRegistry", False, repr(exc))

try:
    degraded_agent = agent_module.Agent(
        model=agent_module.model,
        tools=[agent_module.search_knowledge_base, agent_module.calculate_loyalty_discount],
        hooks=[agent_module.GatewayErrorHook(gateway_available=False, reason="the connection to the service failed")],
        system_prompt="degraded-path assembly check",
    )
    check("agent assembles with the degradation hook", degraded_agent is not None)
except Exception as exc:  # pragma: no cover
    check("agent assembles with the degradation hook", False, repr(exc))

# ── 4. The agent stays responsive when a Gateway call fails ──────────────────
# Section 3 tested the pieces; this runs a whole agent turn with a stub model
# (no AWS calls) so the loop is exercised end to end: the model asks for a
# Gateway tool, the tool fails, and the turn still finishes with a reply.
print()
print("=" * 78)
print("4. AGENT TURN WITH A FAILING GATEWAY TOOL (stub model, no AWS calls)")
print("=" * 78)

from strands.models.model import Model  # noqa: E402
from strands.types.tools import ToolResult  # noqa: E402


class StubModel(Model):
    """Deterministic stand-in for the LLM: asks for a tool, then answers."""

    def __init__(self) -> None:
        self.turns = 0

    def update_config(self, **model_config) -> None:  # pragma: no cover
        return None

    def get_config(self) -> dict:  # pragma: no cover
        return {}

    def structured_output(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.turns += 1
        saw_tool_result = any(
            any("toolResult" in block for block in (m.get("content") or []))
            for m in messages
        )
        yield {"messageStart": {"role": "assistant"}}
        if not saw_tool_result:
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": "order-tracker___get_order", "toolUseId": "stub-1"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"order_id": "ORD-001"}'}}}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": (
                "I could not look up that order right now. Please try again in a "
                "moment, or call support on 1-800-555-0199."
            )}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if not saw_tool_result else "end_turn"}}


@agent_module.tool(name="order-tracker___get_order", description="Order lookup (stub that fails)")
def _failing_order_lookup(order_id: str) -> str:
    raise RuntimeError(
        "ClientError: AccessDenied; endpoint=https://wah32tfea3.execute-api.us-east-1.amazonaws.com "
        "role=arn:aws:iam::032189013563:role/csai-gateway-role"
    )


stub_agent = agent_module.Agent(
    model=StubModel(),
    tools=[_failing_order_lookup],
    hooks=[agent_module.GatewayErrorHook(gateway_available=True)],
    system_prompt="test agent — gateway failure path",
)

try:
    turn = stub_agent("Can you track order ORD-001?")
    reply = turn.message["content"][0]["text"]
    crashed = None
except Exception as exc:  # pragma: no cover
    crashed, reply = exc, ""
print(f"  model turns: {stub_agent.model.turns} | crashed: {crashed!r}")
print(f"  reply: {reply!r}")

check("turn completes instead of raising", crashed is None, repr(crashed))
check("agent answers the customer", bool(reply.strip()))
check("reply offers a next step", agent_module.SUPPORT_LINE in reply)

tool_results = [
    block["toolResult"]
    for message in stub_agent.messages
    for block in (message.get("content") or [])
    if isinstance(block, dict) and "toolResult" in block
]
print(f"  tool results in the conversation: {len(tool_results)}")
if tool_results:
    result_text = json.dumps(tool_results[0])
    print(f"  result seen by the model: {tool_results[0].get('content')!r}"[:300])
    check("failed call returns an error result, not a crash", tool_results[0].get("status") == "error")
    check("result names the failed operation", "the order lookup" in result_text)
    for leaked in ("032189013563", "execute-api", "arn:aws", "AccessDenied"):
        check(f"no internals reach the model ({leaked})", leaked not in result_text)
else:
    check("the failing call produced a tool result", False, "no tool results found")

# ── Summary ─────────────────────────────────────────────────────────────────
print()
print("=" * 78)
passed = sum(1 for _, status, _ in results if status == PASS)
failed = [(name, detail) for name, status, detail in results if status == FAIL]
print(f"SUMMARY: {passed}/{len(results)} checks passed")

if failed:
    print()
    for name, detail in failed:
        print(f"  FAILED: {name} {detail}")
    sys.exit(1)

print("All required fixes verified.")
