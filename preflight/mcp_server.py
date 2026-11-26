"""Preflight as an MCP server, so any AI coding agent (Claude Code, Cursor, Codex...) can set up and
judge Flagsmith experiments safely.

Why this exists: Flagsmith's own MCP server can create experiments but cannot verify that they will
record data, and has no tool to start them. Preflight's tools validate first, show the exact changes,
and only apply a plan whose fingerprint the human has seen.

Safety model:
  - lint / check / plan / verdict are read-only (annotated readOnlyHint) and never change Flagsmith.
  - apply is annotated destructiveHint, so MCP clients ask the human before each call.
  - apply only executes a plan previously returned by `plan`, identified by its fingerprint, and only if
    Flagsmith still matches it. If anything changed, it refuses and asks for a new plan.
  - smoke (writes synthetic traffic) is deliberately NOT exposed to agents.
  - every call is appended to .preflight/audit.jsonl.

Run:  python -m preflight.mcp_server        (stdio)
Add to Claude Code:  claude mcp add preflight -- python -m preflight.mcp_server
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .cli import audit, load_dotenv
from .gates import judge
from .lint import lint

load_dotenv()

server = MCPServer(
    name="preflight",
    title="Preflight for Flagsmith Experimentation",
    instructions=(
        "Use these tools to set up and judge Flagsmith A/B experiments safely. "
        "Always call preflight_plan first and show the user its steps, problems and warnings. "
        "Only call preflight_apply with the fingerprint of a plan the user explicitly approved. "
        "Never call a winner yourself: use preflight_verdict, which applies Flagsmith's own thresholds."
    ),
    version="0.2.0",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
PLANS = Path(".preflight") / "plans"


def _env(environment: str | None) -> str:
    env = environment or os.environ.get("FLAGSMITH_ENVIRONMENT_KEY")
    if not env:
        raise ValueError("No environment key: pass `environment` or set FLAGSMITH_ENVIRONMENT_KEY.")
    return env


@server.tool(annotations=READ_ONLY)
def preflight_lint(path: str = ".", metric_events: list[str] | None = None) -> dict[str, Any]:
    """Scan app code for the silent ways a Flagsmith experiment records nothing: events not enabled,
    flag read before identify, event-name case mismatch with a metric, unflushed events, SDK too old.
    `metric_events` are the case-sensitive event names your metrics count."""
    findings = lint(Path(path), set(metric_events) if metric_events else None)
    audit("mcp.lint", path=path, findings=len(findings))
    return {"ok": not any(f.severity == "error" for f in findings), "findings": [f.as_dict() for f in findings]}


@server.tool(annotations=READ_ONLY)
def preflight_check(path: str = ".", environment: str | None = None) -> dict[str, Any]:
    """Check a Flagsmith environment (warehouse connection, metrics, experiments, variation keys and
    allocations) and cross-check the code at `path` against the metrics' event names."""
    from .api import FlagsmithAdmin
    from .checks import check_environment

    env = _env(environment)
    platform, ctx = check_environment(FlagsmithAdmin(), env)
    code = lint(Path(path), set(ctx["metrics"].values()))
    findings = platform + code
    audit("mcp.check", findings=len(findings))
    return {"ok": not any(f.severity == "error" for f in findings), "context": ctx,
            "findings": [f.as_dict() for f in findings]}


@server.tool(annotations=READ_ONLY)
def preflight_plan(name: str, hypothesis: str, flag: str, event: str, variants: list[str],
                   split: dict[str, float] | None = None, traffic: float = 100.0,
                   control_value: str = "control", metric_name: str | None = None,
                   code_path: str | None = None, environment: str | None = None) -> dict[str, Any]:
    """Validate an experiment and preview the exact Flagsmith API calls, WITHOUT changing anything.
    `variants` are 'key' or 'key=value' strings. `split` maps variant key -> % of experiment traffic
    (control gets the rest; default is an even split). Pass `code_path` to confirm the app sends `event`.
    Returns a fingerprint; show the user the steps, problems and warnings before any apply."""
    from .api import FlagsmithAdmin
    from .planner import Spec, Variant, build_plan

    vs = []
    for raw in variants:
        k, _, v = raw.partition("=")
        vs.append(Variant(k.strip(), (v or k).strip()))
    spec = Spec(environment=_env(environment), name=name, hypothesis=hypothesis, flag=flag, variants=vs,
                event=event, metric_name=metric_name, control_value=control_value, traffic=traffic,
                split=split or {}, code_path=code_path)
    plan = build_plan(FlagsmithAdmin(), spec)
    PLANS.mkdir(parents=True, exist_ok=True)
    (PLANS / f"{plan.fingerprint()}.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    audit("mcp.plan", fingerprint=plan.fingerprint(), ok=plan.ok)
    out = plan.to_dict()
    out["next"] = ("Show this plan to the user. If they approve, call preflight_apply with this fingerprint."
                   if plan.ok else "Fix the problems listed, then plan again. Do not apply.")
    return out


@server.tool(annotations=WRITES)
def preflight_apply(fingerprint: str, start: bool = False) -> dict[str, Any]:
    """Apply a plan the user approved, identified by the fingerprint preflight_plan returned.
    Refuses if Flagsmith changed since that plan (the fingerprint no longer matches) or the plan has problems.
    `start=True` also starts the experiment, which locks the flag."""
    from .api import FlagsmithAdmin
    from .planner import ApplyError, Spec, apply_plan, build_plan

    f = PLANS / f"{fingerprint}.json"
    if not f.exists():
        return {"applied": False, "reason": f"No plan with fingerprint {fingerprint}. Call preflight_plan first."}
    saved = json.loads(f.read_text(encoding="utf-8"))
    api = FlagsmithAdmin()
    plan = build_plan(api, Spec.from_dict(saved["spec"]))
    if plan.fingerprint() != fingerprint:
        audit("mcp.apply.stale", fingerprint=fingerprint, now=plan.fingerprint())
        return {"applied": False, "reason": "Flagsmith changed since this plan was shown. Make a new plan and "
                "get the user's approval again.", "current_plan": plan.to_dict()}
    if not plan.ok:
        return {"applied": False, "reason": "Plan has blocking problems.", "problems": plan.problems}
    steps: list[str] = []
    try:
        result = apply_plan(api, plan, start=start, on_step=lambda s, r: steps.append(s.summary))
    except ApplyError as e:
        audit("mcp.apply.failed", fingerprint=fingerprint, step=e.step_no, error=str(e.cause)[:300])
        return {"applied": False, "failed_step": e.step_no, "error": str(e.cause), "already_applied": e.done,
                "next": "Fix the cause and plan again; the plan resumes from what already exists."}
    audit("mcp.apply.done", fingerprint=fingerprint, **result)
    return {"applied": True, "steps": steps, **result}


@server.tool(annotations=READ_ONLY)
def preflight_verdict(experiment_id: int, environment: str | None = None, refresh: bool = False) -> dict[str, Any]:
    """Honest go / no-go for a running experiment using Flagsmith's thresholds: no call before every arm has
    50 identities and 5 conversions; a winner needs >95% chance to beat control and a credible interval
    clear of zero; a sample-ratio mismatch overrides everything. `refresh` asks Flagsmith to recompute
    (allowed once per 5 minutes)."""
    from .api import FlagsmithAdmin
    from .checks import allocations_of, normalise_flagsmith_results

    env = _env(environment)
    api = FlagsmithAdmin()
    note = None
    if refresh:
        try:
            api.refresh_results(env, experiment_id)
        except Exception as e:
            note = f"refresh skipped: {e}"
    exp = api.experiment(env, experiment_id)
    primary = next((m.get("metric") for m in exp.get("metrics") or []), None)
    arms, meta = normalise_flagsmith_results(api.results(env, experiment_id), allocations_of(exp), primary)
    v = judge(arms)
    if (meta.get("flagsmith_srm_p_value") or 1) < 0.001:
        v.status, v.headline, v.ship = "srm", "Flagsmith reports a sample-ratio mismatch.", None
    audit("mcp.verdict", experiment=experiment_id, status=v.status)
    return {"experiment": {"id": exp.get("id"), "name": exp.get("name"), "status": exp.get("status"),
                           "hypothesis": exp.get("hypothesis")},
            "verdict": v.as_dict(), "arms": [a.__dict__ for a in arms], "meta": meta, "note": note}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
