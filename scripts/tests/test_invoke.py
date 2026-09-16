"""Local invoke harness: run one agent turn against the real resources.

Usage: uv run python test_invoke.py '<json payload>'
"""
import asyncio
import json
import sys

sys.path.insert(0, ".")

import main as m  # noqa: E402


async def run():
    payload = json.loads(sys.argv[1])
    reply = await m.invoke(payload)
    print("=" * 70)
    print(reply)
    print("=" * 70)


asyncio.run(run())
