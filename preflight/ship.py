"""Round 4: ship the winner on the same flag, only when the evidence supports it.

Endpoint (from the Flagsmith OpenAPI schema, verified live 2026-11-26):
  POST environments/{key}/experiments/{id}/complete/
  body {"rollout_decision": "control" | "variant" | "disable", "rollout_variant": <multivariate option id>}
"Completes an experiment and decides what its feature serves afterwards ... The rollout override is removed."
"""
from __future__ import annotations

from dataclasses import dataclass

from .api import FlagsmithAdmin
from .checks import allocations_of, normalise_flagsmith_results
from .gates import Verdict, judge


@dataclass
class ShipPlan:
    experiment: dict
    verdict: Verdict
    decision: str | None          # control | variant | disable
    variant_key: str | None
    variant_id: int | None
    allowed: bool
    overridden: bool
    reason: str

    def body(self) -> dict:
        b: dict = {"rollout_decision": self.decision}
        if self.decision == "variant":
            b["rollout_variant"] = self.variant_id
        return b

    def as_dict(self) -> dict:
        return {"experiment": {k: self.experiment.get(k) for k in ("id", "name", "status")},
                "verdict": self.verdict.as_dict(), "decision": self.decision, "variant": self.variant_key,
                "allowed": self.allowed, "overridden": self.overridden, "reason": self.reason,
                "request": self.body() if self.decision else None}


def plan_ship(api: FlagsmithAdmin, env: str, exp_id: int, override: str | None = None,
              override_reason: str | None = None) -> ShipPlan:
    """override: 'control', 'disable' or a variant key. Only used when the evidence doesn't decide."""
    exp = api.experiment(env, exp_id)
    options = {o.get("key"): o["id"] for o in (exp.get("feature") or {}).get("multivariate_options") or []}
    primary = next((m.get("metric") for m in exp.get("metrics") or []), None)
    arms, meta = normalise_flagsmith_results(api.results(env, exp_id), allocations_of(exp), primary)
    v = judge(arms)
    if (meta.get("flagsmith_srm_p_value") or 1) < 0.001:
        v.status, v.headline, v.ship = "srm", "Flagsmith reports a sample-ratio mismatch.", None

    status = str(exp.get("status", "")).lower()
    if status in ("completed", "complete", "ended"):
        return ShipPlan(exp, v, None, None, None, False, False,
                        f"Experiment is already {status} (decision: {exp.get('rollout_decision')}).")

    def target(name: str) -> tuple[str, str | None, int | None]:
        if name in ("control", "disable"):
            return name, None, None
        if name not in options:
            raise ValueError(f"Unknown variant '{name}'. Variants on this flag: {sorted(options)}")
        return "variant", name, options[name]

    if v.status in ("winner", "loser") and v.ship:
        d, k, i = target(v.ship)
        if override and override != v.ship:
            return ShipPlan(exp, v, d, k, i, False, False,
                            f"The evidence says ship '{v.ship}', not '{override}'. Refusing to ship against it.")
        return ShipPlan(exp, v, d, k, i, True, False, v.headline)

    if override:
        if not (override_reason and len(override_reason.split()) >= 4):
            return ShipPlan(exp, v, None, None, None, False, False,
                            "The evidence doesn't decide this experiment. To ship anyway, give --decision and a "
                            "written --reason (at least a few words); it will be recorded as a gates override.")
        d, k, i = target(override)
        return ShipPlan(exp, v, d, k, i, True, True, f"Gates overridden: {override_reason}")
    return ShipPlan(exp, v, None, None, None, False, False,
                    f"{v.headline} Nothing will be shipped. Keep it running, or decide explicitly with "
                    "--decision and --reason.")


def ship(api: FlagsmithAdmin, env: str, plan: ShipPlan) -> dict:
    if not plan.allowed or not plan.decision:
        raise RuntimeError("This ship plan is not allowed.")
    exp_id = plan.experiment["id"]
    api.post(f"environments/{env}/experiments/{exp_id}/complete/", plan.body())
    after = api.experiment(env, exp_id)  # read back: never report success we didn't observe
    return {"status": after.get("status"), "rollout_decision": after.get("rollout_decision"),
            "rollout_variant": after.get("rollout_variant"), "ended_at": after.get("ended_at")}
