"""Checks that need the Flagsmith Admin API, plus results normalisation."""
from __future__ import annotations

from typing import Any

from .api import FlagsmithAdmin
from .gates import Arm
from .lint import Finding


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def metric_events(metrics: list[dict]) -> dict[str, str]:
    """metric name -> event name it counts."""
    out = {}
    for m in metrics:
        ev = _first(m.get("definition") or {}, "event") or _first(m, "event")
        if ev:
            out[str(m.get("name", ev))] = str(ev)
    return out


def check_environment(api: FlagsmithAdmin, env_key: str) -> tuple[list[Finding], dict]:
    """Platform-side checks. Returns findings and a context dict for display."""
    findings: list[Finding] = []
    where = f"flagsmith:{env_key[:6]}…"

    conns = api.warehouse_connections(env_key)
    if not conns:
        findings.append(Finding("PF101", "error", where, 0,
                                "This environment has no warehouse connection, so no experiment can record events.",
                                "Environment Settings > Warehouse > connect the Flagsmith warehouse (Cloud) or your own."))
    else:
        bad = [c for c in conns if str(_first(c, "status", default="")).lower() in ("errored", "error", "pending connection")]
        for c in bad:
            findings.append(Finding("PF101", "error", where, 0,
                                    f"Warehouse connection status is '{c.get('status')}'.",
                                    "Open Environment Settings > Warehouse and fix the connection."))

    metrics = api.metrics(env_key)
    events = metric_events(metrics)
    experiments = api.experiments(env_key)

    for exp in experiments:
        name = exp.get("name", f"experiment {exp.get('id')}")
        status = str(exp.get("status", "")).lower()
        # Variations must carry a key or Flagsmith records no exposures for them (docs).
        variations = _first(exp, "variations", "multivariate_options", default=None)
        if variations is None:
            rollout = exp.get("experiment_rollout") or {}
            variations = rollout.get("multivariate_feature_state_values")
        for v in variations or []:
            key = _first(v, "key", "variant_key", "multivariate_feature_option_key")
            if isinstance(v, dict) and "key" in v and not key:
                findings.append(Finding("PF102", "error", where, 0,
                                        f"'{name}': a variation has no key. Flagsmith records no exposures for it.",
                                        "Edit the flag's variant and give it a key (variant name)."))
        if status in ("draft", "created", "not_started"):
            findings.append(Finding("PF103", "warning", where, 0,
                                    f"'{name}' exists but is not running (status '{status}').",
                                    "Start it once the smoke test passes."))

    ctx = {"warehouse_connections": len(conns), "metrics": events, "experiments": [
        {"id": e.get("id"), "name": e.get("name"), "status": e.get("status")} for e in experiments]}
    return findings, ctx


def _ratio(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x / 100.0 if x > 1.0 else x


def normalise_results(raw: Any) -> list[Arm]:
    """Map a Flagsmith results payload to Arms. Tolerant to field naming; verify with --raw."""
    if isinstance(raw, dict):
        rows = _first(raw, "variations", "variants", "arms", "results", default=[])
        if isinstance(rows, dict):  # {name: {...}}
            rows = [{"name": k, **v} for k, v in rows.items()]
        metric_rows = _first(raw, "metrics", default=None)
    else:
        rows, metric_rows = raw or [], None
    arms: list[Arm] = []
    for r in rows:
        name = str(_first(r, "key", "name", "variation", default="?"))
        is_control = bool(_first(r, "is_control", default=False)) or name.lower() == "control"
        ci = _first(r, "credible_interval", "lift_credible_interval", "ci", default=None)
        lo = hi = None
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            lo, hi = _ratio(ci[0]), _ratio(ci[1])
        elif isinstance(ci, dict):
            lo, hi = _ratio(_first(ci, "lower", "low")), _ratio(_first(ci, "upper", "high"))
        arms.append(Arm(
            name=name,
            identities=int(_first(r, "identities", "exposures", "enrolled", "sample_size", default=0) or 0),
            conversions=_first(r, "conversions", "converted", default=None),
            expected_share=_ratio(_first(r, "allocation", "percentage_allocation", "weight", default=None)),
            win_probability=_ratio(_first(r, "chance_to_beat_control", "win_probability", "probability_to_beat_control")),
            ci_low=lo, ci_high=hi, is_control=is_control,
        ))
    _ = metric_rows
    return arms
