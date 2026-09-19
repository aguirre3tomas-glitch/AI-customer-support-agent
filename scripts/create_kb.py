"""Create the CustomerSupportKB Knowledge Base.

The lab account denies aoss:* and s3vectors:* (no identity-based policy allows
them), so the OpenSearch Serverless collection cannot be created here. The
fallback that fits this environment is a MANAGED knowledge base: Bedrock
provisions and manages the vector store itself.

The S3 bucket with product_catalog.txt already exists (probe_kb_env.py).

Run from the starter folder:  uv run python ../scripts/create_kb.py
"""
import json
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ACCT = "032189013563"
BUCKET = f"csai-kb-{ACCT}-us-east-1"
KB_NAME = "CustomerSupportKB"
DS_NAME = "product-catalog-s3"
ROLE_NAME = "csai-kb-role"
EMBED_ARN = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"

REPO = Path(__file__).resolve().parents[1]
CFG_PATH = REPO / "infra_config.json"

ba = boto3.client("bedrock-agent", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)

cfg = json.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}
role_arn = f"arn:aws:iam::{ACCT}:role/{ROLE_NAME}"
s3 = boto3.client("s3", region_name=REGION)

# ── 0. prerequisites: the bucket and the service role ────────────────────────
# Both are created here so this script also works on a fresh account. A lab reset
# wipes them, and create_knowledge_base then fails with "Unable to verify the
# specified embedding model. Please verify the role permissions..." because the
# role it is told to assume does not exist.
try:
    s3.head_bucket(Bucket=BUCKET)
    print(f"bucket exists: {BUCKET}")
except ClientError:
    s3.create_bucket(Bucket=BUCKET)
    print(f"bucket created: {BUCKET}")

catalog = REPO / "starter" / "product_catalog.txt"
s3.upload_file(str(catalog), BUCKET, "product_catalog.txt")
print(f"catalog uploaded: {catalog.name} -> s3://{BUCKET}/product_catalog.txt")

try:
    iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )
    print(f"role created: {ROLE_NAME}")
except ClientError as exc:
    if exc.response["Error"]["Code"] != "EntityAlreadyExists":
        raise
    print(f"role exists: {ROLE_NAME}")

iam.put_role_policy(
    RoleName=ROLE_NAME,
    PolicyName="CsaiKbAccess",
    PolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["bedrock:InvokeModel"],
                    "Resource": [f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"],
                },
            ],
        }
    ),
)
print("role policy attached: CsaiKbAccess")

# ── 1. find or create the KB ──────────────────────────────────────────────────
kb_id = cfg.get("kb_id")
if kb_id:
    print(f"KB id en config: {kb_id}")
else:
    existing = ba.list_knowledge_bases().get("knowledgeBaseSummaries", [])
    for k in existing:
        if k["name"] == KB_NAME:
            kb_id = k["knowledgeBaseId"]
            print(f"KB existente reutilizada: {kb_id}")
            break

if not kb_id:
    try:
        kb = ba.create_knowledge_base(
            name=KB_NAME,
            description="Product catalog + store policies for the customer support agent",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {
                    "embeddingModelType": "CUSTOM",
                    "embeddingModelArn": EMBED_ARN,
                },
            },
        )["knowledgeBase"]
        kb_id = kb["knowledgeBaseId"]
        print(f"KB created: {kb_id} (type={kb.get('knowledgeBaseConfiguration',{}).get('type')})")
    except ClientError as exc:
        print("create_knowledge_base FAILED:", exc.response["Error"]["Code"])
        print("  ", exc.response["Error"].get("Message", "")[:300])
        raise SystemExit(1)

# ── 2. wait for ACTIVE ────────────────────────────────────────────────────────
for i in range(60):
    kb = ba.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
    status = kb["status"]
    if status in ("ACTIVE", "FAILED"):
        break
    print(f"  [{i}] status: {status}")
    time.sleep(10)
print(f"KB status: {status}")

if status == "FAILED":
    print("  reasons:", kb.get("failureReasons"))
    raise SystemExit(1)

cfg["kb_id"] = kb_id
cfg["kb_role_arn"] = role_arn
cfg["kb_bucket"] = BUCKET

# ── 3. data source ────────────────────────────────────────────────────────────
ds_id = cfg.get("kb_data_source_id")
if not ds_id:
    for d in ba.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", []):
        if d["name"] == DS_NAME:
            ds_id = d["dataSourceId"]
            print(f"data source existente: {ds_id}")
            break

if not ds_id:
    ds = ba.create_data_source(
        knowledgeBaseId=kb_id,
        name=DS_NAME,
        dataSourceConfiguration={
            "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
            "managedKnowledgeBaseConnectorConfiguration": {
                "connectorParameters": {
                    "type": "S3",
                    "version": "1",
                    "connectionConfiguration": {
                        "bucketName": BUCKET,
                        "bucketOwnerAccountId": ACCT,
                    },
                },
            },
        },
    )["dataSource"]
    ds_id = ds["dataSourceId"]
    print(f"data source created: {ds_id}")

cfg["kb_data_source_id"] = ds_id

# managed data sources are created asynchronously: wait for AVAILABLE
for i in range(60):
    ds = ba.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
    st = ds["status"]
    if st in ("AVAILABLE", "FAILED"):
        break
    print(f"  [{i}] data source status: {st}")
    time.sleep(10)
print(f"data source status: {st}")
if st == "FAILED":
    print("  reasons:", ds.get("failureReasons"))
    raise SystemExit(1)

# ── 4. ingestion job ──────────────────────────────────────────────────────────
job = ba.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
job_id = job["ingestionJobId"]
print(f"ingestion job: {job_id} status={job['status']}")

for i in range(60):
    job = ba.get_ingestion_job(
        knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id,
    )["ingestionJob"]
    status = job["status"]
    stats = job.get("statistics", {})
    if status in ("COMPLETE", "FAILED", "STOPPED"):
        break
    print(f"  [{i}] {status} scanned={stats.get('numberOfDocumentsScanned')} indexed={stats.get('numberOfNewDocumentsIndexed')}")
    time.sleep(10)

print(f"ingestion: {status} :: scanned={stats.get('numberOfDocumentsScanned')} "
      f"indexed={stats.get('numberOfNewDocumentsIndexed')} failed={stats.get('numberOfDocumentsFailed')}")
if job.get("failureReasons"):
    print("  reasons:", job["failureReasons"][:2])

# ── 5. sanity retrieve ────────────────────────────────────────────────────────
try:
    bar = boto3.client("bedrock-agent-runtime", region_name=REGION)
    r = bar.retrieve(knowledgeBaseId=kb_id, retrievalQuery={"text": "What is the return policy for electronics?"})
    hits = r.get("retrievalResults", [])
    print(f"\nretrieve: {len(hits)} hits")
    for h in hits[:2]:
        print("   -", h["content"]["text"][:160].replace("\n", " "))
except ClientError as exc:
    print("retrieve FAILED:", exc.response["Error"]["Code"], exc.response["Error"].get("Message","")[:200])

CFG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print(f"\nConfig guardada en {CFG_PATH}")
