"""Run a shell command inside the deployed AgentCore runtime and print output.

Usage: uv run python ../scripts/tests/runtime_diag.py "sh -c 'ls /var/task'"
"""
import json
import pathlib
import sys
import uuid

import boto3

# The runtime ARN carries a suffix that changes with every deployment, so it is
# read from the AgentCore config instead of being hardcoded.
_CFG = pathlib.Path(__file__).resolve().parents[2] / "starter" / ".bedrock_agentcore.yaml"
RUNTIME_ARN = None
for line in _CFG.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if stripped.startswith("agent_arn:") and stripped.split(":", 1)[1].strip() not in ("", "null"):
        RUNTIME_ARN = stripped.split(":", 1)[1].strip()
if not RUNTIME_ARN:
    raise SystemExit(f"no agent_arn in {_CFG} — deploy the agent first")
print(f"runtime: {RUNTIME_ARN}")

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
