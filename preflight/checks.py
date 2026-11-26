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
        # Verified shape (2026-11-26): experiment.feature.multivariate_options[].key
        feature = exp.get("feature") or {}
        options = feature.get("multivariate_options") or []
        if feature.get("type") and feature.get("type") != "MULTIVARIATE":
            findings.append(Finding("PF102", "error", where, 0,
                                    f"'{name}': flag '{feature.get('name')}' is not multivariate, so there is nothing to split.",
                                    "Add at least one variant to the flag."))
        for v in options:
            if not (v.get("key") or "").strip():
                label = v.get("string_value") or v.get("integer_value") or v.get("id")
                findings.append(Finding("PF102", "error", where, 0,
                                        f"'{name}': variation '{label}' on flag '{feature.get('name')}' has no key. "
                                        "Flagsmith records no exposures for it.",
                                        "Features > open the flag > pencil icon next to the variant name > set a key."))
        allocated = {s.get("multivariate_feature_option"): s.get("percentage_allocation")
                     for s in (exp.get("experiment_rollout") or {}).get("multivariate_feature_state_values") or []}
        for v in options:
            if v.get("id") in allocated and not allocated[v["id"]]:
                findings.append(Finding("PF104", "warning", where, 0,
                                        f"'{name}': variation '{v.get('key') or v.get('string_value')}' gets 0% of traffic.",
                                        "Give it a share in the experiment's variation split."))
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


def _inference_for(inference: Any, variant: str) -> dict:
    """`inference` was {} in every payload seen so far (too little data); accept a per-variant dict
    or a list of {variant: ...} rows once Flagsmith fills it."""
    if isinstance(inference, dict):
        v = inference.get(variant)
        if isinstance(v, dict):
            return v
        rows = _first(inference, "variants", "comparisons", default=None)
        if isinstance(rows, dict) and isinstance(rows.get(variant), dict):
            return rows[variant]
        if isinstance(rows, list):
            return next((r for r in rows if _first(r, "variant", "key", "name") == variant), {})
    if isinstance(inference, list):
        return next((r for r in inference if _first(r, "variant", "key", "name") == variant), {})
    return {}


def normalise_flagsmith_results(raw: Any, allocations: dict[str, float] | None = None,
                                metric_id: int | None = None) -> tuple[list[Arm], dict]:
    """Parse the verified Flagsmith shape (captured 2026-11-26):
    {"results": {"as_of", "is_final", "payload": {"metrics": [{"metric_id", "variants": {name: {"n", "sum", ...}},
     "inference": {...}}], "srm_p_value", "exposures_timeseries"}}}
    Returns (arms, meta)."""
    res = (raw or {}).get("results") if isinstance(raw, dict) else None
    if not res:
        return [], {"state": "no results computed yet (run with --refresh, then wait a minute)"}
    payload = res.get("payload") or {}
    metrics = payload.get("metrics") or []
    m = next((x for x in metrics if metric_id is None or x.get("metric_id") == metric_id), None)
    meta = {"as_of": res.get("as_of"), "is_final": res.get("is_final"),
            "flagsmith_srm_p_value": payload.get("srm_p_value"), "metric_id": m.get("metric_id") if m else None}
    if not m:
        return [], {**meta, "state": "no metric data yet"}
    allocations = allocations or {}
    arms = []
    for name, stats in (m.get("variants") or {}).items():
        inf = _inference_for(m.get("inference"), name)
        ci = _first(inf, "credible_interval", "lift_credible_interval", "ci", "lift_ci", default=None)
        lo = hi = None
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            lo, hi = _ratio(ci[0]), _ratio(ci[1])
        elif isinstance(ci, dict):
            lo, hi = _ratio(_first(ci, "lower", "low")), _ratio(_first(ci, "upper", "high"))
        arms.append(Arm(
            name=name,
            identities=int(stats.get("n") or 0),
            conversions=int(round(stats.get("sum") or 0)),  # occurrence metric: identities that converted
            expected_share=_ratio(allocations.get(name)),
            win_probability=_ratio(_first(inf, "chance_to_beat_control", "probability_to_beat_control",
                                          "win_probability", "prob_beat_control")),
            ci_low=lo, ci_high=hi, is_control=(name == "control"),
        ))
    # make sure every allocated arm appears, even with zero data
    for name, share in allocations.items():
        if name not in {a.name for a in arms}:
            arms.append(Arm(name, 0, 0, _ratio(share), is_control=(name == "control")))
    arms.sort(key=lambda a: (not a.is_control, a.name))
    return arms, meta


def allocations_of(exp: dict) -> dict[str, float]:
    """variant key -> % of experiment traffic, from the verified experiment shape."""
    opts = {o["id"]: (o.get("key") or o.get("string_value")) for o in (exp.get("feature") or {}).get("multivariate_options") or []}
    shares = {opts.get(s["multivariate_feature_option"]): s.get("percentage_allocation") or 0
              for s in (exp.get("experiment_rollout") or {}).get("multivariate_feature_state_values") or []}
    shares = {k: v for k, v in shares.items() if k}
    shares["control"] = max(0.0, 100.0 - sum(shares.values()))
    return shares


def normalise_results(raw: Any) -> list[Arm]:
    """Legacy/fallback mapper for other payload shapes."""
    if isinstance(raw, dict) and "results" in raw and (raw["results"] is None or "payload" in (raw["results"] or {})):
        return normalise_flagsmith_results(raw)[0]
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
