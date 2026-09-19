"""Tear down every AWS resource this project created, in us-east-1.

Keeps the same guarantee as the setup scripts: it is written to be run twice.
Anything already gone is reported and skipped instead of aborting the run, so a
partial teardown can simply be repeated.

Run from the starter folder:  uv run python ../scripts/teardown.py
"""

import json
import pathlib
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
REPO = pathlib.Path(__file__).resolve().parents[1]
CFG_PATH = REPO / "infra_config.json"
CFG = json.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}

ac = boto3.client("bedrock-agentcore-control", region_name=REGION)
bac = boto3.client("bedrock-agent", region_name=REGION)
apigw = boto3.client("apigateway", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)

AGENT_NAME = "customer_support_agent"
BUCKETS = [
    CFG.get("kb_bucket", "csai-kb-032189013563-us-east-1"),
    "bedrock-agentcore-codebuild-sources-032189013563-us-east-1",
]
FUNCTIONS = ["order-tracker", "refund-processor"]
ROLE_PREFIXES = (
    "csai-",
    "AmazonBedrockAgentCoreSDKRuntime-us-east-1-",
)


def say(text=""):
    print(text, flush=True)


def attempt(label, fn, *args, **kwargs):
    """Run one deletion, report the outcome, never abort the whole teardown."""
    try:
        result = fn(*args, **kwargs)
        say(f"  deleted: {label}")
        return result
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "NoSuchEntity", "404", "NotFound"):
            say(f"  already gone: {label}")
        else:
            say(f"  FAILED ({code}): {label} :: {exc.response['Error'].get('Message', '')[:120]}")
        return None
    except Exception as exc:  # noqa: BLE001
        say(f"  FAILED: {label} :: {exc}")
        return None


say("=" * 78)
say("TEARDOWN")
say("=" * 78)

# 1. AgentCore Runtime --------------------------------------------------------
say("\n1. Runtime")
runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
mine = [r for r in runtimes if r["agentRuntimeName"] == AGENT_NAME]
if not mine:
    say("  no runtime of this project found")
for r in mine:
    runtime_id = r["agentRuntimeId"]
    if attempt(f"runtime {runtime_id}", ac.delete_agent_runtime, agentRuntimeId=runtime_id):
        for _ in range(30):
            status = [
                x for x in ac.list_agent_runtimes().get("agentRuntimes", [])
                if x["agentRuntimeId"] == runtime_id
            ]
            if not status:
                break
            time.sleep(10)

# 2. Gateway and its targets --------------------------------------------------
say("\n2. Gateway")
gateways = ac.list_gateways().get("items", [])
if not gateways:
    say("  no gateway found")
for gw in gateways:
    gw_id = gw["gatewayId"]
    targets = ac.list_gateway_targets(gatewayIdentifier=gw_id).get("items", [])
    for t in targets:
        attempt(f"gateway target {t['name']}",
                ac.delete_gateway_target, gatewayIdentifier=gw_id, targetId=t["targetId"])
    attempt(f"gateway {gw_id}", ac.delete_gateway, gatewayIdentifier=gw_id)

# 3. Memory -------------------------------------------------------------------
say("\n3. Memory")
memories = ac.list_memories().get("memories", [])
if not memories:
    say("  no memory resource found")
for m in memories:
    attempt(f"memory {m['id']}", ac.delete_memory, memoryId=m["id"])

# 4. Knowledge base -----------------------------------------------------------
say("\n4. Knowledge base")
for kb in bac.list_knowledge_bases().get("knowledgeBaseSummaries", []):
    kb_id = kb["knowledgeBaseId"]
    for ds in bac.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", []):
        attempt(f"data source {ds['name']}",
                bac.delete_data_source, knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
    attempt(f"knowledge base {kb_id}", bac.delete_knowledge_base, knowledgeBaseId=kb_id)

# 5. Lambda and API Gateway ---------------------------------------------------
say("\n5. Lambda functions and REST API")
for fn in FUNCTIONS:
    attempt(f"lambda {fn}", lam.delete_function, FunctionName=fn)

rest_apis = apigw.get_rest_apis().get("items", [])
if rest_apis:
    for api in rest_apis:
        attempt(f"REST API {api['name']} ({api['id']})",
                apigw.delete_rest_api, restApiId=api["id"])
else:
    say("  no REST API found")

# 6. S3 buckets ---------------------------------------------------------------
say("\n6. S3 buckets")
for bucket in BUCKETS:
    try:
        keys = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            keys += [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            say(f"  emptied: {bucket} ({len(keys)} objects)")
        attempt(f"bucket {bucket}", s3.delete_bucket, Bucket=bucket)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NoSuchBucket", "404"):
            say(f"  already gone: bucket {bucket}")
        else:
            say(f"  FAILED ({code}): bucket {bucket}")

# 7. CloudWatch ---------------------------------------------------------------
say("\n7. CloudWatch alarm, metric filters and log groups")
attempt("alarm customer-support-agent-errors",
        cw.delete_alarms, AlarmNames=["customer-support-agent-errors"])

groups = logs.describe_log_groups().get("logGroups", [])
for g in groups:
    name = g["logGroupName"]
    if ("bedrock-agentcore" in name and AGENT_NAME in name) or name.startswith("/aws/lambda/"):
        for f in logs.describe_metric_filters(logGroupName=name).get("metricFilters", []):
            attempt(f"metric filter {f['filterName']}", logs.delete_metric_filter,
                    logGroupName=name, filterName=f["filterName"])
        attempt(f"log group {name}", logs.delete_log_group, logGroupName=name)

# 8. IAM roles ----------------------------------------------------------------
say("\n8. IAM roles")
for role in iam.list_roles(MaxItems=1000)["Roles"]:
    if not role["RoleName"].startswith(ROLE_PREFIXES):
        continue
    name = role["RoleName"]
    for p in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
        attempt(f"policy {p} on {name}", iam.delete_role_policy, RoleName=name, PolicyName=p)
    for p in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", []):
        attempt(f"detach {p['PolicyName']} from {name}", iam.detach_role_policy,
                RoleName=name, PolicyArn=p["PolicyArn"])
    attempt(f"role {name}", iam.delete_role, RoleName=name)

# 9. What is left -------------------------------------------------------------
say("\n" + "=" * 78)
say("WHAT IS STILL THERE")
say("=" * 78)
say(f"  runtimes:      {len(ac.list_agent_runtimes().get('agentRuntimes', []))}")
say(f"  gateways:      {len(ac.list_gateways().get('items', []))}")
say(f"  memories:      {len(ac.list_memories().get('memories', []))}")
say(f"  knowledge bases: {len(bac.list_knowledge_bases().get('knowledgeBaseSummaries', []))}")
say(f"  lambda functions: {len(lam.list_functions().get('Functions', []))}")
say(f"  REST APIs:     {len(apigw.get_rest_apis().get('items', []))}")
say(f"  buckets:       {len(s3.list_buckets().get('Buckets', []))}")
say(f"  alarms:        {len(cw.describe_alarms().get('MetricAlarms', []))}")
say(f"  iam roles:     {len(iam.list_roles(MaxItems=1000)['Roles'])}")
say()
say("Teardown finished.")
