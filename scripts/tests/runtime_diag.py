"""Run a shell command inside the deployed AgentCore runtime and print output.

Usage: uv run python ../scripts/tests/runtime_diag.py "sh -c 'ls /var/task'"
"""
import json
import sys
import uuid

import boto3

RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:032189013563:runtime/customer_support_agent-hFhMtcHN2g"
)

cmd = sys.argv[1] if len(sys.argv) > 1 else "sh -c 'ls -la /var/task | head -20'"

c = boto3.client("bedrock-agentcore", region_name="us-east-1")
r = c.invoke_agent_runtime_command(
    agentRuntimeArn=RUNTIME_ARN,
    runtimeSessionId=f"diag-{uuid.uuid4().hex[:32]}",
    body={"command": cmd, "timeout": 60},
)

print("statusCode:", r.get("statusCode"))
for event in r.get("stream") or []:
    chunk = event.get("chunk", {})
    delta = chunk.get("contentDelta", {})
    if "stdout" in delta:
        sys.stdout.write(delta["stdout"])
    if "stderr" in delta:
        sys.stdout.write("[stderr] " + delta["stderr"])
    if "contentStop" in chunk:
        print("\n[exit]", chunk["contentStop"])
