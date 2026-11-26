"""Plan and apply a Flagsmith experiment safely.

`build_plan` is read-only: it inspects Flagsmith, validates the spec against the documented failure
modes (and optionally against your code), and returns the exact API calls it would make.
`apply_plan` makes those calls only after a human approved the plan, and records each one.

Endpoints (verified against Flagsmith Cloud on 2026-11-26 unless marked):
  GET  environments/{key}/                              -> project id
  GET  projects/{pid}/features/?search=                 -> existing flag
  POST projects/{pid}/features/                         (create flag)
  GET/POST projects/{pid}/features/{fid}/mv-options/    (variations, with `key`)
  GET/POST environments/{key}/experiment-metrics/
  GET/POST environments/{key}/experiments/
  POST environments/{key}/experiments/{id}/start/
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .api import FlagsmithAdmin
from .lint import lint

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


@dataclass
class Variant:
    key: str
    value: str


@dataclass
class Spec:
    environment: str
    name: str
    hypothesis: str
    flag: str
    variants: list[Variant]
    event: str
    metric_name: str | None = None
    control_value: str = "control"
    traffic: float = 100.0
    split: dict[str, float] = field(default_factory=dict)  # variant key -> %; control gets the rest
    aggregation: str = "occurrence"
    direction: str = "up"
    code_path: str | None = None

    @staticmethod
    def from_dict(d: dict) -> "Spec":
        d = dict(d)
        d["variants"] = [v if isinstance(v, Variant) else Variant(**v) for v in d.get("variants", [])]
        return Spec(**d)

    def to_dict(self) -> dict:
        return asdict(self)

    def effective_split(self) -> dict[str, float]:
        if self.split:
            return dict(self.split)
        share = 100.0 / (len(self.variants) + 1)
        return {v.key: round(share, 2) for v in self.variants}


@dataclass
class Step:
    summary: str
    method: str
    path: str
    body: dict | None
    run: Callable[[dict], Any] | None = None  # receives the shared context, returns the API response

    def show(self) -> dict:
        return {"summary": self.summary, "call": f"{self.method} {self.path}", "body": self.body}


@dataclass
class Plan:
    spec: Spec
    project_id: int | None
    problems: list[str]          # blocking: apply refuses
    warnings: list[str]
    existing: dict[str, Any]
    steps: list[Step]

    @property
    def ok(self) -> bool:
        return not self.problems

    def fingerprint(self) -> str:
        """Approval token: changes if anything in the plan changes."""
        blob = json.dumps({"spec": self.spec.to_dict(), "steps": [s.show() for s in self.steps]}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {"fingerprint": self.fingerprint(), "ok": self.ok, "problems": self.problems,
                "warnings": self.warnings, "existing": self.existing, "spec": self.spec.to_dict(),
                "steps": [s.show() for s in self.steps]}


def validate_spec(spec: Spec) -> tuple[list[str], list[str]]:
    problems, warnings = [], []
    if not spec.variants:
        problems.append("Add at least one variation besides control.")
    keys = [v.key for v in spec.variants]
    for k in keys:
        if not k:
            problems.append("Every variation needs a key: without one Flagsmith records no exposures for it.")
        elif k == "control":
            problems.append("'control' is reserved for the flag's own value; pick another variation key.")
        elif not KEY_RE.match(k):
            problems.append(f"Variation key '{k}' should be lowercase letters, digits, '_' or '-'.")
    if len(set(keys)) != len(keys):
        problems.append("Variation keys must be unique.")
    split = spec.effective_split()
    unknown = set(split) - set(keys)
    if unknown:
        problems.append(f"Split mentions unknown variation(s): {sorted(unknown)}.")
    total = sum(split.values())
    if total >= 100:
        problems.append(f"Variations take {total:g}% of experiment traffic, leaving nothing for control.")
    for k in keys:
        if split.get(k, 0) <= 0:
            problems.append(f"Variation '{k}' gets 0% of traffic, so it can never win.")
    if not (0 < spec.traffic <= 100):
        problems.append("Traffic must be between 0 and 100%.")
    if not spec.event.strip():
        problems.append("A metric needs an event name.")
    elif spec.event != spec.event.strip() or " " in spec.event:
        warnings.append(f"Event '{spec.event}' contains spaces; make sure your code sends exactly this string.")
    if spec.aggregation not in ("occurrence", "count", "sum", "mean"):
        warnings.append(f"Aggregation '{spec.aggregation}' is not one I have verified; 'occurrence' is.")
    if len(spec.hypothesis.split()) < 6:
        warnings.append("The hypothesis is very short. Name the change, the metric and the expected direction.")
    return problems, warnings


def build_plan(api: FlagsmithAdmin, spec: Spec) -> Plan:
    problems, warnings = validate_spec(spec)
    env = spec.environment
    existing: dict[str, Any] = {}
    steps: list[Step] = []

    env_info = api.get(f"environments/{env}/")
    pid = env_info.get("project")
    existing["environment"] = env_info.get("name")

    # 1. warehouse: without one, nothing is recorded
    conns = api.warehouse_connections(env)
    if not conns:
        problems.append(f"Environment '{env_info.get('name')}' has no warehouse connection, so no event can be recorded. "
                        "Environment Settings > Warehouse > connect one first.")

    # 2. flag and its variations
    feature = next((f for f in api.features(pid, spec.flag) if f.get("name") == spec.flag), None)
    options: list[dict] = []
    if feature:
        existing["flag"] = {"id": feature["id"], "type": feature.get("type")}
        options = api.items(api.get(f"projects/{pid}/features/{feature['id']}/mv-options/"))
        if str(feature.get("initial_value")) != spec.control_value:
            warnings.append(f"Flag '{spec.flag}' has value '{feature.get('initial_value')}', not '{spec.control_value}'. "
                            "Control will serve the flag's existing value.")
    else:
        fbody = {"name": spec.flag, "initial_value": spec.control_value, "default_enabled": True,
                 "description": f"Experiment: {spec.name}"}
        steps.append(Step(f"Create flag '{spec.flag}' (control value '{spec.control_value}')",
                          "POST", f"projects/{pid}/features/", fbody,
                          run=lambda ctx, b=fbody: _store(ctx, "feature", api.post(f"projects/{pid}/features/", b))))
    by_key = {o.get("key"): o for o in options if o.get("key")}
    keyless = [o for o in options if not (o.get("key") or "").strip()]
    if keyless:
        problems.append(f"Flag '{spec.flag}' already has {len(keyless)} variation(s) without a key "
                        f"({', '.join(str(o.get('string_value')) for o in keyless)}). Flagsmith records no exposures "
                        "for them. Give them keys in the dashboard first.")
    for v in spec.variants:
        if v.key in by_key:
            if str(by_key[v.key].get("string_value")) != v.value:
                warnings.append(f"Variation '{v.key}' exists with value '{by_key[v.key].get('string_value')}'; "
                                f"the plan keeps it (not '{v.value}').")
            continue
        body = {"type": "unicode", "string_value": v.value, "default_percentage_allocation": 0, "key": v.key}
        steps.append(Step(f"Add variation '{v.key}' = '{v.value}' to '{spec.flag}'", "POST",
                          f"projects/{pid}/features/<flag id>/mv-options/", body,
                          run=_add_option(api, pid, body)))
    existing["variations"] = sorted(by_key)

    # 3. one active experiment per flag (Flagsmith rule; the flag is locked while it runs)
    exps = api.experiments(env)
    active = [e for e in exps if (e.get("feature") or {}).get("name") == spec.flag
              and str(e.get("status")).lower() in ("running", "created", "draft", "paused")]
    if active:
        problems.append(f"Flag '{spec.flag}' already has experiment '{active[0].get('name')}' "
                        f"(#{active[0].get('id')}, {active[0].get('status')}). Flagsmith allows one active experiment per flag.")
    if any(e.get("name") == spec.name for e in exps):
        problems.append(f"An experiment named '{spec.name}' already exists in this environment.")

    # 4. metric (environment-scoped)
    metrics = api.metrics(env)
    metric = next((m for m in metrics if (m.get("definition") or {}).get("event") == spec.event), None)
    near = [m for m in metrics if (m.get("definition") or {}).get("event", "").casefold() == spec.event.casefold()
            and (m.get("definition") or {}).get("event") != spec.event]
    if near:
        problems.append(f"Metric '{near[0].get('name')}' counts event '{near[0]['definition']['event']}', which differs "
                        f"from '{spec.event}' only in case. Event names are case-sensitive; pick one spelling.")
    if metric:
        existing["metric"] = {"id": metric["id"], "name": metric.get("name")}
    else:
        mbody = {"name": spec.metric_name or spec.event.replace("_", " ").capitalize(),
                 "aggregation": spec.aggregation, "direction": spec.direction,
                 "definition": {"version": 1, "event": spec.event}}
        steps.append(Step(f"Create metric '{mbody['name']}' counting event '{spec.event}' ({spec.aggregation})",
                          "POST", f"environments/{env}/experiment-metrics/", mbody,
                          run=lambda ctx, b=mbody: _store(ctx, "metric",
                                                          api.post(f"environments/{env}/experiment-metrics/", b))))

    # 5. the experiment (created, not started: starting locks the flag)
    split = spec.effective_split()
    ebody = {
        "feature": "<flag id>", "name": spec.name, "hypothesis": spec.hypothesis,
        "metrics": [{"metric": "<metric id>", "expected_direction": "increase" if spec.direction == "up" else "decrease"}],
        "experiment_rollout": {
            "enabled": True, "rollout_percentage": spec.traffic,
            "feature_state_value": {"type": "string", "value": spec.control_value},
            "multivariate_feature_state_values": [
                {"multivariate_feature_option": f"<id of {k}>", "percentage_allocation": pct} for k, pct in split.items()],
        },
    }
    steps.append(Step(f"Create experiment '{spec.name}': {spec.traffic:g}% of traffic, split "
                      + ", ".join(f"{k} {p:g}%" for k, p in split.items())
                      + f", control {100 - sum(split.values()):g}%",
                      "POST", f"environments/{env}/experiments/", ebody, run=_create_experiment(api, env, pid, spec)))

    # 6. code: does the app actually send what the metric counts?
    if spec.code_path:
        findings = lint(Path(spec.code_path), {spec.event})
        for f in findings:
            line = f"[{f.rule}] {f.message} ({f.file}:{f.line})"
            (problems if f.severity == "error" else warnings).append(line)
        existing["code_checked"] = spec.code_path
    else:
        warnings.append("No --code path given, so I could not confirm your app sends this event.")

    return Plan(spec, pid, problems, warnings, existing, steps)


def _store(ctx: dict, key: str, value: Any) -> Any:
    ctx[key] = value
    return value


class ApplyError(RuntimeError):
    def __init__(self, step_no: int, step: "Step", cause: Exception, done: list[str]):
        self.step_no, self.step, self.cause, self.done = step_no, step, cause, done
        super().__init__(f"Step {step_no} failed ({step.summary}): {cause}")


def _add_option(api: FlagsmithAdmin, pid: int, body: dict) -> Callable[[dict], Any]:
    def run(ctx: dict) -> Any:
        fid = ctx["feature"]["id"]
        # The API also wants the feature id in the body (HTTP 400 without it, verified 2026-11-26).
        return api.post(f"projects/{pid}/features/{fid}/mv-options/", {**body, "feature": fid})
    return run


def _create_experiment(api: FlagsmithAdmin, env: str, pid: int, spec: Spec) -> Callable[[dict], Any]:
    def run(ctx: dict) -> Any:
        fid = ctx["feature"]["id"]
        options = api.items(api.get(f"projects/{pid}/features/{fid}/mv-options/"))
        ids = {o.get("key"): o["id"] for o in options}
        split = spec.effective_split()
        missing = [k for k in split if k not in ids]
        if missing:
            raise RuntimeError(f"Variations {missing} were not found on the flag after creating them.")
        body = {
            "feature": fid, "name": spec.name, "hypothesis": spec.hypothesis,
            "metrics": [{"metric": ctx["metric"]["id"],
                         "expected_direction": "increase" if spec.direction == "up" else "decrease"}],
            "experiment_rollout": {
                "enabled": True, "rollout_percentage": spec.traffic,
                "feature_state_value": {"type": "string", "value": spec.control_value},
                "multivariate_feature_state_values": [
                    {"multivariate_feature_option": ids[k], "percentage_allocation": pct} for k, pct in split.items()],
            },
        }
        res = api.post(f"environments/{env}/experiments/", body)
        ctx["experiment"] = res
        return res
    return run


def apply_plan(api: FlagsmithAdmin, plan: Plan, start: bool = False,
               on_step: Callable[[Step, Any], None] | None = None) -> dict:
    if not plan.ok:
        raise RuntimeError("Plan has blocking problems; refusing to apply.")
    ctx: dict[str, Any] = {}
    if "flag" in plan.existing:
        ctx["feature"] = {"id": plan.existing["flag"]["id"]}
    if "metric" in plan.existing:
        ctx["metric"] = {"id": plan.existing["metric"]["id"]}
    done: list[str] = []
    for i, s in enumerate(plan.steps, 1):
        try:
            res = s.run(ctx) if s.run else None
        except Exception as e:  # stop at the first failure; the plan is idempotent, so re-planning resumes here
            raise ApplyError(i, s, e, done) from e
        done.append(s.summary)
        if on_step:
            on_step(s, res)
    exp = ctx.get("experiment") or {}
    if start and exp.get("id"):
        res = api.post(f"environments/{plan.spec.environment}/experiments/{exp['id']}/start/")
        ctx["started"] = res
        if on_step:
            on_step(Step(f"Start experiment #{exp['id']}", "POST",
                         f"environments/{plan.spec.environment}/experiments/{exp['id']}/start/", None), res)
    return {"experiment_id": exp.get("id"), "status": (ctx.get("started") or exp).get("status"),
            "feature_id": (ctx.get("feature") or {}).get("id"), "metric_id": (ctx.get("metric") or {}).get("id")}
