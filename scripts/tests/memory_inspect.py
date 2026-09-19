"""Inspect what AgentCore Memory actually stored for a customer."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "starter"))

import main as agent_module  # noqa: E402

mc = agent_module.memory_client
mid = agent_module.MEMORY_ID
actor = "CUST-123"

print("memory:", mid)
for s in mc.get_memory_strategies(mid):
    print(f"  strategy {s.get('type')}: {s.get('name')} ns={s.get('namespaces')} status={s.get('status')}")

namespaces = agent_module.get_namespaces(mc, mid)
print("\nnamespaces resueltos:", namespaces)

for strategy_type, template in namespaces.items():
    namespace = template.format(actorId=actor)
    print(f"\n--- {strategy_type} @ {namespace}")
    try:
        records = mc.retrieve_memories(
            memory_id=mid, namespace=namespace, query="name communication preference", top_k=5,
        )
        print(f"  records: {len(records)}")
        for r in records:
            print("   ", str(r)[:300])
    except Exception as exc:  # noqa: BLE001
        print("  retrieve failed:", exc)

print("\n--- sesiones registradas ---")
try:
    print(mc.list_sessions(memory_id=mid, actor_id=actor))
except Exception as exc:  # noqa: BLE001
    print("  list_sessions:", exc)
