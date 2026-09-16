"""Create a CloudWatch metric filter + alarm on the agent runtime's errors.

Log group: /aws/bedrock-agentcore/runtimes/<runtime>-DEFAULT
Filter:    lines containing ERROR  -> metric AgentRuntimeErrors
Alarm:     fires when more than 5 errors land in 5 minutes

Run: uv run python ../scripts/create_cloudwatch_alarm.py
"""
import json

import boto3

REGION = "us-east-1"
LOG_GROUP = "/aws/bedrock-agentcore/runtimes/customer_support_agent-hFhMtcHN2g-DEFAULT"
FILTER_NAME = "customer_support_agent_errors"
METRIC_NAMESPACE = "CsaiAgent"
METRIC_NAME = "AgentRuntimeErrors"
ALARM_NAME = "customer-support-agent-errors"

logs = boto3.client("logs", region_name=REGION)
cw = boto3.client("cloudwatch", region_name=REGION)

logs.put_metric_filter(
    logGroupName=LOG_GROUP,
    filterName=FILTER_NAME,
    filterPattern="ERROR",
    metricTransformations=[
        {
            "metricNamespace": METRIC_NAMESPACE,
            "metricName": METRIC_NAME,
            "metricValue": "1",
            "defaultValue": 0,
        }
    ],
)
print(f"metric filter '{FILTER_NAME}' on {LOG_GROUP}")

cw.put_metric_alarm(
    AlarmName=ALARM_NAME,
    AlarmDescription="Customer support agent: runtime errors in the last 5 minutes",
    Namespace=METRIC_NAMESPACE,
    MetricName=METRIC_NAME,
    Statistic="Sum",
    Period=300,
    EvaluationPeriods=1,
    Threshold=5,
    ComparisonOperator="GreaterThanThreshold",
    TreatMissingData="notBreaching",
)
print(f"alarm '{ALARM_NAME}': >5 errors / 5 min -> ALARM")

alarms = cw.describe_alarms(AlarmNames=[ALARM_NAME])["MetricAlarms"]
for a in alarms:
    print(json.dumps(
        {
            "AlarmName": a["AlarmName"],
            "StateValue": a["StateValue"],
            "MetricName": a["MetricName"],
            "Threshold": a["Threshold"],
            "ComparisonOperator": a["ComparisonOperator"],
            "Period": a["Period"],
            "Namespace": a["Namespace"],
        },
        indent=2,
    ))

filters = logs.describe_metric_filters(logGroupName=LOG_GROUP, filterNamePrefix=FILTER_NAME)
print("filters:", [f["filterName"] for f in filters.get("metricFilters", [])])
