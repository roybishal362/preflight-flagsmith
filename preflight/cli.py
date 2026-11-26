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
    fs = Flagsmith(environment_key=env_key, enable_events=True,
                   api_url=os.environ.get("FLAGSMITH_SDK_API_URL") or None)
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
    from .checks import normalise_results

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
    arms = normalise_results(raw)
    v = judge(arms)
    if a.json:
        print(json.dumps({"verdict": v.as_dict(), "arms": [x.__dict__ for x in arms]}, indent=2)); return 0
    colour = {"winner": "32;1", "loser": "33;1", "srm": "31;1"}.get(v.status, "36;1")
    print(c(v.headline, colour))
    for r in v.reasons:
        print("  -", r)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    load_dotenv()
    p = argparse.ArgumentParser(prog="preflight", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"preflight {__version__}")
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
