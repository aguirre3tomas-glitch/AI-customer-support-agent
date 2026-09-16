"""Deploy the two project Lambdas (order-tracker, refund-processor).

Idempotent: creates the IAM role + attaches basic execution policy,
zips each handler, and creates (or updates) both Lambda functions.

Run from the starter folder:  uv run python ../scripts/deploy_lambdas.py
"""
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ROLE_NAME = "csai-lambda-basic-role"

REPO = Path(__file__).resolve().parents[1]
LAMBDA_DIR = REPO / "starter" / "lambda"
FUNCTIONS = [
    ("order-tracker", LAMBDA_DIR / "order_tracker.py"),
    ("refund-processor", LAMBDA_DIR / "refund_processor.py"),
]

iam = boto3.client("iam", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)

# ── 1. IAM role ───────────────────────────────────────────────────────────────
trust = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}

try:
    iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))
    print(f"role {ROLE_NAME}: created")
except ClientError as exc:
    code = exc.response["Error"]["Code"]
    if code == "EntityAlreadyExists":
        print(f"role {ROLE_NAME}: already exists")
    else:
        sys.exit(f"create_role failed: {exc}")

role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
print("role arn:", role_arn)

try:
    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("basic execution policy: attached")
except ClientError as exc:
    print("attach policy:", exc.response["Error"]["Code"])


def zip_bytes(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, os.path.basename(path))
    return buf.getvalue()


# ── 2. Functions ──────────────────────────────────────────────────────────────
for name, src in FUNCTIONS:
    handler = os.path.basename(src).replace(".py", "") + ".lambda_handler"
    code = zip_bytes(src)
    print(f"\n{name}: deploying ({src.name}, handler={handler})")

    created = False
    for attempt in range(1, 7):  # IAM propagation can take a few seconds
        try:
            resp = lam.create_function(
                FunctionName=name,
                Runtime="python3.12",
                Role=role_arn,
                Handler=handler,
                Code={"ZipFile": code},
                Timeout=30,
                MemorySize=256,
            )
            print("  created:", resp["FunctionArn"])
            created = True
            break
        except ClientError as exc:
            code_ = exc.response["Error"]["Code"]
            msg = exc.response["Error"].get("Message", "")
            if code_ == "ResourceConflictException" or "already exist" in msg.lower():
                resp = lam.update_function_code(FunctionName=name, ZipFile=code)
                print("  updated:", resp["FunctionArn"])
                created = True
                break
            if "cannot be assumed" in msg.lower() or code_ == "InvalidParameterValueException":
                print(f"  attempt {attempt}: role not ready yet ({code_}), retrying in 10s...")
                time.sleep(10)
                continue
            raise
    if not created:
        sys.exit(f"{name}: could not create after retries")

    lam.get_waiter("function_active").wait(FunctionName=name)
    cfg = lam.get_function(FunctionName=name)["Configuration"]
    print("  state:", cfg["State"], "| runtime:", cfg["Runtime"], "| arn:", cfg["FunctionArn"])

print("\nDone.")
