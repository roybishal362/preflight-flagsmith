# Demo video script (target 2:45, hard limit 3:00)

Judging asks the video to show: the problem, what was built, and how Flagsmith fits in.
Record at 1920×1080, terminal font 18pt+, dark theme. Speak slowly; captions are fine.

## Before recording (10 min)

1. Open these, each in its own tab or window, in this order:
   - Tab 1: https://preflight-flagsmith.vercel.app
   - Tab 2: Flagsmith dashboard → Experiments (Development) → "Demo button text test"
   - Tab 3: GitHub repo README
   - Terminal in `e:\falgsmith\preflight` with `.venv` active, cleared (`cls`)
2. Make the broken demo app (shown in scene 2): `python scripts/make_broken_demo.py` creates `demo_broken/`.
3. Run each command once off-camera so nothing is slow on camera.

## Scene 1 — The problem (0:00–0:30)

**Show:** the live page (Tab 1), then the Flagsmith experiment page (Tab 2).

**Say:**
"This page is running a real Flagsmith experiment right now: two button labels, 50/50, and a click is the
conversion. Flagsmith Experimentation is great, but an experiment can look perfect in this dashboard and still record
nothing. Events not enabled, a user never identified, an event name off by one capital letter, a variation without a
key. None of these throw an error. You find out days later. While building this, we hit one ourselves."

## Scene 2 — Catch it before launch (0:30–1:05)

**Show:** terminal.

```
preflight lint demo_broken --event demo_button_clicked
```

**Say:** "Preflight reads your code. Here it finds events never enabled, the flag read before identify, and an event
named with the wrong case. Each would have silently killed the experiment."

```
preflight lint web --event demo_button_clicked
```

**Say:** "Our real page: clean."

## Scene 3 — Set up an experiment safely (1:05–1:45)

**Show:** terminal.

```
preflight plan --name "Second test" --flag show_demo_button --variant variant_c --event Demo_Button_Clicked --hypothesis "A shorter label increases demo button clicks" --code web
```

**Say:** "Planning is read-only. Against the live Flagsmith project it blocks this for three reasons: the flag already
has a running experiment, Flagsmith only allows one, the event name clashes in case with an existing metric, and our
code doesn't send that spelling. Nothing was sent."

Then show the README "Live evidence" row about `apply` and say:
"When a plan is clean, apply re-reads Flagsmith, refuses if anything changed since you reviewed it, asks you, and
logs every call. We used it to create and start a real experiment; when Flagsmith's API rejected a call the docs
don't mention, it stopped, told us exactly what existed, and resumed cleanly."

## Scene 4 — Proof the data arrives, and an honest verdict (1:45–2:20)

```
preflight verdict 697 --environment <production key>
preflight verdict 692
```

**Say:** "Smoke test: 120 synthetic users through the real SDK into a separate environment. Flagsmith counted exactly
the 63 and 57 users and 37 conversions we sent. The verdict uses Flagsmith's own Bayesian results and thresholds:
here, control wins with a credible interval clear of zero. On the real experiment it says: not enough data yet,
don't call a winner. It will never crown a winner from 84% and an interval crossing zero."

## Scene 5 — AI agents and self-hosting (2:20–2:45)

**Show:** `python scripts/mcp_e2e.py` output (8 PASS), then `docker compose ps` in `selfhost/`.

**Say:** "The same tools are an MCP server, so Claude Code or Cursor can set up experiments, but every change is a
plan you approve, bound to a fingerprint. And `preflight selfhost up` gives you a self-hosted Flagsmith with
Experimentation in one command. It caught one more silent failure: on self-hosted Flagsmith, both SDKs still send
events to Flagsmith Cloud unless you set the events URL. That's rule PF006. Preflight: so your experiments actually
measure something."

## After recording

- Trim dead time; keep under 3:00.
- Upload to YouTube as **Unlisted** (or as the hackathon requires); paste the link into the README and the submission.
