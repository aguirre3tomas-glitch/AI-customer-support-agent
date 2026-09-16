"""Probe script: map what the lab account actually allows for the KB path.

Checks (in order):
  1. product_catalog.txt present in starter/
  2. S3 bucket create + upload
  3. aoss CreateCollection across name/region variants (stops at first success)
  4. KB execution role creation + policy
"""
import json
import pathlib

import boto3
from botocore.exceptions import ClientError

ACCT = "032189013563"
BUCKET = f"csai-kb-{ACCT}-us-east-1"

# ── 1. product catalog present? ───────────────────────────────────────────────
cat = pathlib.Path("product_catalog.txt")
print(f"[1] product_catalog.txt: {'EXISTS' if cat.exists() else 'MISSING'} ({cat.resolve()})")
if cat.exists():
    print(f"    {cat.stat().st_size} bytes")

# ── 2. S3 bucket + upload ─────────────────────────────────────────────────────
s3 = boto3.client("s3", region_name="us-east-1")
try:
    s3.create_bucket(Bucket=BUCKET)
    print(f"[2] bucket created: {BUCKET}")
except ClientError as e:
    code = e.response["Error"]["Code"]
    if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
        print(f"[2] bucket exists: {BUCKET}")
    else:
        print(f"[2] bucket: {code} :: {e.response['Error'].get('Message','')[:120]}")

if cat.exists():
    try:
        s3.upload_file(str(cat), BUCKET, "product_catalog.txt")
        print(f"    uploaded product_catalog.txt -> s3://{BUCKET}/product_catalog.txt")
    except ClientError as e:
        print(f"    upload: {e.response['Error']['Code']}")

# ── 3. aoss probes ────────────────────────────────────────────────────────────
print("[3] aoss CreateCollection probes:")
created = None
for region in ("us-east-1", "us-west-2"):
    oss = boto3.client("opensearchserverless", region_name=region)
    for name in ("customersupportkb", "customer-support-kb", "csai-kb"):
        try:
            r = oss.create_collection(name=name, type="VECTORSEARCH")
            arn = r["createCollectionDetail"]["arn"]
            print(f"    OK {region}/{name} -> {arn}")
            created = (region, name, arn)
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"].get("Message", "")[:110]
            print(f"    {region}/{name}: {code} :: {msg}")
    if created:
        break

if not created:
    print("    => ninguna variante permitida: aoss esta fuera del lab")

# ── 4. KB execution role ──────────────────────────────────────────────────────
iam = boto3.client("iam", region_name="us-east-1")
trust = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}
role_arn = None
try:
    role = iam.create_role(RoleName="csai-kb-role", AssumeRolePolicyDocument=json.dumps(trust))
    role_arn = role["Role"]["Arn"]
    print(f"[4] role created: {role_arn}")
except ClientError as e:
    if e.response["Error"]["Code"] == "EntityAlreadyExists":
        role_arn = iam.get_role(RoleName="csai-kb-role")["Role"]["Arn"]
        print(f"[4] role exists: {role_arn}")
    else:
        print(f"[4] role: {e.response['Error']['Code']}")

if role_arn:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
             "Resource": [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"]},
            {"Effect": "Allow", "Action": "bedrock:InvokeModel",
             "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"},
            {"Effect": "Allow", "Action": "aoss:APIAccessAll", "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName="csai-kb-role", PolicyName="KBPolicy", PolicyDocument=json.dumps(policy))
    print("    inline policy: put")
