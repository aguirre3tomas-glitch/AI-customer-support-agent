#!/usr/bin/env bash
# Run the six submission tests against the deployed agent and save evidence.
set -u
export PATH="$HOME/bin:$PATH"
export AGENTCORE_SUPPRESS_RECOMMENDATION=1
cd "$(dirname "$0")/../../starter" || exit 1
EVID=../evidence
mkdir -p "$EVID"

run() {
  local name="$1" payload="$2"
  {
    echo "### $name"
    echo "COMMAND: agentcore invoke '$payload'"
    echo
    uv run agentcore invoke "$payload" 2>&1
    echo
  } > "$EVID/$name.txt" 2>&1
  echo "---------- $name ----------"
  sed -n '/^Response:/,$p' "$EVID/$name.txt" | head -25
}

run test2-refund '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "customer_id": "CUST-123", "session_id": "t2"}'
run test3-knowledge-base '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'
run test5-loyalty-discount '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "customer_id": "CUST-123", "session_id": "t5"}'
run test4a-memory-session-a '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-123", "session_id": "s-A"}'
echo "waiting 50s for memory extraction..."
sleep 50
run test4b-memory-session-b '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-123", "session_id": "s-B"}'
run test6-browser '{"prompt": "Go to https://www.udacity.com and tell me the page title.", "customer_id": "CUST-123", "session_id": "t6"}'
echo "ALL DONE"
