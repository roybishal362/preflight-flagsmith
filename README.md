# Preflight

**Catch the silent ways a Flagsmith experiment records nothing, before you launch it, and get an honest verdict after.**

Built for *Toggle Wars: The Flagsmith Experimentation Hackathon* (WeMakeDevs × Flagsmith, Nov 26–29 2026).

Live demo page (running a real Flagsmith experiment): **https://preflight-flagsmith.vercel.app**

---

## The problem

Flagsmith Experimentation is powerful, but an experiment can be set up "correctly" in the dashboard and still collect
**zero data**. Flagsmith's own docs list the causes, and none of them throw an error:

| Silent failure (from Flagsmith's docs) | What happens |
|---|---|
| SDK initialised without `enableEvents` / `enable_events` | No exposures, no conversions |
| Experiment flag read before the user is identified | No exposures |
| Tracked event name differs from the metric's event (case-sensitive) | Conversions silently ignored |
| Short-lived process exits before batched events flush | Events lost |
| SDK older than the Experimentation minimum | No experiment APIs |
| A variation has no **key** | No exposures for that variation |

You find out days later, when the results page still says "collecting data".

We hit one of these ourselves while building this project: the variation we created in the dashboard had **no key**.
Launching it would have recorded nothing for half the traffic. Preflight's `check` now catches exactly that.

## What Preflight does

```
preflight lint   [PATH] --event NAME   static checks on your code (no account needed)
preflight check  [PATH]                code + Flagsmith platform checks (warehouse, metrics, variant keys, allocations)
preflight smoke  --flag F --event E    drive synthetic users through the real SDK, after a human confirms
preflight verdict EXPERIMENT_ID        honest go / no-go using Flagsmith's own thresholds
```

| Rule | Checks |
|---|---|
| PF001 | Experiment SDK calls exist but events are never enabled |
| PF002 | Experiment flag read without an identity (JS `identify`, Python `identifier=`) |
| PF003 | Tracked event names vs. metric events: case mismatch (error), unknown event (warning), metric never tracked (error) |
| PF004 | Events enabled in a short-lived script/server file that never flushes or closes |
| PF005 | `@flagsmith/flagsmith` < 12.4.0, `@flagsmith/nodejs` < 9.1.0, legacy `flagsmith-nodejs`, Python `flagsmith` < 6.3.0 |
| PF101 | Environment has no (or a broken) warehouse connection |
| PF102 | A variation has no key, or the flag isn't multivariate |
| PF103 | Experiment exists but isn't running |
| PF104 | A variation gets 0% of experiment traffic |

**Verdict gates** (from Flagsmith's statistics docs): no call before every arm has ≥ 50 identities and ≥ 5 conversions;
a winner needs > 95% chance to beat control **and** a credible interval clear of zero; sample-ratio mismatch (p < 0.001)
overrides everything.

**Guardrails:** `smoke` refuses to run against the environment of your live experiment (synthetic users would pollute
real results) and asks a human before sending anything; every action is appended to `.preflight/audit.jsonl`;
the Organisation API key is read from a git-ignored `.env`, never from the command line.

## How Flagsmith is used

```
                    ┌────────────────────────── Flagsmith Cloud ───────────────────────────┐
 Visitor ──► Landing page (React, @flagsmith/flagsmith 12.6)                               │
   │   identify(visitorId) ──► getExperimentFlag('show_demo_button') ──► exposure event ──►│ Warehouse
   │   click ──► trackEvent('demo_button_clicked') ──► flushEvents() ─────────────────────►│   │
   │                                                                                       │   ▼
   │                                              Experiment #692 (multivariate flag,      │ Bayesian
   │                                              metric, 50/50 split) ◄───────────────────┤ results
   │                                                                                       │
 Developer ──► preflight lint  (reads code)                                                │
           ──► preflight check (Admin API: warehouse-connections, experiment-metrics,      │
           │                    experiments) ◄─────────────────────────────────────────────┤
           ──► preflight smoke (Python SDK 6.7, synthetic identities, test environment) ──►│
           ──► preflight verdict (Admin API: results/refresh, results) ◄───────────────────┘
```

- **Feature flags:** `show_demo_button` is a multivariate flag (`control` / `variant_b`) that decides the button text.
- **Experimentation:** experiment #692 tests the hypothesis below with a real metric from real events.
- **Admin API:** Preflight reads warehouse connections, metrics, experiments and results, and triggers result refreshes.
- **SDKs:** JavaScript SDK in the page; Python SDK in `preflight smoke`.

## The real experiment (Track 1)

- **Hypothesis:** A clearer CTA label ("Try the live demo now") increases demo button clicks compared with "Get the demo".
- **Flag:** `show_demo_button`. Control `control` ("Get the demo"), variation `variant_b` ("Try the live demo now").
- **Metric:** `Demo button click`, event `demo_button_clicked`, occurrence, higher is better.
- **Split:** 100% of visitors enter, 50/50. Visitors are identified by a random ID kept in `localStorage`.
- **Data hygiene:** two identities in the data are the builder's own test clicks (one local, one on the live page,
  both 2026-11-26, both landed in control). They are disclosed here and are far below the 50-per-arm gate.
- **Result:** run `preflight verdict 692`. It is reported here only once the gates are met. *(Pending, see Status.)*

## Quick start

```bash
pip install -e .
preflight lint path/to/your/app --event your_metric_event

cp .env.example .env    # then fill FLAGSMITH_ENVIRONMENT_KEY and FLAGSMITH_API_KEY (Organisation API key)
preflight check path/to/your/app
preflight verdict <experiment id> --refresh
```

Run the demo page locally: `cd web && cp .env.example .env` (add your client-side environment key), then
`npm install && npm run dev`.

## Claims and evidence

| Claim | Evidence |
|---|---|
| Lint catches each documented failure mode | `tests/test_lint_and_gates.py`, one test per rule |
| Platform checks work on Flagsmith's real payload shape | `tests/test_checks.py`, payloads captured from experiment #692 |
| Verdict follows Flagsmith's thresholds; the WeMakeDevs blog example (84%, CI −14%..+44%) is "inconclusive" | `tests/test_lint_and_gates.py::test_blog_example_is_inconclusive` |
| The live page sends exposures and conversions that reach Flagsmith | Results payload for #692 showed `control: n=1, sum=1` after one test click (2026-11-26) |
| `preflight lint web` on our own page reports no problems | run it |

Run all tests: `python -m pytest -q`

## Status

Built during the hackathon window (first commit 2026-11-26 21:40 IST). Verified against live Flagsmith: lint, check, verdict, the demo page and experiment #692.
`smoke` is implemented but not yet run against a live test environment. In progress: collecting real visitors
for the experiment result.

## License

MIT
