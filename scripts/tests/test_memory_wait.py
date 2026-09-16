"""Wait for the memory strategy extraction and probe namespace variants."""
import json
import sys
import time

sys.path.insert(0, ".")

import main as m  # noqa: E402

ns = m.get_namespaces(m.memory_client, m.MEMORY_ID)
print("namespaces:", json.dumps(ns, indent=1))

print("\nevents in session smoke-1:")
evs = m.memory_client.list_events(
    memory_id=m.MEMORY_ID, actor_id="CUST-123", session_id="smoke-1",
)
print(f"  {len(evs)} events")

variants = [
    ("exact facts", {"namespace": "cs_agent/CUST-123/facts"}),
    ("exact facts trailing /", {"namespace": "cs_agent/CUST-123/facts/"}),
    ("path prefix", {"namespace_path": "cs_agent/CUST-123/"}),
    ("exact prefs", {"namespace": "cs_agent/CUST-123/preferences"}),
]

query = "What is the customer's name and communication preference?"
for attempt in range(10):
    found = False
    for label, kwargs in variants:
        try:
            recs = m.memory_client.retrieve_memories(
                memory_id=m.MEMORY_ID, query=query, top_k=5, **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [{attempt}] {label}: ERROR {exc}")
            continue
        if recs:
            print(f"  [{attempt}] {label}: {len(recs)} records")
            for r in recs:
                print("    -", json.dumps(r, default=str)[:220])
            found = True
    if found:
        break
    print(f"  [{attempt}] nothing yet — waiting 30s")
    time.sleep(30)

print("\ndone")
