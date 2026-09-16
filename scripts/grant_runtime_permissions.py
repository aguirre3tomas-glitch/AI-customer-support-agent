"""Grant the AgentCore Runtime execution role the permissions main.py needs.

The toolkit's auto-created role only covers logs/xray/model/code-interpreter.
The agent also uses:
  - AgentCore Memory   (control plane get/list + data plane events & records)
  - AgentCore Browser  (aws.browser.v1 sessions)
  - Bedrock Knowledge Bases (Retrieve)
Attached as a SEPARATE inline policy so toolkit redeploys (which rewrite their
own BedrockAgentCoreRuntimeExecutionPolicy-* policy) cannot drop it.

Run:  uv run python ../scripts/grant_runtime_permissions.py
"""
import json

import boto3

REGION = "us-east-1"
ACCT = "032189013563"
ROLE = "AmazonBedrockAgentCoreSDKRuntime-us-east-1-aa96c3a063"
POLICY_NAME = "CsaiAgentExtraPermissions"

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AgentCoreMemory",
            "Effect": "Allow",
            "Action": [
                # control plane
                "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:ListMemories",
                "bedrock-agentcore:GetMemoryStrategy",
                "bedrock-agentcore:ListMemoryStrategies",
                # data plane: events + records
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:GetEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:DeleteEvent",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord",
                "bedrock-agentcore:DeleteMemoryRecord",
                "bedrock-agentcore:BatchCreateMemoryRecords",
                "bedrock-agentcore:BatchUpdateMemoryRecords",
                "bedrock-agentcore:BatchDeleteMemoryRecords",
                "bedrock-agentcore:ListSessions",
                "bedrock-agentcore:ListActors",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{REGION}:{ACCT}:memory/*",
                f"arn:aws:bedrock-agentcore:{REGION}:{ACCT}:memory",
            ],
        },
        {
            "Sid": "AgentCoreBrowser",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:StartBrowserSession",
                "bedrock-agentcore:StopBrowserSession",
                "bedrock-agentcore:GetBrowserSession",
                "bedrock-agentcore:ListBrowserSessions",
                # without this the CDP websocket handshake is rejected with 403
                "bedrock-agentcore:ConnectBrowserAutomationStream",
                "bedrock-agentcore:ConnectBrowserLiveViewStream",
                "bedrock-agentcore:InvokeBrowser",
                "bedrock-agentcore:SaveBrowserSessionProfile",
                "bedrock-agentcore:UpdateBrowserStream",
                "bedrock-agentcore:GetBrowser",
                "bedrock-agentcore:ListBrowsers",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{REGION}:aws:browser/*",
                f"arn:aws:bedrock-agentcore:{REGION}:{ACCT}:browser/*",
            ],
        },
        {
            "Sid": "KnowledgeBaseRetrieve",
            "Effect": "Allow",
            "Action": [
                "bedrock:Retrieve",
                "bedrock:RetrieveAndGenerate",
            ],
            "Resource": [
                f"arn:aws:bedrock:{REGION}:{ACCT}:knowledge-base/*",
            ],
        },
    ],
}

iam = boto3.client("iam", region_name=REGION)
iam.put_role_policy(
    RoleName=ROLE,
    PolicyName=POLICY_NAME,
    PolicyDocument=json.dumps(policy),
)
print(f"inline policy '{POLICY_NAME}' attached to {ROLE}")

# verify
names = iam.list_role_policies(RoleName=ROLE)["PolicyNames"]
print("role policies:", names)
