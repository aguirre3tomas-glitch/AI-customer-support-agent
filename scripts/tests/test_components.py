"""Component test for main.py pieces (no agent loop yet)."""
import json
import sys

sys.path.insert(0, ".")

import main as m  # noqa: E402

print("=== 1. get_namespaces ===")
ns = m.get_namespaces(m.memory_client, m.MEMORY_ID)
print(json.dumps(ns, indent=1))

print("\n=== 2. search_knowledge_base ===")
tool = m.search_knowledge_base
fn = getattr(tool, "_tool_func", None) or getattr(tool, "func", None) or tool
try:
    out = fn("What is the return policy for electronics?")
except TypeError as exc:
    print("direct call failed:", exc)
    out = None
if out:
    print(str(out)[:600])

print("\n=== 3. calculate_loyalty_discount ===")
tool2 = m.calculate_loyalty_discount
fn2 = getattr(tool2, "_tool_func", None) or getattr(tool2, "func", None) or tool2
try:
    out2 = fn2(4250, "Gold", 150.0, "standard")
except TypeError as exc:
    print("direct call failed:", exc)
    out2 = None
if out2:
    print(str(out2)[:600])

print("\n=== 4. memory round-trip (create_event -> retrieve_memories) ===")
ev = m.memory_client.create_event(
    memory_id=m.MEMORY_ID,
    actor_id="CUST-123",
    session_id="smoke-1",
    messages=[
        ("Hi, I am Jane. I prefer concise responses.", "USER"),
        ("Nice to meet you, Jane. I will keep it brief.", "ASSISTANT"),
    ],
)
print("event created:", json.dumps(ev, default=str)[:200])

import time  # noqa: E402
time.sleep(45)  # let the extraction job run

recs = m.memory_client.retrieve_memories(
    memory_id=m.MEMORY_ID,
    namespace="cs_agent/CUST-123/facts",
    query="What is the customer's name?",
    top_k=5,
)
print(f"\nfacts retrieved: {len(recs)}")
print(json.dumps(recs, default=str, indent=1)[:1200])
