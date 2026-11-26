# Preflight

**Your Flagsmith A/B test can look perfect in the dashboard and still record nothing.
Preflight catches that before launch, sets experiments up safely (for you or your AI agent), and refuses to call a
winner too early.**

Built for *Toggle Wars: The Flagsmith Experimentation Hackathon* (WeMakeDevs × Flagsmith, Nov 26–29 2026).

- Live page running a real Flagsmith experiment: **https://preflight-flagsmith.vercel.app**
- CLI: `preflight lint | check | plan | apply | smoke | verdict | selfhost`
- GitHub Action: fail a PR that would make an experiment record nothing
- Self-hosted Flagsmith with Experimentation in one command: [`selfhost/`](selfhost/README.md)
- MCP server: the same tools for Claude Code, Cursor or any MCP agent, with human approval on every change

---

## The problem

Flagsmith Experimentation records exposures and conversions from your app and runs a Bayesian engine on them. The
setup has several failure modes that **raise no error**. Flagsmith's own docs list them:

| Silent failure | What happens |
|---|---|
| SDK initialised without `enableEvents` / `enable_events` | No exposures, no conversions |
| Experiment flag read before the user is identified | No exposures |
| Tracked event name differs from the metric's event, even only in case | Conversions silently ignored |
| A short-lived process exits before batched events flush | Events lost |
| SDK older than the Experimentation minimum | No experiment APIs |
| A variation has no **key** | No exposures for that variation |

You find out days later, when the results page still says "collecting data".

**This happened to us while building this project.** The variation we created in the dashboard had no key. Launching
it would have recorded nothing for half the traffic. Every failure mode in the table is now a Preflight check.

## What Preflight does

| Command | What it does | Changes Flagsmith? |
|---|---|---|
| `preflight lint PATH --event E` | Scans JS/TS/Python code for the failure modes above | No |
| `preflight check PATH` | Checks the environment (warehouse, metrics, experiments, variation keys, allocations) and cross-checks your code against the metric events | No |
| `preflight plan …` | Validates a new experiment and prints the **exact API calls** it would make, with a fingerprint | No |
| `preflight apply PLAN --start` | Re-reads Flagsmith, refuses if anything changed since the plan, asks a human, applies, audits | Yes, after approval |
| `preflight smoke --flag F --event E` | Sends synthetic users through the real SDK to a **test** environment and proves every arm is served | Sends test events, after approval |
| `preflight verdict ID --refresh` | Go / no-go using Flagsmith's own thresholds | No |

### Checks

| Rule | Catches |
|---|---|
| PF001 | Experiment SDK calls, but events never enabled |
| PF002 | Experiment flag read without an identity |
| PF003 | Event names vs. metric events: case mismatch, unknown event, metric never tracked (constants are resolved) |
| PF004 | Events enabled in a short-lived script that never flushes or closes |
| PF005 | `@flagsmith/flagsmith` < 12.4.0, `@flagsmith/nodejs` < 9.1.0, legacy `flagsmith-nodejs`, Python `flagsmith` < 6.3.0 |
| PF006 | Flags come from a self-hosted API but events still go to Flagsmith Cloud (both SDKs default the events URL to `events.api.flagsmith.com`) |
| PF101 | No warehouse connection, or a broken one |
| PF102 | A variation without a key; a flag that isn't multivariate |
| PF103 | Experiment created but not running |
| PF104 | A variation with 0% of traffic |

`plan` also **blocks**: a second active experiment on the same flag (Flagsmith allows one), an event name that clashes
in case with an existing metric, existing keyless variations, splits that leave control nothing, and app code that
never sends the metric's event.

### Verdict gates (from Flagsmith's statistics docs)

- No call until every arm has **≥ 50 identities and ≥ 5 conversions**.
- A winner needs **> 95% chance to beat control and** a 95% credible interval clear of zero.
- Every treatment below 5% means control wins.
- A sample-ratio mismatch (p < 0.001, Flagsmith's or ours) overrides everything.

The WeMakeDevs tutorial's example (84% chance to win, interval −14%..+44%) comes out **inconclusive**, not a winner.

## Safety

- **Human in the loop.** `apply` and `smoke` ask before changing anything. They refuse in a non-interactive shell
  unless `--yes` is passed.
- **No stale approvals.** A plan has a fingerprint of its inputs and calls. `apply` rebuilds it from live Flagsmith
  state and stops if it no longer matches.
- **Partial failure is safe.** `apply` stops at the first failed call and lists what was already created. Planning is
  idempotent, so re-running `plan` resumes from where it stopped. This was tested live (see below).
- **No pollution.** `smoke` refuses to run against the environment of your live experiment. Synthetic identities
  are prefixed `preflight_smoke_<run>_`.
- **Audit log.** Every plan, apply step, smoke run and verdict is appended to `.preflight/audit.jsonl`.
- **Secrets.** The Organisation API key lives in a git-ignored `.env`, is never printed, and never appears in the
  plan output.

## For AI agents (MCP)

Flagsmith's MCP server can create experiments, but it cannot verify that they will record data, and it has no tool to
start them. Preflight's MCP server gives an agent the safe path:

```
preflight_lint     read-only   scan code for silent failures
preflight_check    read-only   environment + code health
preflight_plan     read-only   validate and preview exact changes, returns a fingerprint
preflight_apply    destructive applies only a fingerprinted plan the user approved; refuses if Flagsmith changed
preflight_verdict  read-only   honest go / no-go
```

`apply` is annotated `destructiveHint`, so MCP clients ask the human before each call. `smoke` is deliberately not
exposed to agents.

```bash
pip install -e ".[mcp]"
claude mcp add preflight -- python -m preflight.mcp_server   # Claude Code; run from this folder
```

Then, for example: *"Set up an experiment on `checkout_button` testing 'Pay now' vs 'Buy', measured by
`checkout_completed`, and check my code in ./app first."* The agent calls `preflight_plan`, shows the steps, and
waits for your OK before `preflight_apply`.

## In CI (GitHub Action)

```yaml
- uses: roybishal362/preflight-flagsmith@main
  with:
    path: web
    events: demo_button_clicked,signup_completed
```

This repo's own CI runs it on the demo page on every push.

## Self-hosted Flagsmith

`preflight selfhost up` starts open-source Flagsmith with Experimentation in Docker and bootstraps it through the API
(admin, organisation, project, environments, warehouse, API key), idempotently. Every Preflight command then works
against it with `--env-file selfhost/.env.local`. Details and verified results: [`selfhost/README.md`](selfhost/README.md).

## How Flagsmith is used

```
                    ┌──────────────────────────── Flagsmith Cloud ────────────────────────────┐
 Visitor ─► Landing page (React, @flagsmith/flagsmith 12.6)                                    │
   │  identify(visitorId) ─► getExperimentFlag('show_demo_button') ─► exposure ───────────────►│ Warehouse
   │  click ─► trackEvent('demo_button_clicked') ─► flushEvents() ────────────────────────────►│    │
   │                                                                                           │    ▼
   │                                  Experiment #692: multivariate flag, metric, 50/50 ◄──────┤ Bayesian
   │                                                                                           │ results
 Developer or AI agent (CLI / MCP)                                                             │
   ├─ lint     reads code                                                                      │
   ├─ check    GET warehouse-connections, experiment-metrics, experiments ◄────────────────────┤
   ├─ plan     GET environment, features, mv-options, metrics, experiments ◄───────────────────┤
   ├─ apply    POST features, mv-options, experiment-metrics, experiments, start ─────────────►│
   ├─ smoke    Python SDK 6.7: get_experiment_flag, track_event (test environment) ───────────►│
   └─ verdict  POST results/refresh, GET results ◄─────────────────────────────────────────────┘
```

Flagsmith features used: multivariate feature flags with keyed variations, environments, Experimentation (metrics,
experiments, rollout and split, start, Bayesian results with lift, credible interval and chance to win, SRM), the
managed warehouse, the Admin API, and the JavaScript and Python SDKs with events.

## Live evidence (Flagsmith Cloud, 2026-11-26)

| What | Result |
|---|---|
| Live page sends exposures and conversions | Two test clicks (local and production) appear in experiment #692 as `control: n=2, sum=2` |
| `plan` blocks unsafe setups against real data | A duplicate experiment on `show_demo_button` with a case-mismatched event was blocked for 3 reasons, nothing was sent |
| `apply` creates and starts an experiment | Created flag `preflight_smoke_test`, variation `variant_b`, metric #701, experiment #697, and started it |
| Partial failure and resume | The first apply stopped at step 2 (HTTP 400: Flagsmith also wants `feature` in the variation body, which the docs don't say). We fixed it; re-running `plan` skipped the existing flag and finished |
| `smoke` proves bucketing and ingestion | 120 synthetic identities: 63 control / 57 variant_b served, 37 conversions sent. Flagsmith reported exactly `n=63, n=57` and `24 + 13 = 37` conversions |
| `verdict` reads Flagsmith's inference | #697: chance to win 1.1%, lift CI [−74.7%, −5.6%] → "Control wins". #692: "Not enough data yet" |
| MCP over stdio | `python scripts/mcp_e2e.py`: 8/8 checks pass against live Flagsmith |

The smoke experiment uses a deterministic synthetic conversion pattern, so its "result" demonstrates the pipeline,
not a real effect.

## The real experiment (Track 1)

- **Hypothesis:** A clearer CTA label ("Try the live demo now") increases demo button clicks compared with "Get the demo".
- **Flag:** `show_demo_button`. Control `control` ("Get the demo"), variation `variant_b` ("Try the live demo now").
- **Metric:** `Demo button click`, event `demo_button_clicked`, occurrence, higher is better.
- **Split:** 100% of visitors enter, 50/50. Each visitor is identified by a random ID kept in `localStorage`; no personal data.
- **Data hygiene:** two identities are the builder's own test clicks (one local, one on the live page, both in
  control). They are disclosed here and are far below the 50-per-arm gate.
- **Result:** `preflight verdict 692`. Reported here only once the gates are met.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate        # Windows; use bin/activate elsewhere
pip install -e ".[mcp,dev]"

preflight lint path/to/app --event your_metric_event   # no account needed

cp .env.example .env    # FLAGSMITH_ENVIRONMENT_KEY, FLAGSMITH_API_KEY (Organisation API key),
                        # FLAGSMITH_SMOKE_ENVIRONMENT_KEY (a separate test environment)
preflight check path/to/app
preflight plan --name "Checkout copy" --flag checkout_button --variant pay_now="Pay now" \
               --event checkout_completed --hypothesis "Shorter copy increases completed checkouts" --code path/to/app
preflight apply .preflight/plans/<fingerprint>.json --start
preflight verdict <experiment id> --refresh
```

Demo page: `cd web && cp .env.example .env` (client-side environment key), then `npm install && npm run dev`.

## Tests

```bash
python -m pytest -q          # 25 unit tests; platform tests use payloads captured from live Flagsmith
python scripts/mcp_e2e.py    # MCP protocol end-to-end against live Flagsmith (read-only)
```

## Limitations

- `lint` uses patterns, not a full parser. It resolves simple constants and ignores strings and comments, but it can
  miss calls built dynamically (for example an event name assembled at runtime) or wrapped in your own helper.
- `check` and `verdict` rely on Experimentation API shapes captured from Flagsmith Cloud and self-hosted on
  2026-11-26. Experimentation is in beta, so these may change; the tests pin the shapes we saw.
- `plan` handles string-valued variations and one primary metric.
- The self-hosted kit uses the Flagsmith-database warehouse, which Flagsmith positions for evaluation, not production volume.

## Status

Built during the hackathon window (first commit 2026-11-26 21:40 IST). Collecting real visitors for experiment #692.

## License

MIT
