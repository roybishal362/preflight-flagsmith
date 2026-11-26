# Submission text (copy into the hackathon form)

Repo: https://github.com/roybishal362/preflight-flagsmith
Live page: https://preflight-flagsmith.vercel.app
Demo video: <paste the YouTube link after recording>

## One-liner

Preflight catches the silent ways a Flagsmith experiment records nothing, proves the data arrives, refuses to call a
winner too early, and ships the winner safely, from the CLI, any AI agent over MCP, CI, or a self-hosted Flagsmith.

## Description (about 200 words)

Flagsmith Experimentation can look perfect in the dashboard and still record nothing: events not enabled, users never
identified, an event name with the wrong case, a variation without a key, or, on self-hosted Flagsmith, events quietly
sent to Flagsmith Cloud. None of these raise an error. We hit four of them while building this.

Preflight covers all four rounds of an experiment. `plan`/`apply` create flag, variations, metric and experiment
through the Admin API, after a read-only plan a human approves. `lint` and `check` find the failure modes in code and
in live warehouse data. `smoke` proves each run's events reached the warehouse. `verdict` applies Flagsmith's own
thresholds. `ship` completes the experiment with the decision the evidence supports and checks what users get.

Every feature was verified live on Flagsmith Cloud and a self-hosted instance, with exact event counts. Preflight is
also an MCP server (a real Claude Code session used it safely), a GitHub Action, and a one-command self-hosted
Flagsmith kit. It found six things worth fixing upstream, including a JS SDK option that is silently ignored.

## If the form asks for a track

- **Track 2 (AI agents):** an MCP server with 7 tools; read-only planning; destructive tools bound to a fingerprinted,
  human-approved plan; agents cannot override the statistical gates. Evidence: `docs/agent-transcript.md`,
  `scripts/mcp_e2e.py` (12 live checks).
- **Track 3 (open-source Flagsmith):** `preflight selfhost up` runs and bootstraps open-source Flagsmith with
  Experimentation; the full plan → smoke → verdict → ship flow and a real browser click were verified on it.
  Integrations: MCP server, GitHub Action, CLI.
- **Track 1 (real A/B test):** experiment #692 on the live page; result reported only once the gates are met.

## Tech

Python 3.12 (Flagsmith Python SDK 6.7, requests, MCP SDK 2.3), React + TypeScript (Vite, `@flagsmith/flagsmith`
12.6), Docker Compose, Vercel, GitHub Actions. 37 unit tests plus a live MCP end-to-end test.
