"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, sys, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── Runtime bootstrap ─────────────────────────────────────────────────────────
# The deployment archive is built on Windows, which doesn't carry POSIX mode
# bits: Playwright's bundled node driver lands in /var/task without the execute
# permission and the browser tool dies with
#   PermissionError: [Errno 13] Permission denied: '/var/task/playwright/driver/node'
# If the bundled driver can't run, copy it to /tmp and point Playwright at the copy.

def _ensure_playwright_driver() -> None:
    """Make Playwright's node driver runnable inside the AgentCore runtime."""
    if sys.platform == "win32":
        return
    try:
        import shutil
        import subprocess
        from pathlib import Path

        import playwright

        driver = Path(playwright.__file__).parent / "driver" / "node"
        if not driver.exists():
            return

        try:
            probe = subprocess.run(
                [str(driver), "--version"], capture_output=True, timeout=20,
            )
            runs = probe.returncode == 0
        except Exception:  # noqa: BLE001 — not executable here; copy it below
            runs = False
        if runs:
            return  # the bundled driver already runs

        target = Path("/tmp/playwright-node")
        if not target.exists():
            shutil.copy2(driver, target)
        target.chmod(0o755)
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(target)
        logger.info("playwright driver copied to %s", target)
    except Exception as exc:  # noqa: BLE001 — never block startup over this
        logger.warning("playwright driver bootstrap skipped: %s", exc)


_ensure_playwright_driver()


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Values collected while setting up the AWS resources (Part 1).

GATEWAY_URL = "https://customersupportgateway-4grevewyco.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "PGV0IQVSWO"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-HQ2KtBHkEs"


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# The agent model plus the two clients every tool below shares.

model_id = "global.amazon.nova-2-lite-v1:0"

model = BedrockModel(model_id=model_id)

memory_client = MemoryClient(region_name=REGION)

_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# The memory resource carries two strategies; each one owns a namespace
# template that still needs the actorId filled in before use.

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)
    return {
        strategy["type"]: strategy["namespaces"][0]
        for strategy in strategies
        if strategy.get("namespaces")
    }


def _memory_text(record: Dict) -> str:
    """Pull plain text out of a memory record (shape varies by strategy)."""
    content = record.get("content")
    if isinstance(content, dict):
        text = content.get("text") or content.get("stringValue") or ""
    elif isinstance(content, str):
        text = content
    else:
        inner = record.get("memoryRecord") or {}
        content = inner.get("content") if isinstance(inner, dict) else None
        text = content.get("text", "") if isinstance(content, dict) else ""

    # strategies store structured JSON — flatten it into readable lines
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(data, dict):
        return "; ".join(f"{k}: {v}" for k, v in data.items())
    return text


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Long-term memory, wired through the Strands hook system:
#   • before the model sees a user turn  → prepend what we already know
#   • after the agent answers           → save the turn back to memory

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        messages = event.agent.messages
        if not messages:
            return

        last = messages[-1]
        if last.get("role") != "user":
            return

        content = last.get("content") or []
        if not content or "toolResult" in content[0]:
            return

        query = content[0].get("text")
        if not query:
            return

        memories = []
        for strategy_type, namespace in self.namespaces.items():
            namespace = namespace.format(actorId=self.actor_id)
            for record in self.memory_client.retrieve_memories(
                memory_id=self.memory_id,
                namespace=namespace,
                query=query,
                top_k=5,
            ):
                text = _memory_text(record).strip()
                if text:
                    memories.append(f"[{strategy_type}] {text}")

        if memories:
            context_block = "\n".join(memories)
            content[0]["text"] = f"Customer Context:\n{context_block}\n\n{query}"

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        customer_query = None
        agent_response = None

        for message in reversed(event.agent.messages):
            content = message.get("content") or []
            if not content:
                continue
            text = content[0].get("text")
            if not text:
                continue
            if message.get("role") == "assistant" and agent_response is None:
                agent_response = text
            elif message.get("role") == "user" and customer_query is None:
                # strip the context block this hook prepended, so memory
                # stores the customer's own words and nothing else
                if text.startswith("Customer Context:"):
                    text = text.split("\n\n", 1)[-1]
                customer_query = text
            if customer_query and agent_response:
                break

        if customer_query and agent_response:
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")],
            )

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Grounds policy and product answers in the knowledge base instead of the
# model's memory.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        response = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
    except Exception as exc:  # noqa: BLE001 — surface the reason to the model
        logger.error("knowledge base lookup failed: %s", exc)
        return f"Knowledge base lookup failed: {exc}"

    results = response.get("retrievalResults", [])
    if not results:
        return "No relevant information found in the knowledge base."

    chunks = [
        r["content"]["text"]
        for r in results
        if r.get("content", {}).get("text")
    ]
    return "\n---\n".join(chunks) if chunks else "No relevant information found in the knowledge base."


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Money math runs in the AgentCore Code Interpreter sandbox: exact arithmetic,
# and the agent never has to trust the model to add up.

LOYALTY_CALCULATION = """
import json

earn_rates = {"standard": 1, "device": 2, "fresh": 5}
tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}

POINT_VALUE = 0.01          # 100 points = $1
POINT_VALUE_CENTS = 1       # ... which is one cent per point
REDEMPTION_STEP = 500       # points redeem in blocks of 500 (and 500 is the minimum)
MAX_REDEEM_SHARE = 0.5      # points can cover at most half of the order

tier_rate = tier_rates.get(tier, 0.0)
earn_rate = earn_rates.get(product_category, 1)

# Redemption rules, applied in this order:
#   1. a balance only redeems in whole 500-point blocks, so it is floored to the
#      block below it first (499 points redeem nothing, 999 redeem 500);
#   2. the redeemed value can never exceed half of the order total;
#   3. the cap itself is floored to a block as well — without this second floor a
#      $45 order would allow 2250 points ($22.50), which is inside the cap but is
#      not a valid block, so only 2000 points ($20.00) may be redeemed.
balance_blocks = (int(loyalty_points) // REDEMPTION_STEP) * REDEMPTION_STEP
order_cents = int(round(order_total * 100))
cap_points = int((order_cents * MAX_REDEEM_SHARE) // POINT_VALUE_CENTS)
points_redeemed = min(balance_blocks, cap_points)
points_redeemed = (points_redeemed // REDEMPTION_STEP) * REDEMPTION_STEP
points_discount = round(points_redeemed * POINT_VALUE, 2)

subtotal = round(order_total - points_discount, 2)
tier_discount = round(subtotal * tier_rate, 2)
final_total = round(subtotal - tier_discount, 2)
total_savings = round(points_discount + tier_discount, 2)

points_earned = int(order_total * earn_rate)
remaining_points = int(loyalty_points - points_redeemed + points_earned)

print(json.dumps({
    "loyalty_points": loyalty_points,
    "tier": tier,
    "tier_rate": tier_rate,
    "tier_discount_pct": round(tier_rate * 100, 2),
    "order_total": order_total,
    "product_category": product_category,
    "points_redeemed": points_redeemed,
    "points_discount": points_discount,
    "tier_discount": tier_discount,
    "total_savings": total_savings,
    "final_total": final_total,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}))
"""


TIER_RATES = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
EARN_RATES = {"standard": 1, "device": 2, "fresh": 5}


def _fallback_breakdown(
    loyalty_points: int, tier: str, order_total: float, product_category: str,
) -> dict:
    """Tier-discount-only result, shaped exactly like the sandbox result.

    Result contract: the sandbox path and this fallback path return the same
    keys with the same meaning, so a caller never has to check which path ran.
    ``points_redeemed``, ``tier_discount_pct`` and ``remaining_points`` are
    always present and always numeric.

    Points are deliberately not redeemed here — without the sandbox there is no
    exact arithmetic to back the redemption rules, so the balance is reported
    unchanged (plus the points this order earns).
    """
    tier_rate = TIER_RATES.get(tier, 0.0)
    earn_rate = EARN_RATES.get(product_category, 1)
    points_balance = int(loyalty_points)
    tier_discount = round(order_total * tier_rate, 2)
    points_earned = int(order_total * earn_rate)
    return {
        "loyalty_points": points_balance,
        "tier": tier,
        "tier_rate": tier_rate,
        "tier_discount_pct": round(tier_rate * 100, 2),
        "order_total": order_total,
        "product_category": product_category,
        "points_redeemed": 0,
        "points_discount": 0.0,
        "tier_discount": tier_discount,
        "total_savings": tier_discount,
        "final_total": round(order_total - tier_discount, 2),
        "points_earned": points_earned,
        "remaining_points": points_balance + points_earned,
        "note": "Code Interpreter unavailable — tier discount only, points not redeemed.",
    }


@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    header = (
        f"loyalty_points = {int(loyalty_points)}\n"
        f"tier = {tier!r}\n"
        f"order_total = {float(order_total)}\n"
        f"product_category = {product_category!r}\n"
    )
    code = header + LOYALTY_CALCULATION

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {"code": code, "language": "python", "clearContext": True},
            )
            for event in response.get("stream", []):
                if "result" not in event:
                    continue
                parts = [
                    item.get("text", "")
                    for item in event["result"].get("content", [])
                    if isinstance(item, dict)
                ]
                output = "\n".join(p for p in parts if p).strip()
                if output:
                    return output
            return "Code Interpreter returned no output."
    except Exception as exc:  # noqa: BLE001 — fall back to a plain calculation
        logger.error("code interpreter unavailable (%s); using fallback", exc)
        return json.dumps(
            _fallback_breakdown(loyalty_points, tier, order_total, product_category)
        )


# ── Gateway error handling ───────────────────────────────────────────────────
# The Gateway is the only remote dependency behind the agent's tools, and it can
# fail in two different ways: the MCP connection may be unreachable when the turn
# starts, or a call may reach it and fail (Lambda timeout, throttling, dropped
# connection). Neither case should reach the customer as silence, as a raw stack
# trace, or — worst of all — as invented order details.

SUPPORT_LINE = "1-800-555-0199"
GATEWAY_TOOL_PREFIXES = ("order-tracker___", "refund-processor___")
GATEWAY_OPERATIONS = {
    "order-tracker___get_order": "the order lookup",
    "order-tracker___get_customer_orders": "the customer order list",
    "order-tracker___get_customer": "the customer profile lookup",
    "refund-processor___initiate_refund": "the refund request",
    "refund-processor___check_refund_status": "the refund status check",
    "refund-processor___get_return_label": "the return label",
}


def _is_gateway_tool(name: str) -> bool:
    """True for the tools served by the Gateway (order tracking and refunds)."""
    return any(str(name).startswith(prefix) for prefix in GATEWAY_TOOL_PREFIXES)


def _failure_text(error: object, _depth: int = 0) -> str:
    """Every message in an exception tree, flattened into one string.

    A connection failure reaches us wrapped: an MCPClientInitializationError
    whose cause is an ExceptionGroup whose own child is the ConnectError that
    actually says what happened. The top-level message mentions none of it, so
    ``__cause__``, ``__context__`` and ExceptionGroup children are all walked
    (with a depth guard, since exception chains can be cyclic).

    The result is only ever used to classify the failure — never shown to a
    customer.
    """
    if _depth > 6:
        return ""
    parts = [str(error)]
    nested: list = list(getattr(error, "exceptions", None) or [])
    cause = getattr(error, "__cause__", None)
    context = getattr(error, "__context__", None)
    if cause is not None:
        nested.append(cause)
    if context is not None and context is not cause:
        nested.append(context)
    for inner in nested:
        parts.append(_failure_text(inner, _depth + 1))
    return " | ".join(p for p in parts if p)


def _describe_failure(error: object) -> str:
    """Short, safe description of a failure — never the raw error text.

    A raw exception can carry URLs, request ids or other internals, so failures
    are classified into a handful of plain-language reasons instead of being
    passed through.
    """
    text = _failure_text(error).lower()
    if "timeout" in text or "timed out" in text:
        return "the request timed out"
    if "throttl" in text or "rate exceeded" in text:
        return "the service is throttling requests"
    if "connection" in text or "connect" in text or "network" in text:
        return "the connection to the service failed"
    if "not found" in text or "no such" in text:
        return "the requested record was not found"
    return "the service returned an error"


def _gateway_failure_message(tool_name: str, reason: str) -> str:
    """One message for every Gateway failure: what failed and what to do next.

    This is the text the model receives, so it also states the rule for the
    reply: acknowledge the failure, offer a next step, never invent the data.
    """
    operation = GATEWAY_OPERATIONS.get(tool_name, "the requested operation")
    return (
        f"Gateway failure: {operation} could not be completed — {reason}. "
        f"No order or refund data is available for this request and nothing was "
        f"changed. Tell the customer plainly that {operation} is unavailable right "
        f"now, do not invent order, refund or tracking details, and offer to retry "
        f"in a moment or to call support on {SUPPORT_LINE}."
    )


class GatewayErrorHook(HookProvider):
    """Turns Gateway failures into clear messages instead of crashes or silence.

    Both failure modes are covered:
      * the Gateway was unreachable when the turn started, so no tools were
        loaded — order and refund calls are cancelled before they are attempted,
        with the reason attached, so the model can explain what is unavailable;
      * a call reached the Gateway and failed — the raw error result is replaced
        by the same kind of message, keeping internals away from the customer.
    """

    def __init__(self, gateway_available: bool = True, reason: str | None = None) -> None:
        self.gateway_available = gateway_available
        self.reason = reason or "the service did not respond"

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        tool_name = (event.tool_use or {}).get("name", "")
        if self.gateway_available or not _is_gateway_tool(tool_name):
            return
        # The call would fail anyway: cancel it with a message the model can use.
        event.cancel_tool = _gateway_failure_message(tool_name, self.reason)

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        tool_name = (event.tool_use or {}).get("name", "")
        if not _is_gateway_tool(tool_name):
            return
        result = event.result or {}
        failed = event.exception is not None or result.get("status") == "error"
        if not failed:
            return
        reason = _describe_failure(event.exception or result)
        event.result = {
            "toolUseId": result.get("toolUseId", ""),
            "status": "error",
            "content": [{"text": _gateway_failure_message(tool_name, reason)}],
        }


def _attach_gateway_tools(tools: list, startup_timeout: int = 30):
    """Start the Gateway connection and append its MCP tools to ``tools``.

    Returns ``(client, problem)``. ``problem`` is None when the tools loaded, or
    a short description when the Gateway could not be reached — in which case the
    turn continues without them: policy answers, discount math and the browser
    still work, and order or refund questions get an honest answer instead of
    taking the whole request down.
    """
    client = MCPClient(
        lambda: streamable_http_client(GATEWAY_URL), startup_timeout=startup_timeout,
    )
    try:
        client.start()
        tools.extend(client.list_tools_sync())
        return client, None
    except Exception as exc:  # noqa: BLE001 — one remote dependency must not kill the turn
        logger.error("gateway tools unavailable: %s", exc)
        client.stop(None, None, None)  # start() can fail with the session half-open
        return None, _describe_failure(exc)


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# One invocation = one customer turn. The hook handles memory, the tools cover
# the Knowledge Base / Gateway / sandbox / browser, and the prompt keeps the
# answers grounded in what those tools actually returned.

system_prompt = """You are the customer support assistant for our online store: friendly, precise,
and quick to the point.

How you work:
- Ground every fact in a tool result. Never invent order details, refund IDs, prices,
  policies, or tracking numbers.
- Answer the question the customer actually asked, with the specifics that matter
  (order status, tracking number, carrier, dates, refund IDs, amounts).

Tools you can reach:
- order-tracker___get_order — look up a single order by order_id.
- order-tracker___get_customer_orders — every order for a customer.
- order-tracker___get_customer — customer profile: name, loyalty points, tier.
- refund-processor___initiate_refund — start a refund (needs order_id and reason).
  Confirm the order and the reason first; never guess an amount. When you tell the
  customer a refund went through, always quote the refund ID the tool returned.
- refund-processor___check_refund_status — follow up on a refund already filed.
- refund-processor___get_return_label — produce a prepaid return label.
- search_knowledge_base — product specs, return policy, warranty, loyalty program.
  Search this before answering any policy or product question.
- calculate_loyalty_discount — runs the exact discount math (points + tier) in a sandbox.
  Use it whenever the customer asks what they would pay.
- browser — for anything on the public web (for example, reading a page title).
  Browser session names must use lowercase letters, digits and dashes only
  (e.g. `udacity-page`) — underscores are rejected.
- If a tool rejects your arguments, fix them once. If the same tool fails a
  second time, stop and tell the customer what could not be done instead of
  retrying.

Memory:
When a line starting with "Customer Context:" appears in the conversation, it is what you
already know about this customer from earlier sessions. Use it naturally, never recite it
back, and never claim to remember something that is not there.

Escalation:
If a tool fails or the data is missing, say so plainly and offer the next best step
(for example our support line at 1-800-555-0199) instead of guessing."""


@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent

    The turn never fails silently. A Gateway problem degrades only the order and
    refund tools and tells the customer what could not be done; anything else
    unexpected returns a plain message with a next step instead of a stack trace.
    """
    try:
        user_input = payload.get("prompt")
        actor_id = payload.get("customer_id") or "guest"
        session_id = payload.get("session_id") or str(uuid.uuid4())

        memory_hook = MemoryHook(actor_id, session_id, memory_client, MEMORY_ID)
        browser = AgentCoreBrowser(region=REGION)

        tools = [search_knowledge_base, calculate_loyalty_discount, browser.browser]
        mcp_client, gateway_problem = _attach_gateway_tools(tools)
        if gateway_problem:
            logger.warning("continuing without gateway tools (%s)", gateway_problem)

        try:
            agent = Agent(
                model=model,
                tools=tools,
                hooks=[
                    memory_hook,
                    GatewayErrorHook(gateway_problem is None, gateway_problem),
                ],
                system_prompt=system_prompt,
            )
            result = agent(user_input)
        finally:
            if mcp_client is not None:
                mcp_client.stop(None, None, None)

        return result.message["content"][0]["text"]

    except Exception as exc:  # noqa: BLE001 — never drop a customer request
        # Full detail goes to the logs; the customer gets a message they can act
        # on. The raw error is kept out of the reply: it can carry request ids,
        # URLs and other internals.
        logger.error("invoke failed: %s", exc)
        return (
            "Sorry, I could not complete that request — something went wrong on our "
            f"side. Please try again in a moment, or call support on {SUPPORT_LINE}."
        )


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
