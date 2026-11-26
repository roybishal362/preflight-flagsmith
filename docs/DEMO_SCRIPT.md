# Demo video script (target 2:50, hard limit 3:00)

The judges score the video on: the problem, what was built, and how Flagsmith fits in.
Record at 1920×1080 with a terminal font of 18pt or larger. Talk slowly. Every command below has already been run
live; the outputs you will see are real.

## Before recording (15 min)

1. `cd e:\falgsmith\preflight`, activate `.venv`, run `cls`.
2. `docker compose -f selfhost/docker-compose.yml ps` should show 4 healthy services. If not: `preflight selfhost up`.
3. `python scripts/make_broken_demo.py` creates `demo_broken/`.
4. Open browser tabs: (1) https://preflight-flagsmith.vercel.app (2) Flagsmith dashboard, Experiments
   (3) the GitHub README (4) `docs/agent-transcript.md` on GitHub.
5. Run every command once off-camera so nothing is slow on camera.

## 0:00–0:25 The problem

**Show:** the live page, then the Flagsmith experiment page.

**Say:** "This page runs a real Flagsmith experiment. Flagsmith Experimentation is powerful, but an experiment can
look perfect in this dashboard and still record nothing: events not enabled, a user never identified, an event name
off by one capital letter, a variation without a key. None of these raise an error. While building this we hit four
of them ourselves. So we built Preflight."

## 0:25–0:55 Round 1 and 2: plan safely, check the code

```
preflight lint demo_broken --event demo_button_clicked
preflight plan --name "Second test" --flag show_demo_button --variant variant_c --event Demo_Button_Clicked --hypothesis "A shorter label increases demo button clicks" --code web
```

**Say:** "Lint reads the code: events never enabled, a flag read before identify, a wrong-case event. Each would kill
the experiment silently. Plan is read-only. Against live Flagsmith it blocks this experiment for three reasons and
sends nothing. When a plan is clean, apply re-checks Flagsmith, asks me, and logs every call."

## 0:55–1:30 Round 3: prove the data arrives

```
preflight --env-file selfhost/.env.local smoke --flag preflight_smoke_test --event smoke_converted -n 60 --yes
set FLAGSMITH_SDK_EVENTS_URL=https://events.api.flagsmith.com/
preflight --env-file selfhost/.env.local smoke --flag preflight_smoke_test --event smoke_converted -n 30 --yes --verify-timeout 30
set FLAGSMITH_SDK_EVENTS_URL=
```

**Say:** "Smoke sends synthetic users through the real SDK into a test environment and proves this run's events
reached the warehouse. This is a self-hosted Flagsmith. In the second run the events go to Flagsmith Cloud, the SDK
default. The flags still work, so nobody notices, but nothing is recorded. Preflight catches it: rule PF006. Our own
browser fix for this was silently ignored, because the JavaScript SDK only reads the events URL inside
eventProcessorConfig. That is now a check too."

## 1:30–2:05 Round 4: an honest verdict, then ship

```
preflight verdict 692
preflight --env-file selfhost/.env.local ship 1 --environment <self-host Production key> --yes
```

Then show the ship output for experiment #2 from the README ("Live evidence").

**Say:** "Verdict uses Flagsmith's own Bayesian results and thresholds. Our live experiment: not enough data, so don't
call it. Ship refuses an inconclusive result. On our rehearsal, variant B won at 99.5% with an interval clear of zero.
Ship rolled it out on the same flag, then checked what real users get. Twenty of twenty new users got the winner.
And it found that unidentified requests still get the old value, which is easy to miss."

## 2:05–2:35 Track 2: an AI agent with a human in the loop

**Show:** `docs/agent-transcript.md`, then `python scripts/mcp_e2e.py` (12 PASS).

**Say:** "Preflight is also an MCP server. Here Claude Code turned a one-line request into a plan, found all three
blockers against live Flagsmith and my code, and changed nothing. Apply and ship are destructive tools: the client
asks me first, plans are bound to a fingerprint, and an agent can never override the statistics."

## 2:35–2:50 Track 3: self-hosted, reusable

**Show:** `docker compose ps` in `selfhost/`, then the GitHub Action in the README.

**Say:** "One command gives you open-source Flagsmith with Experimentation, bootstrapped through the API, and the same
checks run in CI as a GitHub Action. Preflight: so your experiments actually measure something."

## After recording

- Trim pauses; keep it under 3:00.
- Upload as Unlisted on YouTube (unless the organisers require something else).
- Paste the link into the README's top table and into the submission.
