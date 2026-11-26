"""Preflight: catch the silent ways a Flagsmith experiment records nothing.

  preflight lint   [PATH] [--event NAME ...]    static checks on your code
  preflight check  [PATH]                       code + Flagsmith platform checks (needs FLAGSMITH_API_KEY)
  preflight smoke  --flag F --event E           drive synthetic users through the real SDK (asks first)
  preflight verdict EXPERIMENT_ID [--refresh]   honest go / no-go on the results
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .gates import judge
from .lint import Finding, lint

AUDIT = Path(".preflight") / "audit.jsonl"
COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def audit(action: str, **detail) -> None:
    AUDIT.parent.mkdir(exist_ok=True)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action, **detail}
    with AUDIT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing to change anything without a human: re-run in a terminal, or pass --yes.")
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def print_findings(findings: list[Finding]) -> int:
    if not findings:
        print(c("✓ No problems found. This experiment can record data.", "32"))
        return 0
    errors = sum(f.severity == "error" for f in findings)
    for f in findings:
        tag = c("ERROR", "31;1") if f.severity == "error" else c("WARN ", "33;1")
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"{tag} {f.rule} {loc}\n      {f.message}\n      fix: {f.fix}")
    print(f"\n{errors} error(s), {len(findings) - errors} warning(s).")
    return 1 if errors else 0


def cmd_lint(a) -> int:
    events = set(a.event) if a.event else None
    findings = lint(Path(a.path), events)
    if a.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        return 1 if any(f.severity == "error" for f in findings) else 0
    return print_findings(findings)


def cmd_check(a) -> int:
    from .api import FlagsmithAdmin
    from .checks import check_environment

    env_key = a.environment or os.environ.get("FLAGSMITH_ENVIRONMENT_KEY")
    if not env_key:
        print("Set FLAGSMITH_ENVIRONMENT_KEY (or pass --environment)."); return 2
    api = FlagsmithAdmin()
    platform, ctx = check_environment(api, env_key)
    events = set(ctx["metrics"].values())
    code = lint(Path(a.path), events)
    audit("check", environment=env_key[:6], findings=len(platform) + len(code))
    if a.json:
        print(json.dumps({"context": ctx, "findings": [f.as_dict() for f in platform + code]}, indent=2))
        return 1 if any(f.severity == "error" for f in platform + code) else 0
    print(c("Flagsmith", "1"), f"warehouse connections: {ctx['warehouse_connections']}, "
          f"metrics: {ctx['metrics'] or 'none'}")
    for e in ctx["experiments"]:
        print(f"  experiment #{e['id']} '{e['name']}' status={e['status']}")
    print()
    return print_findings(platform + code)


def cmd_smoke(a) -> int:
    from flagsmith import Flagsmith

    env_key = a.environment or os.environ.get("FLAGSMITH_SMOKE_ENVIRONMENT_KEY")
    if not env_key:
        print("Pass --environment with the key of a TEST environment (not the one with your live experiment)."); return 2
    live = os.environ.get("FLAGSMITH_ENVIRONMENT_KEY")
    if live and env_key == live and not a.allow_live:
        print("That is your live experiment's environment. Synthetic users would pollute real results.\n"
              "Use a separate environment, or pass --allow-live if you really mean it."); return 2
    run = uuid.uuid4().hex[:6]
    plan = (f"Smoke test: {a.n} synthetic identities 'preflight_smoke_{run}_<i>' will read '{a.flag}' "
            f"and {a.rate:.0%} of them will send '{a.event}' to environment {env_key[:6]}….")
    print(plan)
    if not confirm("Send this test traffic?", a.yes):
        audit("smoke.declined", flag=a.flag); return 1
    from flagsmith.analytics import EventProcessorConfig

    api_url = os.environ.get("FLAGSMITH_SDK_API_URL") or None
    events_url = os.environ.get("FLAGSMITH_SDK_EVENTS_URL")
    if api_url and not events_url and "api.flagsmith.com" not in api_url:
        # self-hosted: events must go to the same host, not to Flagsmith Cloud (PF006)
        events_url = api_url.rstrip("/").removesuffix("/v1") + "/"
    kwargs = {"event_processor_config": EventProcessorConfig(events_api_url=events_url)} if events_url else {}
    if events_url:
        print(f"Events URL: {events_url}")
    fs = Flagsmith(environment_key=env_key, enable_events=True, api_url=api_url, **kwargs)
    seen: dict[str, int] = {}
    converted = 0
    t0 = time.time()
    for i in range(a.n):
        ident = f"preflight_smoke_{run}_{i}"
        flag = fs.get_experiment_flag(a.flag, identifier=ident)
        value = str(flag.value) if getattr(flag, "enabled", False) else "(disabled)"
        seen[value] = seen.get(value, 0) + 1
        if (i * 7919) % 100 < a.rate * 100:  # deterministic spread, no randomness in tests
            fs.track_event(a.event, identifier=ident)
            converted += 1
    fs.close()
    audit("smoke.sent", flag=a.flag, event=a.event, identities=a.n, conversions=converted, run=run, variants=seen)
    print(f"Sent in {time.time() - t0:.1f}s. Variants served: {seen}. Conversions sent: {converted}.")
    if len(seen) < 2:
        print(c("✗ Only one variant was served. The flag is not splitting traffic (experiment not running, "
                "weights at 0, or flag disabled).", "31;1")); return 1
    print(c("✓ Bucketing works. Now run `preflight verdict <id> --refresh` in ~1 minute to confirm Flagsmith "
            "ingested the exposures.", "32"))
    return 0


def cmd_verdict(a) -> int:
    from .api import FlagsmithAdmin
    from .checks import allocations_of, normalise_flagsmith_results

    env_key = a.environment or os.environ.get("FLAGSMITH_ENVIRONMENT_KEY")
    api = FlagsmithAdmin()
    if a.refresh:
        try:
            api.refresh_results(env_key, a.experiment_id)
            audit("results.refresh", experiment=a.experiment_id)
        except Exception as e:  # refresh is rate-limited to once per 5 minutes
            print(c(f"(refresh skipped: {e})", "2"))
    raw = api.results(env_key, a.experiment_id)
    if a.raw:
        print(json.dumps(raw, indent=2)); return 0
    exp = api.experiment(env_key, a.experiment_id)
    primary = next((m.get("metric") for m in exp.get("metrics") or []), None)
    arms, meta = normalise_flagsmith_results(raw, allocations_of(exp), primary)
    v = judge(arms)
    fs_srm = meta.get("flagsmith_srm_p_value")
    if fs_srm is not None and fs_srm < 0.001 and v.status != "srm":
        v.status, v.headline = "srm", "Flagsmith reports a sample-ratio mismatch. Do not trust these results."
    if a.json:
        print(json.dumps({"experiment": exp.get("name"), "meta": meta, "verdict": v.as_dict(),
                          "arms": [x.__dict__ for x in arms]}, indent=2)); return 0
    print(c(f"Experiment #{exp.get('id')} '{exp.get('name')}'", "1"), f"({exp.get('status')}, results as of {meta.get('as_of')})")
    if exp.get("hypothesis"):
        print(c(f"Hypothesis: {exp['hypothesis']}", "2"))
    for arm in arms:
        rate = f"{arm.conversions / arm.identities:.1%}" if arm.identities else "n/a"
        print(f"  {arm.name:<12} identities={arm.identities:<5} converted={arm.conversions or 0:<5} rate={rate}")
    colour = {"winner": "32;1", "loser": "33;1", "srm": "31;1"}.get(v.status, "36;1")
    print(c(v.headline, colour))
    for r in v.reasons:
        print("  -", r)
    audit("verdict", experiment=a.experiment_id, status=v.status)
    return 0


def _spec_from_args(a):
    from .planner import Spec, Variant

    if a.spec:
        return Spec.from_dict(json.loads(Path(a.spec).read_text(encoding="utf-8")))
    env_key = a.environment or os.environ.get("FLAGSMITH_ENVIRONMENT_KEY")
    missing = [n for n, v in (("--name", a.name), ("--flag", a.flag), ("--event", a.event),
                              ("--hypothesis", a.hypothesis), ("--variant", a.variant), ("environment", env_key)) if not v]
    if missing:
        raise SystemExit(f"Missing: {', '.join(missing)} (or pass --spec file.json)")
    variants = []
    for raw in a.variant:
        key, _, value = raw.partition("=")
        variants.append(Variant(key.strip(), (value or key).strip()))
    split = {}
    for raw in a.split or []:
        k, _, pct = raw.partition("=")
        split[k.strip()] = float(pct)
    return Spec(environment=env_key, name=a.name, hypothesis=a.hypothesis, flag=a.flag, variants=variants,
                event=a.event, metric_name=a.metric_name, control_value=a.control_value, traffic=a.traffic,
                split=split, code_path=a.code)


def _print_plan(plan) -> None:
    print(c(f"Plan {plan.fingerprint()}  ·  environment '{plan.existing.get('environment')}'", "1"))
    print(f"  Experiment : {plan.spec.name}\n  Hypothesis : {plan.spec.hypothesis}")
    print(f"  Flag       : {plan.spec.flag} (control='{plan.spec.control_value}', variations: "
          + ", ".join(f"{v.key}='{v.value}'" for v in plan.spec.variants) + ")")
    print(f"  Metric     : event '{plan.spec.event}' ({plan.spec.aggregation}, {plan.spec.direction})")
    if plan.existing:
        print(c(f"  Already in Flagsmith: {json.dumps({k: v for k, v in plan.existing.items() if k != 'environment'})}", "2"))
    print(c("\nChanges (nothing has been sent yet):", "1"))
    for i, s in enumerate(plan.steps, 1):
        print(f"  {i}. {s.summary}\n     {s.method} {s.path}")
    for w in plan.warnings:
        print(c("  WARN  ", "33;1") + w)
    for p in plan.problems:
        print(c("  BLOCK ", "31;1") + p)
    print(c("\n✓ Plan is safe to apply." if plan.ok else "\n✗ Fix the BLOCK items before applying.",
            "32;1" if plan.ok else "31;1"))


def cmd_plan(a) -> int:
    from .api import FlagsmithAdmin
    from .planner import build_plan

    spec = _spec_from_args(a)
    plan = build_plan(FlagsmithAdmin(), spec)
    out = Path(".preflight") / "plans" / f"{plan.fingerprint()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    audit("plan", fingerprint=plan.fingerprint(), ok=plan.ok, steps=len(plan.steps), problems=len(plan.problems))
    if a.json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        _print_plan(plan)
        if plan.ok:
            print(f"\nReview it, then: preflight apply {out}" + (" --start" if not a.json else ""))
    return 0 if plan.ok else 1


def cmd_apply(a) -> int:
    from .api import FlagsmithAdmin
    from .planner import Spec, apply_plan, build_plan

    saved = json.loads(Path(a.plan_file).read_text(encoding="utf-8"))
    api = FlagsmithAdmin()
    plan = build_plan(api, Spec.from_dict(saved["spec"]))  # re-read live state; never trust a stale plan
    if plan.fingerprint() != saved["fingerprint"]:
        print(c("Flagsmith changed since this plan was made. Here is the current plan:", "33;1"))
    _print_plan(plan)
    if not plan.ok:
        audit("apply.blocked", fingerprint=plan.fingerprint(), problems=plan.problems)
        return 1
    if not plan.steps:
        print("Nothing to do."); return 0
    if plan.fingerprint() != saved["fingerprint"] and not a.yes:
        print("Re-run `preflight plan` and review the new plan before applying."); return 1
    what = "apply these changes" + (" and START the experiment (this locks the flag)" if a.start else "")
    if not confirm(f"\n{what[0].upper() + what[1:]}?", a.yes):
        audit("apply.declined", fingerprint=plan.fingerprint()); return 1

    def log(step, res):
        rid = res.get("id") if isinstance(res, dict) else None
        print(c("  ✓ ", "32") + step.summary + (f"  (id {rid})" if rid else ""))
        audit("apply.step", fingerprint=plan.fingerprint(), call=f"{step.method} {step.path}", id=rid)

    from .planner import ApplyError
    try:
        result = apply_plan(api, plan, start=a.start, on_step=log)
    except ApplyError as e:
        audit("apply.failed", fingerprint=plan.fingerprint(), step=e.step_no, call=f"{e.step.method} {e.step.path}",
              error=str(e.cause)[:300], done=e.done)
        print(c(f"\n✗ Step {e.step_no} failed: {e.step.summary}\n  {e.cause}", "31;1"))
        print(f"  Already applied: {e.done or 'nothing'}.\n  Nothing after step {e.step_no} was sent. "
              "Fix the cause, then run `preflight plan` again: it skips what already exists and resumes.")
        return 1
    audit("apply.done", fingerprint=plan.fingerprint(), **result)
    print(c(f"\nDone: experiment #{result['experiment_id']} status={result['status']}", "32;1"))
    if not a.start:
        print("Not started. Run your smoke test, then start it in the dashboard or with --start.")
    return 0


def cmd_selfhost(a) -> int:
    import subprocess

    from .selfhost import bootstrap_from_files, make_env_example

    folder = Path(a.folder)
    if a.action == "up":
        make_env_example(folder / ".env")
        print("Starting self-hosted Flagsmith (first run downloads the images)…")
        r = subprocess.run(["docker", "compose", "up", "-d"], cwd=folder)
        if r.returncode:
            return r.returncode
        a.action = "bootstrap"
    if a.action == "bootstrap":
        res = bootstrap_from_files(folder)
        audit("selfhost.bootstrap", project=res["project"], environments=list(res["environments"]))
        print(c("\n✓ Self-hosted Flagsmith is ready for experiments.", "32;1"))
        print(f"  Dashboard: http://localhost:8000   Config: {folder / '.env.local'}\n"
              f"  Use it:    preflight --env-file {folder / '.env.local'} check")
        return 0
    if a.action == "down":
        return subprocess.run(["docker", "compose", "down"] + (["-v"] if a.wipe else []), cwd=folder).returncode
    return 2


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--env-file" in argv:  # load a specific config first (e.g. selfhost/.env.local); it wins over .env
        i = argv.index("--env-file")
        load_dotenv(Path(argv[i + 1]))
        del argv[i:i + 2]
    load_dotenv()
    p = argparse.ArgumentParser(prog="preflight", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"preflight {__version__}")
    p.add_argument("--env-file", help="load settings from this file first (e.g. selfhost/.env.local)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("lint", help="static checks on your code")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--event", action="append", help="metric event name (repeatable, case-sensitive)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("check", help="code + Flagsmith platform checks")
    s.add_argument("path", nargs="?", default=".")
    s.add_argument("--environment")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("plan", help="validate and preview an experiment (read-only)")
    s.add_argument("--spec", help="JSON spec file (instead of the flags below)")
    s.add_argument("--environment")
    s.add_argument("--name")
    s.add_argument("--hypothesis")
    s.add_argument("--flag")
    s.add_argument("--control-value", default="control")
    s.add_argument("--variant", action="append", help="KEY or KEY=VALUE (repeatable)")
    s.add_argument("--split", action="append", help="KEY=PERCENT of experiment traffic (default: even)")
    s.add_argument("--traffic", type=float, default=100.0, help="%% of users who enter the experiment")
    s.add_argument("--event", help="conversion event name (case-sensitive)")
    s.add_argument("--metric-name")
    s.add_argument("--code", help="path to your app: confirm it really sends the event")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("apply", help="apply a reviewed plan (asks a human first)")
    s.add_argument("plan_file")
    s.add_argument("--start", action="store_true", help="also start the experiment")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("selfhost", help="run and bootstrap a self-hosted Flagsmith with Experimentation")
    s.add_argument("action", choices=["up", "bootstrap", "down"])
    s.add_argument("--folder", default="selfhost")
    s.add_argument("--wipe", action="store_true", help="with down: also delete the database volume")
    s.set_defaults(fn=cmd_selfhost)

    s = sub.add_parser("smoke", help="drive synthetic users through the real SDK")
    s.add_argument("--flag", required=True)
    s.add_argument("--event", required=True)
    s.add_argument("--environment")
    s.add_argument("-n", type=int, default=120)
    s.add_argument("--rate", type=float, default=0.3)
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s.add_argument("--allow-live", action="store_true")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("verdict", help="honest go / no-go on results")
    s.add_argument("experiment_id", type=int)
    s.add_argument("--environment")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--raw", action="store_true", help="print the raw results payload")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_verdict)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except Exception as e:  # show a clear message, not a stack trace
        print(c(f"preflight: {e}", "31"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
