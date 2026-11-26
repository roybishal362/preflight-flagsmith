"""Platform checks, run against a fake API that returns the payload shape
captured from the real Flagsmith API on 2026-11-26 (experiment 692)."""
import copy

from preflight.checks import check_environment

EXPERIMENT = {
    "id": 692, "name": "Demo button text test", "status": "running",
    "feature": {"id": 299129, "name": "show_demo_button", "type": "MULTIVARIATE",
                "multivariate_options": [{"id": 37371, "string_value": "variant_b",
                                          "default_percentage_allocation": 50.0, "key": "variant_b"}]},
    "experiment_rollout": {"multivariate_feature_state_values": [
        {"multivariate_feature_option": 37371, "percentage_allocation": 50.0}]},
}


class FakeAPI:
    def __init__(self, experiments, warehouses=({"status": "Connected"},), metrics=None):
        self._e, self._w = experiments, list(warehouses)
        self._m = metrics if metrics is not None else [
            {"name": "Demo button click", "definition": {"version": 1, "event": "demo_button_clicked"}}]

    def warehouse_connections(self, env): return self._w
    def metrics(self, env): return self._m
    def experiments(self, env): return self._e


def rules(findings): return {f.rule for f in findings}


def test_real_payload_is_clean():
    findings, ctx = check_environment(FakeAPI([EXPERIMENT]), "nqtMsqwUVNMn")
    assert findings == []
    assert ctx["metrics"] == {"Demo button click": "demo_button_clicked"}


def test_variant_without_key_is_caught():
    exp = copy.deepcopy(EXPERIMENT)
    exp["feature"]["multivariate_options"][0]["key"] = ""
    assert "PF102" in rules(check_environment(FakeAPI([exp]), "k")[0])


def test_zero_allocation_and_missing_warehouse():
    exp = copy.deepcopy(EXPERIMENT)
    exp["experiment_rollout"]["multivariate_feature_state_values"][0]["percentage_allocation"] = 0
    found = rules(check_environment(FakeAPI([exp], warehouses=()), "k")[0])
    assert {"PF101", "PF104"} <= found


# Real results payload captured from Flagsmith on 2026-11-26 (one test click on control).
REAL_RESULTS = {"results": {"as_of": "2026-11-26T16:43:17Z", "is_final": False, "payload": {
    "metrics": [{"variants": {"control": {"n": 1, "sum": 1.0, "sum_squares": 1.0}}, "inference": {},
                 "metric_id": 682}], "srm_p_value": None}}}


def test_parse_real_results_payload():
    from preflight.checks import allocations_of, normalise_flagsmith_results
    from preflight.gates import judge
    arms, meta = normalise_flagsmith_results(REAL_RESULTS, allocations_of(EXPERIMENT), 682)
    by = {a.name: a for a in arms}
    assert by["control"].identities == 1 and by["control"].conversions == 1 and by["control"].is_control
    assert by["variant_b"].identities == 0 and by["variant_b"].expected_share == 0.5
    assert judge(arms).status == "collecting"


def test_no_results_yet():
    from preflight.checks import normalise_flagsmith_results
    arms, meta = normalise_flagsmith_results({"results": None})
    assert arms == [] and "no results" in meta["state"]
