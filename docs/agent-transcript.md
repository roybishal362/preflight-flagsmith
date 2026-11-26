# A real AI agent using Preflight over MCP

Recorded 2026-11-26 with Claude Code 2.1.330 in headless mode, against the live Flagsmith Cloud project.
The agent was allowed only the read-only tools; `preflight_apply` and `preflight_ship` were disallowed.

## Command

```bash
claude -p "I want to A/B test a new label on the demo button. Use the preflight MCP tools to plan an experiment on
flag show_demo_button with a new variant variant_c=Start now, measured by the event Demo_Button_Clicked, hypothesis
'A shorter label increases demo button clicks', and check my app code in ./web. Do NOT apply anything. Tell me in
5 short bullet points what the plan would change and whether it is safe." \
  --mcp-config .mcp.json --strict-mcp-config \
  --allowedTools "mcp__preflight__preflight_plan,mcp__preflight__preflight_check,mcp__preflight__preflight_lint,mcp__preflight__preflight_verdict" \
  --disallowedTools "mcp__preflight__preflight_apply,mcp__preflight__preflight_ship,Bash,Edit,Write"
```

## Agent's answer (verbatim)

- **Not safe to apply as-is.** The plan failed with 3 problems, and I applied nothing.
- **Blocker 1:** `show_demo_button` already has a running experiment, "Demo button text test" (#692). Flagsmith allows
  one active experiment per flag, so creating a second one would fail or collide. You'd need to stop #692 first, or
  add `variant_c` to it.
- **Blocker 2 (event case mismatch):** your code in `web/src/App.tsx:62` sends `demo_button_clicked`. The plan
  measures `Demo_Button_Clicked`, and the existing metric also counts the lowercase name. Flagsmith ignores events
  with the wrong case, so the new metric would record zero clicks. Fix this by picking one spelling, either in the
  code or in the plan.
- **What it would change if the blockers were fixed:** it would add the variation `variant_c` = "Start now" to the
  flag, which already has `variant_b`. It would create a new metric, "Demo button clicked". It would create the
  experiment `demo-button-label` at 100% traffic, split 50% `variant_c` and 50% control, in the Development
  environment.
- **Next step:** I can re-run the plan once you decide two things. First, whether to stop #692 or extend it. Second,
  which event spelling to use. If you pick the lowercase `demo_button_clicked`, the code needs no change.

## Audit log entry written by the server

```json
{"at": "2026-11-26T17:31:45+00:00", "action": "mcp.plan", "fingerprint": "a978478bbb41", "ok": false}
```

## What this shows

- The agent turned a one-line request into a full, validated experiment plan through Preflight's MCP tools.
- Preflight's checks ran against live Flagsmith (the running experiment, the existing metric) **and** the app code
  (`web/src/App.tsx:62`), and the agent relayed all three blockers with fixes.
- Nothing changed in Flagsmith. With `apply` allowed, the client would still ask the human before the call, and
  `apply` only accepts the fingerprint of a plan the human saw.
