"""Create the CustomerSupportMemory AgentCore memory resource.

Two strategies, exactly as the course specifies:
  - customer_facts       (SEMANTIC)        namespace cs_agent/{actorId}/facts
  - customer_preferences (USER_PREFERENCE) namespace cs_agent/{actorId}/preferences

Run from the starter folder:  uv run python ../scripts/create_memory.py
"""
import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
MEMORY_NAME = "CustomerSupportMemory"

REPO = Path(__file__).resolve().parents[1]
CFG_PATH = REPO / "infra_config.json"
cfg = json.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}

ac = boto3.client("bedrock-agentcore-control", region_name=REGION)

mem_id = cfg.get("memory_id")

if not mem_id:
    for m in ac.list_memories().get("memories", []):
        if m.get("name") == MEMORY_NAME:
            mem_id = m["id"]
            print(f"memory existente reutilizada: {mem_id} ({m.get('status')})")
            break

if not mem_id:
    try:
        mem = ac.create_memory(
            name=MEMORY_NAME,
            description="Long-term memory for the customer support agent",
            eventExpiryDuration=30,
            memoryStrategies=[
                {
                    "semanticMemoryStrategy": {
                        "name": "customer_facts",
                        "description": "Facts the customer shares about themselves or their orders",
                        "namespaces": ["cs_agent/{actorId}/facts"],
                    }
                },
                {
                    "userPreferenceMemoryStrategy": {
                        "name": "customer_preferences",
                        "description": "How the customer likes to be communicated with",
                        "namespaces": ["cs_agent/{actorId}/preferences"],
                    }
                },
            ],
        )
        mem_id = mem["memory"]["id"]
        print(f"memory created: {mem_id}")
    except ClientError as exc:
        print("create_memory FAILED:", exc.response["Error"]["Code"])
        print("  ", exc.response["Error"].get("Message", "")[:400])
        raise SystemExit(1)

# ── wait for ACTIVE ───────────────────────────────────────────────────────────
for i in range(60):
    m = ac.get_memory(memoryId=mem_id)["memory"]
    status = m.get("status")
    if status in ("ACTIVE", "FAILED"):
        break
    print(f"  [{i}] status: {status}")
    time.sleep(10)

print(f"memory status: {status}")
if status == "FAILED":
    print("  reasons:", m.get("statusReasons"))
    raise SystemExit(1)

strategies = m.get("strategies", [])
print("strategies:")
for s in strategies:
    print(f"  - {s.get('name')} [{s.get('type')}] ns={s.get('namespaces')} status={s.get('status')}")

cfg["memory_id"] = mem_id
CFG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print(f"\nConfig guardada en {CFG_PATH}")
print(json.dumps(cfg, indent=2))
