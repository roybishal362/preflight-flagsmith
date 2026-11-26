"""End-to-end test of the Preflight MCP server over the real stdio protocol, against live Flagsmith.

Usage: python scripts/mcp_e2e.py
Exercises: list_tools, preflight_lint, preflight_check, preflight_plan (a plan that must be BLOCKED),
preflight_apply with a fake fingerprint (must refuse), preflight_verdict on experiment #692.
Nothing in Flagsmith is changed.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "preflight.mcp_server"])
    failures = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal failures
        print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
        failures += 0 if cond else 1

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("lists 7 tools", set(tools) == {"preflight_lint", "preflight_check", "preflight_plan", "preflight_apply",
                                                   "preflight_verdict", "preflight_ship_preview", "preflight_ship"},
                  ", ".join(sorted(tools)))
            check("ship is marked destructive", bool(tools["preflight_ship"].annotations.destructive_hint))
            ann = tools["preflight_apply"].annotations
            check("apply is marked destructive", bool(ann and ann.destructive_hint))
            check("plan is marked read-only", bool(tools["preflight_plan"].annotations.read_only_hint))

            r = payload(await s.call_tool("preflight_lint", {"path": "web", "metric_events": ["demo_button_clicked"]}))
            check("lint: live page is clean", r["ok"] and r["findings"] == [])

            r = payload(await s.call_tool("preflight_check", {"path": "web"}))
            check("check: live environment is healthy", r["ok"], json.dumps(r["context"]["experiments"]))

            r = payload(await s.call_tool("preflight_plan", {
                "name": "Agent duplicate", "hypothesis": "A clearer label increases demo button clicks a lot",
                "flag": "show_demo_button", "event": "demo_button_clicked", "variants": ["variant_b"],
                "code_path": "web"}))
            check("plan: blocks a second experiment on a running flag", not r["ok"]
                  and any("one active experiment" in p for p in r["problems"]), r["problems"][0][:70])

            r = payload(await s.call_tool("preflight_apply", {"fingerprint": "000000000000"}))
            check("apply: refuses an unknown fingerprint", r["applied"] is False, r["reason"][:60])

            r = payload(await s.call_tool("preflight_verdict", {"experiment_id": 692}))
            check("verdict: reads live experiment #692", r["experiment"]["id"] == 692,
                  f"{r['verdict']['status']}: {r['verdict']['headline']}")

            r = payload(await s.call_tool("preflight_ship_preview", {"experiment_id": 692}))
            check("ship_preview: nothing to ship yet on #692", r["allowed"] is False, r["reason"][:60])

            r = payload(await s.call_tool("preflight_ship", {"experiment_id": 692}))
            check("ship: refuses while the evidence is insufficient", r["shipped"] is False, r["reason"][:60])
    print(f"\n{'ALL PASSED' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
