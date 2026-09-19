"""Repair the REST API so the AgentCore Gateway can import its spec.

Two problems the gateway's OAS parser flagged when adding the order-tracker
target:

  1. A duplicate subtree (/orders/orders/..., /orders/customers/...) created by
     an early version of setup_gateway_infra.py that picked the root resource by
     list position instead of by path. Duplicate paths => repeated operationIds.
  2. Every method was created with put_method but never got a method response,
     so the exported OAS has no `responses` block. The parser requires one.

Also clears a gateway target left in FAILED state so it can be recreated.

Run from the starter folder:  uv run python ../scripts/repair_rest_api.py
"""
import json
import time
from pathlib import Path

import boto3

REGION = "us-east-1"
STAGE = "prod"

REPO = Path(__file__).resolve().parents[1]
CFG_PATH = REPO / "infra_config.json"
CFG = json.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}
API_ID = CFG.get("rest_api_id")
GW_ID = CFG.get("gateway_id")
if not API_ID or not GW_ID:
    raise SystemExit(
        "infra_config.json has no rest_api_id / gateway_id — run setup_gateway_infra.py first"
    )

apigw = boto3.client("apigateway", region_name=REGION)
ac = boto3.client("bedrock-agentcore-control", region_name=REGION)

KEEP = {
    "/",
    "/orders",
    "/orders/{order_id}",
    "/customers",
    "/customers/{customer_id}",
    "/customers/{customer_id}/orders",
}
# only these three carry a GET method
METHOD_PATHS = {"/orders/{order_id}", "/customers/{customer_id}", "/customers/{customer_id}/orders"}

# ── 1. prune stray resources (deepest first) ─────────────────────────────────
items = apigw.get_resources(restApiId=API_ID)["items"]
strays = sorted(
    (r for r in items if r["path"] not in KEEP),
    key=lambda r: len(r["path"]),
    reverse=True,
)
if strays:
    for r in strays:
        apigw.delete_resource(restApiId=API_ID, resourceId=r["id"])
        print(f"stray deleted: {r['path']}")
else:
    print("no stray resources")

# ── 2. method responses (the gateway OAS parser requires `responses`) ────────
for r in sorted(apigw.get_resources(restApiId=API_ID)["items"], key=lambda r: r["path"]):
    if r["path"] in METHOD_PATHS:
        apigw.put_method_response(
            restApiId=API_ID, resourceId=r["id"], httpMethod="GET", statusCode="200",
        )
        print(f"method response 200: GET {r['path']}")

# ── 3. redeploy ──────────────────────────────────────────────────────────────
apigw.create_deployment(
    restApiId=API_ID, stageName=STAGE,
    description="repair: prune duplicate subtree + add method responses",
)
print(f"deployed to stage: {STAGE}")

# ── 4. verify the spec the gateway will parse ────────────────────────────────
body = apigw.get_export(
    restApiId=API_ID, stageName=STAGE, exportType="oas30", accepts="application/json",
)["body"]
spec = json.loads(body.read())

print("\nOAS paths:")
ok = True
seen_ops: set[str] = set()
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        opid = op.get("operationId")
        resps = list(op.get("responses", {}))
        flag = ""
        if not resps:
            flag += " RESPONSES-MISSING"
            ok = False
        if opid in seen_ops:
            flag += " OPID-REPEATED"
            ok = False
        seen_ops.add(opid)
        print(f"  {method.upper():6} {path:45} opId={opid!r} responses={resps or '-'}{flag}")
print("spec OK" if ok else "spec STILL has problems")

# ── 5. clear a FAILED gateway target so it can be recreated ──────────────────
for t in ac.list_gateway_targets(gatewayIdentifier=GW_ID).get("items", []):
    if t.get("status") == "FAILED":
        ac.delete_gateway_target(gatewayIdentifier=GW_ID, targetId=t["targetId"])
        print(f"\ntarget {t['name']} (FAILED): deleted")
        for _ in range(24):
            names = {
                x["name"]
                for x in ac.list_gateway_targets(gatewayIdentifier=GW_ID).get("items", [])
            }
            if t["name"] not in names:
                break
            time.sleep(5)
        print(f"target {t['name']}: removed, ready to recreate")
