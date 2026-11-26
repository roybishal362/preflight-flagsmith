"""Round 4 (ship) decisions, against a fake API returning the payload shapes seen on live Flagsmith."""
import copy

import pytest

from preflight.ship import plan_ship, ship

EXP = {"id": 2, "name": "Checkout CTA", "status": "running",
       "feature": {"id": 5, "name": "checkout_cta", "type": "MULTIVARIATE",
                   "multivariate_options": [{"id": 77, "key": "variant_b", "string_value": "treatment"}]},
       "metrics": [{"metric": 3}],
       "experiment_rollout": {"multivariate_feature_state_values": [
           {"multivariate_feature_option": 77, "percentage_allocation": 50.0}]}}


def results(n_c, conv_c, n_b, conv_b, win, lo, hi):
    return {"results": {"as_of": "t", "is_final": False, "payload": {"srm_p_value": None, "metrics": [{
        "metric_id": 3,
        "variants": {"control": {"n": n_c, "sum": conv_c}, "variant_b": {"n": n_b, "sum": conv_b}},
        "inference": {"variant_b": {"chance_to_win": win, "ci_low": lo, "ci_high": hi}}}]}}}


WIN = results(200, 20, 200, 60, 0.999, 0.8, 3.1)
LOSE = results(200, 60, 200, 20, 0.001, -0.8, -0.4)
UNSURE = results(200, 30, 200, 36, 0.80, -0.2, 0.6)


class FakeAPI:
    def __init__(self, res, exp=EXP):
        self.res, self.exp, self.posted = res, copy.deepcopy(exp), []

    def experiment(self, env, i):
        return self.exp

    def results(self, env, i):
        return self.res

    def post(self, path, body=None):
        self.posted.append((path, body))
        self.exp.update(status="completed", rollout_decision=body["rollout_decision"],
                        rollout_variant=body.get("rollout_variant"), ended_at="now")
        return self.exp


def test_winner_ships_that_variant_by_option_id():
    api = FakeAPI(WIN)
    sp = plan_ship(api, "env", 2)
    assert sp.allowed and sp.decision == "variant" and sp.variant_key == "variant_b"
    assert sp.body() == {"rollout_decision": "variant", "rollout_variant": 77}
    res = ship(api, "env", sp, verify=None)
    assert api.posted == [("environments/env/experiments/2/complete/", {"rollout_decision": "variant", "rollout_variant": 77})]
    assert res["status"] == "completed" and res["rollout_variant"] == 77


def test_loser_keeps_control():
    sp = plan_ship(FakeAPI(LOSE), "env", 2)
    assert sp.allowed and sp.body() == {"rollout_decision": "control"}


def test_inconclusive_refuses_and_ship_raises():
    api = FakeAPI(UNSURE)
    sp = plan_ship(api, "env", 2)
    assert not sp.allowed and sp.decision is None
    with pytest.raises(RuntimeError):
        ship(api, "env", sp)
    assert api.posted == []


def test_override_needs_a_real_reason_and_is_marked():
    assert not plan_ship(FakeAPI(UNSURE), "env", 2, "variant_b", "ok").allowed
    sp = plan_ship(FakeAPI(UNSURE), "env", 2, "variant_b", "Legal requires the new copy regardless of results")
    assert sp.allowed and sp.overridden and sp.body()["rollout_variant"] == 77


def test_cannot_override_against_the_evidence():
    sp = plan_ship(FakeAPI(WIN), "env", 2, "control", "I just prefer the old one, sorry team")
    assert not sp.allowed and "evidence says ship 'variant_b'" in sp.reason


def test_unknown_variant_and_completed_experiment():
    with pytest.raises(ValueError):
        plan_ship(FakeAPI(UNSURE), "env", 2, "variant_z", "a long enough reason here")
    done = {**EXP, "status": "completed", "rollout_decision": "control"}
    assert not plan_ship(FakeAPI(WIN, done), "env", 2).allowed


def test_too_little_data_never_ships_even_with_high_win_probability():
    tiny = results(20, 2, 20, 9, 0.99, 0.5, 4.0)
    assert not plan_ship(FakeAPI(tiny), "env", 2).allowed


def test_expected_value_after_ship():
    from preflight.ship import expected_value
    assert expected_value(plan_ship(FakeAPI(WIN), "env", 2)) == "treatment"
    exp = {**EXP, "feature": {**EXP["feature"], "initial_value": "control"}}
    assert expected_value(plan_ship(FakeAPI(LOSE, exp), "env", 2)) == "control"
