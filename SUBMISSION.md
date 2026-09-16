# Project 2 — Customer Support AI Agent (cd14763)

Submission pack for the AgentCore customer support agent: what was built, how it
was verified, and where the evidence lives.

## Deliverables

| Requirement | Where |
|---|---|
| `main.py` with all 8 TODO sections implemented (no `pass` placeholders) | `starter/main.py` |
| Test 1 — Order Tracking | `evidence/test1-order-tracking.txt` |
| Test 2 — Refund Processing | `evidence/test2-refund.txt` |
| Test 3 — Knowledge Base (RAG) | `evidence/test3-knowledge-base.txt` |
| Test 4 — Long-Term Memory (both sessions) | `evidence/test4a-memory-session-a.txt`, `evidence/test4b-memory-session-b.txt` |
| Test 5 — Loyalty Discount Calculation | `evidence/test5-loyalty-discount.txt` |
| Test 6 — Browser Tool | `evidence/test6-browser.txt` |
| Written reflection | this file, below |
| CloudWatch alarm on runtime errors (extra) | `evidence/cloudwatch-alarm.txt` |

Every test ran against the deployed Runtime (`agentcore invoke`), not a local
harness, and the raw command output is stored next to this file.

## Result summary

1. **Order tracking** — ORD-001 → shipped, UPS, tracking `TRK987654321`, ETA 2026-09-18.
2. **Refund** — ORD-002 refunded, `REF-…` returned, APPROVED, "3–5 business days".
3. **Knowledge base** — Platinum tier answer pulled from the catalog: same-day shipping, 15% discount, priority support.
4. **Memory** — session A introduced "Jane, prefers concise"; session B (new session, same customer) recalled both.
5. **Loyalty math** — 4,000 points redeemed ($40), 10% Gold tier (−$11), final $99, 150 points earned, 400 remaining — computed in the Code Interpreter sandbox.
6. **Browser** — live page title read through the AgentCore browser session.

## Environment notes (honest record)

- The lab account **denies `aoss:*` and `s3vectors:*`**, so the OpenSearch
  Serverless collection the setup page describes cannot be created here. The
  knowledge base is therefore a **Bedrock *managed* knowledge base**
  (`CustomerSupportKB`, Titan Embeddings v2, S3 data source, ingestion COMPLETE)
  — Bedrock provisions and runs the vector store. The agent code is unchanged:
  it still calls `bedrock-agent-runtime.retrieve(knowledgeBaseId=KB_ID, …)`.
- Runtime deploys as `direct_code_deploy` on Python 3.13 (the toolkit CLI caps
  below 3.14), linux/arm64, from the frozen `starter/requirements.txt`.
- Deep-dive log of every infrastructure step, bug and fix: `PLAN.md`.

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
error on Playwright's driver binary — the executable bit was lost because the
deployment archive was built on Windows. The runtime logs pointed straight at
it, and correcting the archive's file modes fixed it. That fix uncovered a
sneakier second problem: with the tool finally reachable, the model kept calling
it with an invalid session name (underscores are rejected) and retried in a
loop. Adding explicit argument rules and a "fail twice, then tell the customer"
instruction to the system prompt ended the loop — and both are now written down
where the next person will trip over them.

For production I would extend this in three ways: validate tool inputs and
outputs against Pydantic models so malformed data fails loudly instead of
reaching the customer; summarise session history once it exceeds a token budget
instead of dropping turns; and keep hardening the deployment path (frozen
requirements, scoped IAM, and the packaging fixes) so a fresh environment can be
stood up reproducibly.
