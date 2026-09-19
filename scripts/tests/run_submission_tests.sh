#!/usr/bin/env bash
# Run the six submission tests against the deployed agent and save evidence.
#
# Each evidence file now contains three things:
#   1. the command that was run
#   2. the raw `agentcore invoke` output (the customer-facing answer)
#   3. the tool invocations the runtime logged for that turn, read from
#      CloudWatch — the proof the rubric asks for that the Gateway-backed
#      tools were really called and returned well-formed data.
set -u
export PATH="$HOME/bin:$PATH"
export AGENTCORE_SUPPRESS_RECOMMENDATION=1
cd "$(dirname "$0")/../../starter" || exit 1
EVID=../evidence
mkdir -p "$EVID"
AWS="/c/Program Files/Amazon/AWSCLIV2/aws.exe"

# The runtime log group carries a suffix that changes with every deployment.
LOG_GROUP=$("$AWS" logs describe-log-groups \
  --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/" \
  --query "logGroups[?contains(logGroupName,'customer_support_agent')].logGroupName" \
  --output text 2>/dev/null | tr '\t' '\n' | grep -- '-DEFAULT' | tail -1)
echo "runtime log group: ${LOG_GROUP:-<none found>}"

tool_calls() {
  [ -z "${LOG_GROUP:-}" ] && return
  # Short window so each test's log shows only that test's own tool calls.
  local since=$(( ( $(date +%s) - 90 ) * 1000 ))
  "$AWS" logs filter-log-events \
    --log-group-name "$LOG_GROUP" \
    --start-time "$since" \
    --filter-pattern '"Tool #"' \
    --query "events[].message" \
    --output text 2>/dev/null | tr '\t' '\n' | grep -i "Tool #" | tail -8
}

run() {
  local name="$1" payload="$2"
  {
    echo "### $name"
    echo "COMMAND: agentcore invoke '$payload'"
    echo
    uv run agentcore invoke "$payload" 2>&1
    echo
    echo "--- tool calls logged by the runtime (CloudWatch) ---"
    sleep 10   # let the log stream flush
    tool_calls
    echo
  } > "$EVID/$name.txt" 2>&1
  echo "---------- $name ----------"
  sed -n '/^Response:/,$p' "$EVID/$name.txt" | head -20
  echo "   tools: $(tool_calls | tr '\n' ' ')"
}

run test1-order-tracking '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-123", "session_id": "t1"}'
run test2-refund '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "customer_id": "CUST-123", "session_id": "t2"}'
run test3-knowledge-base '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'
run test5-loyalty-discount '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "customer_id": "CUST-123", "session_id": "t5"}'
run test4a-memory-session-a '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-123", "session_id": "s-A"}'
echo "waiting 150s for memory extraction..."
sleep 150
run test4b-memory-session-b '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-123", "session_id": "s-B"}'
run test6-browser '{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": "CUST-123", "session_id": "t6"}'
echo "ALL DONE"
