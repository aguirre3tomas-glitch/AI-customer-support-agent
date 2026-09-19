# Project 2: Customer Support AI Agent (cd14763)

Submission pack for the AgentCore customer support agent: what was built, how it
was verified, and where the evidence lives.

## Deliverables

| Requirement | Where |
|---|---|
| `main.py` with all 8 TODO sections implemented (no `pass` placeholders) | `starter/main.py` |
| Test 1: Order Tracking | `evidence/test1-order-tracking.txt` |
| Test 2: Refund Processing | `evidence/test2-refund.txt` |
| Test 3: Knowledge Base (RAG) | `evidence/test3-knowledge-base.txt` |
| Test 4: Long-Term Memory (both sessions) | `evidence/test4a-memory-session-a.txt`, `evidence/test4b-memory-session-b.txt` |
| Test 5: Loyalty Discount Calculation | `evidence/test5-loyalty-discount.txt` |
| Test 6: Browser Tool | `evidence/test6-browser.txt` |
| Written reflection | this file, below |
| CloudWatch alarm on runtime errors (extra) | `evidence/cloudwatch-alarm.txt` |
| Regression suite for the three review findings | `evidence/test-required-fixes.txt` (69/69 checks) |
| Tool invocations behind the captured outputs (annotation) | `evidence/tool-invocations-annotated.txt` |
| Gateway verified over MCP, both targets, no agent in the loop | `evidence/mcp-gateway-verification.txt` |

Every test ran against the deployed Runtime (`agentcore invoke`), not a local
harness, and the raw command output is stored next to this file. Each test file
also carries the tool calls the runtime logged for that turn, read from its
CloudWatch log stream.

## Result summary

1. **Order tracking:** ORD-001 shipped, UPS, tracking `TRK987654321`, ETA 2026-09-21 (tool: `order-tracker___get_order`, API Gateway target).
2. **Refund:** ORD-002 refunded, `REF-QJ8N3BW7`, APPROVED, "3-5 business days" (tools: `order-tracker___get_order` then `refund-processor___initiate_refund`, direct Lambda target).
3. **Knowledge base:** Platinum tier answer retrieved from the catalog, with same-day shipping, 15% discount and priority support (tool: `search_knowledge_base`).
4. **Memory:** session A introduced "Jane, prefers concise responses"; session B, a separate session for the same customer, opened with "Hello Jane! Yes, I remember you're a Gold loyalty member and you prefer concise responses."
5. **Loyalty math:** computed in the Code Interpreter sandbox (tool: `calculate_loyalty_discount`), 4,000 points redeemed ($40), 10% Gold tier ($11), final $99, 400 points remaining, 150 earned.
6. **Browser:** the live page title read through the AgentCore browser session (tool: `browser`).

## Environment notes (honest record)

- The lab account **denies `aoss:*` and `s3vectors:*`**, so the OpenSearch
  Serverless collection the setup page describes cannot be created here. The
  knowledge base is therefore a **Bedrock *managed* knowledge base**
  (`CustomerSupportKB`, Titan Embeddings v2, S3 data source, ingestion COMPLETE),
  in which Bedrock provisions and runs the vector store. The agent code is unchanged:
  it still calls `bedrock-agent-runtime.retrieve(knowledgeBaseId=KB_ID, …)`.
- The lab account was **reset between sessions and came back empty**: no runtime,
  Gateway, memory, knowledge base, Lambda or bucket. Everything the evidence above
  was captured against was then rebuilt from the idempotent scripts in `scripts/`,
  which closed three gaps they had (REST API method responses, knowledge base
  bucket and role creation, and identifiers carrying a deployment-specific
  suffix). Detail in `PLAN.md`.
- Runtime deploys as `direct_code_deploy` on Python 3.13 (the toolkit CLI caps
  below 3.14), linux/arm64, from the frozen `starter/requirements.txt`.
- Deep-dive log of every infrastructure step, bug and fix: `PLAN.md`.

## Review findings and corrections

Three issues were raised in review. All three were reproduced, fixed and covered
by a regression suite that runs locally (`scripts/test_required_fixes.py`, output
in `evidence/test-required-fixes.txt`, 69 checks). The suite includes a whole
agent turn driven by a stub model (no AWS calls) in which a Gateway tool fails:
the turn still completes and the agent answers the customer.

**1. Gateway failures were not surfaced clearly.** The order and refund tools are
the agent's only remote dependency, and the original code let a Gateway problem
either escape as a generic message containing the raw exception text, or fail the
turn entirely. Two failure modes are now handled at the Gateway boundary:

- *Unreachable Gateway.* `_attach_gateway_tools()` starts the MCP connection and
  loads the tools; if that fails, the turn continues with the local tools (policy
  questions, discount math, browser) and the failure reason is passed to a hook.
  A `GatewayErrorHook` cancels any order or refund call before it is attempted
  and hands the model a message naming the failed operation and the next step.
- *Call reached the Gateway and failed* (timeout, throttling, dropped connection,
  Lambda error). The `AfterToolCallEvent` hook replaces the raw error result with
  the same kind of message, so no internals reach the customer.

Raw exception text is never forwarded: `_describe_failure()` classifies the error
into a plain reason ("the request timed out", "the connection to the service
failed", …) and `_failure_text()` walks the whole exception chain, including the
`ExceptionGroup` wrapper the MCP client raises, because the outer message alone
says nothing about the cause. The final catch-all reply in `invoke()` no longer
embeds the exception either.

**2. Points redemption boundaries were wrong.** Redemption floors the balance to
500-point blocks and caps the redeemed value at half the order, but the cap was
applied *after* the floor, so the result could land off a block: a $45 order
allowed 2250 points ($22.50), which is inside the cap but is not a valid
redemption. The cap is now computed in integer cents and floored to a block as
well, so both rules hold at once. Boundaries are tested just below, at and above
a 500-point boundary, plus orders where the cap binds.

**3. The result did not expose every required field.** `tier_discount_pct` was
missing from both paths, and the fallback returned neither `points_redeemed` nor
`remaining_points`. The fallback now builds its result through
`_fallback_breakdown()`, which returns the same key set as the sandbox path:
`points_redeemed` (int), `tier_discount_pct` (float, percentage form) and
`remaining_points` (int) are always present and always numeric. The regression
suite asserts both paths for the same keys and types.

The Test 5 output captured after the change confirms it end to end: 4250 points on
a $150 order redeem 4,000 points ($40), the Gold tier adds $11 (10% of the $110
subtotal), the final total is $99 and 400 points remain. The agent now calls the
calculator instead of working the numbers out itself.

## Reflection

The design decision I am happiest with is how memory is wired in. Instead of
sprinkling memory calls through the agent code, I implemented a `MemoryHook` (a
Strands `HookProvider`) with two callbacks: one that runs before the model sees
a user turn and prepends what we already know about the customer, and one that
runs after the agent answers and saves the turn back. The agent code stays free
of memory plumbing, and the hook gave me one place to fix a subtle problem: the
context block I prepend would otherwise be stored straight back into memory and
pollute future retrievals, so the save hook strips it and stores only the
customer's own words. I also flatten the structured JSON the strategies return
into readable lines, which visibly improved how naturally the model used the
context.

The hardest part was the gap between "works locally" and "works on the Runtime".
Locally every test passed; deployed, the browser test failed with a permission
error on Playwright's driver binary: the executable bit was lost because the
deployment archive was built on Windows. The runtime logs pointed straight at
it, and correcting the archive's file modes fixed it. That fix uncovered a
sneakier second problem: with the tool finally reachable, the model kept calling
it with an invalid session name (underscores are rejected) and retried in a
loop. Adding explicit argument rules and a "fail twice, then tell the customer"
instruction to the system prompt ended the loop, and both are now written down
where the next person will trip over them.

For production I would extend this in three ways: validate tool inputs and
outputs against Pydantic models so malformed data fails loudly instead of
reaching the customer; summarise session history once it exceeds a token budget
instead of dropping turns; and keep hardening the deployment path (frozen
requirements, scoped IAM, and the packaging fixes) so a fresh environment can be
stood up reproducibly.
