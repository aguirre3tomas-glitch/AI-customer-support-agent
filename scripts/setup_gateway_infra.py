"""Deploy the P2 infrastructure: REST API + AgentCore Gateway with 2 targets.

Idempotent-ish: re-running recreates missing pieces and reuses existing ones
where the API allows it. Writes all IDs to infra_config.json in the repo root.

Run from the starter folder:  uv run python ../scripts/setup_gateway_infra.py
"""
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
GATEWAY_NAME = "CustomerSupportGateway"
GW_ROLE_NAME = "csai-gateway-role"
STAGE = "prod"

REPO = Path(__file__).resolve().parents[1]
CFG_PATH = REPO / "infra_config.json"
LAMBDA_SCHEMA = REPO / "starter" / "lambda" / "lambda_schema"

apigw = boto3.client("apigateway", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
ac = boto3.client("bedrock-agentcore-control", region_name=REGION)

acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
ORDER_TRACKER_ARN = f"arn:aws:lambda:{REGION}:{acct}:function:order-tracker"
REFUND_PROCESSOR_ARN = f"arn:aws:lambda:{REGION}:{acct}:function:refund-processor"

cfg = {}
if CFG_PATH.exists():
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))

ROUTES = [
    ("orders", "{order_id}", "/orders/{order_id}", "get_order"),
    ("customers", "{customer_id}", "/customers/{customer_id}", "get_customer"),
]
# NOTE: /customers/{customer_id}/orders (get_customer_orders) is created as a
# sub-resource of /customers/{customer_id} further down.


def find_or_create_rest_api() -> str:
    apis = apigw.get_rest_apis()["items"]
    for a in apis:
        if a["name"] == "customer-support-api":
            print(f"REST API exists: {a['id']}")
            return a["id"]
    a = apigw.create_rest_api(name="customer-support-api", description="P2 order tracking API")
    print(f"REST API created: {a['id']}")
    return a["id"]


def ensure_resource(api_id: str, parent_id: str, part: str) -> str:
    for r in apigw.get_resources(restApiId=api_id)["items"]:
        if r.get("parentId") == parent_id and r["pathPart"] == part:
            return r["id"]
    r = apigw.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)
    print(f"  resource created: {part}")
    return r["id"]


def ensure_get_method(api_id: str, resource_id: str, resource_path: str, op_name: str) -> None:
    try:
        apigw.get_method(restApiId=api_id, resourceId=resource_id, httpMethod="GET")
        print(f"  method exists: GET {resource_path}")
    except ClientError:
        apigw.put_method(
            restApiId=api_id, resourceId=resource_id, httpMethod="GET",
            authorizationType="NONE", operationName=op_name,
        )
        print(f"  method created: GET {resource_path} (op={op_name})")

    try:
        apigw.get_integration(restApiId=api_id, resourceId=resource_id, httpMethod="GET")
        print(f"  integration exists: GET {resource_path}")
        return
    except ClientError:
        pass

    uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{ORDER_TRACKER_ARN}/invocations"
    apigw.put_integration(
        restApiId=api_id, resourceId=resource_id, httpMethod="GET",
        type="AWS_PROXY", integrationHttpMethod="POST", uri=uri,
    )
    print(f"  integration created: GET {resource_path} -> order-tracker")


# ── 1. REST API ───────────────────────────────────────────────────────────────
print("== API Gateway ==")
api_id = find_or_create_rest_api()
root_id = apigw.get_resources(restApiId=api_id)["items"][0]["id"]

orders_id = ensure_resource(api_id, root_id, "orders")
order_id_res = ensure_resource(api_id, orders_id, "{order_id}")
ensure_get_method(api_id, order_id_res, "/orders/{order_id}", "get_order")

cust_id = ensure_resource(api_id, root_id, "customers")
customer_id_res = ensure_resource(api_id, cust_id, "{customer_id}")
ensure_get_method(api_id, customer_id_res, "/customers/{customer_id}", "get_customer")

cust_orders_res = ensure_resource(api_id, customer_id_res, "orders")
ensure_get_method(api_id, cust_orders_res, "/customers/{customer_id}/orders", "get_customer_orders")

# Deploy to stage (ConflictException = nothing changed since the last deploy)
try:
    apigw.create_deployment(restApiId=api_id, stageName=STAGE, description="deploy")
    print(f"  deployed to stage: {STAGE}")
except ClientError as exc:
    if exc.response["Error"]["Code"] == "ConflictException":
        print(f"  stage {STAGE}: up to date (nothing new to deploy)")
    else:
        raise

# Let API Gateway invoke the Lambda
try:
    lam.add_permission(
        FunctionName="order-tracker",
        StatementId="apigw-invoke",
        Action="lambda:InvokeFunction",
        Principal="apigateway.amazonaws.com",
        SourceArn=f"arn:aws:execute-api:{REGION}:{acct}:{api_id}/*",
    )
    print("  lambda permission added: apigateway -> order-tracker")
except ClientError as exc:
    if "ResourceConflictException" in str(exc):
        print("  lambda permission already present")
    else:
        raise

api_url = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE}"
cfg |= {"rest_api_id": api_id, "stage": STAGE, "api_url": api_url}

# ── 2. Gateway IAM role ───────────────────────────────────────────────────────
print("\n== IAM role para Gateway ==")
trust = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}
try:
    role = iam.create_role(RoleName=GW_ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))
    role_arn = role["Role"]["Arn"]
    print(f"role {GW_ROLE_NAME}: created")
except ClientError as exc:
    if exc.response["Error"]["Code"] == "EntityAlreadyExists":
        role_arn = iam.get_role(RoleName=GW_ROLE_NAME)["Role"]["Arn"]
        print(f"role {GW_ROLE_NAME}: already exists")
    else:
        raise

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": [ORDER_TRACKER_ARN, REFUND_PROCESSOR_ARN]},
        {"Effect": "Allow", "Action": "execute-api:Invoke", "Resource": f"arn:aws:execute-api:{REGION}:{acct}:{api_id}/*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{REGION}:{acct}:*"},
    ],
}
iam.put_role_policy(RoleName=GW_ROLE_NAME, PolicyName="GatewayInvoke", PolicyDocument=json.dumps(policy))
print("inline policy: put")
cfg["gateway_role_arn"] = role_arn

# ── 3. AgentCore Gateway ──────────────────────────────────────────────────────
print("\n== AgentCore Gateway ==")
gw_id = cfg.get("gateway_id")
if gw_id:
    print(f"gateway id en config: {gw_id}")
else:
    # reuse an existing gateway with this name if present
    found = None
    for g in ac.list_gateways().get("items", []):
        if g.get("name") == GATEWAY_NAME:
            found = g
            break
    if found:
        gw_id = found["gatewayId"]
        print(f"gateway existente reutilizado: {gw_id}")
    else:
        gw = ac.create_gateway(  # flat response, not nested
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="NONE",
            description="P2 customer support gateway",
        )
        gw_id = gw["gatewayId"]
        print(f"gateway created: {gw_id}")

# esperar READY
gw = ac.get_gateway(gatewayIdentifier=gw_id)
status = gw.get("status")
for _ in range(30):
    gw = ac.get_gateway(gatewayIdentifier=gw_id)
    status = gw.get("status")
    if status == "READY":
        break
    print(f"  gateway status: {status} ...")
    time.sleep(10)

gw_url = gw.get("gatewayUrl") or ""
print(f"gateway status: {gw.get('status')} | url: {gw_url}")
cfg |= {"gateway_id": gw_id, "gateway_url": gw_url}

# ── 4. Targets ────────────────────────────────────────────────────────────────
print("\n== Targets ==")
targets = ac.list_gateway_targets(gatewayIdentifier=gw_id).get("items", [])
existing = {t["name"]: t for t in targets}
print("targets existentes:", list(existing))

if "order-tracker" not in existing:
    ac.create_gateway_target(
        gatewayIdentifier=gw_id,
        name="order-tracker",
        targetConfiguration={
            "mcp": {
                "apiGateway": {
                    "restApiId": api_id,
                    "stage": STAGE,
                    "apiGatewayToolConfiguration": {
                        "toolFilters": [
                            {"filterPath": "/orders/{order_id}", "methods": ["GET"]},
                            {"filterPath": "/customers/{customer_id}/orders", "methods": ["GET"]},
                            {"filterPath": "/customers/{customer_id}", "methods": ["GET"]},
                        ],
                        "toolOverrides": [
                            {"name": "get_order",   "path": "/orders/{order_id}",         "method": "GET"},
                            {"name": "get_customer_orders", "path": "/customers/{customer_id}/orders", "method": "GET"},
                            {"name": "get_customer", "path": "/customers/{customer_id}", "method": "GET"},
                        ],
                    },
                }
            }
        },
    )
    print("target order-tracker: created")
else:
    print("target order-tracker: exists")

schema = json.loads(LAMBDA_SCHEMA.read_text(encoding="utf-8"))
if "refund-processor" not in existing:
    ac.create_gateway_target(
        gatewayIdentifier=gw_id,
        name="refund-processor",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": REFUND_PROCESSOR_ARN,
                    "toolSchema": {"inlinePayload": schema},
                }
            }
        },
    )
    print("target refund-processor: created")
else:
    print("target refund-processor: exists")

CFG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print(f"\nConfig guardada en {CFG_PATH}")
print(json.dumps(cfg, indent=2))
