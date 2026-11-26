import json
import textwrap
from pathlib import Path

from preflight.gates import Arm, _chi2_sf, judge, srm_p_value
from preflight.lint import lint


def write(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def rules(findings):
    return sorted({(f.rule, f.severity) for f in findings})


# ---------------- lint: one test per documented failure mode -----------------

GOOD_JS = """
import flagsmith from '@flagsmith/flagsmith'
await flagsmith.init({ environmentID: 'x', enableEvents: true })
await flagsmith.identify(visitorId)
const flag = flagsmith.getExperimentFlag('show_demo_button')
flagsmith.trackEvent('demo_button_clicked')
await flagsmith.flushEvents()
"""


def test_clean_app_has_no_findings(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS)
    write(tmp_path, "package.json", json.dumps({"dependencies": {"@flagsmith/flagsmith": "^12.6.0"}}))
    assert lint(tmp_path, {"demo_button_clicked"}) == []


def test_pf001_events_not_enabled(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS.replace("enableEvents: true", "cacheFlags: true"))
    assert ("PF001", "error") in rules(lint(tmp_path, {"demo_button_clicked"}))


def test_pf002_flag_read_without_identity(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS.replace("await flagsmith.identify(visitorId)", ""))
    assert ("PF002", "error") in rules(lint(tmp_path, {"demo_button_clicked"}))


def test_pf003_case_mismatch_is_an_error(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS.replace("demo_button_clicked", "Demo_Button_Clicked"))
    found = lint(tmp_path, {"demo_button_clicked"})
    assert any(f.rule == "PF003" and f.severity == "error" and "case" in f.message for f in found)


def test_pf003_metric_event_never_tracked(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS)
    found = lint(tmp_path, {"demo_button_clicked", "signup_completed"})
    assert any(f.rule == "PF003" and "signup_completed" in f.message for f in found)


def test_pf004_node_script_never_flushes(tmp_path):
    write(tmp_path, "server.js", """
        const Flagsmith = require('@flagsmith/nodejs')
        const fs = new Flagsmith({ environmentKey: 'x', enableEvents: true })
        const flag = await fs.getExperimentFlag('show_demo_button', 'user_1')
        fs.trackEvent('demo_button_clicked')
    """)
    assert ("PF004", "warning") in rules(lint(tmp_path, {"demo_button_clicked"}))


def test_pf005_old_and_legacy_sdks(tmp_path):
    write(tmp_path, "package.json", json.dumps({"dependencies": {"@flagsmith/flagsmith": "^11.0.0", "flagsmith-nodejs": "^3.0.0"}}))
    write(tmp_path, "requirements.txt", "flagsmith==5.0.0\n")
    found = [f for f in lint(tmp_path) if f.rule == "PF005"]
    assert len(found) == 3


def test_python_rules(tmp_path):
    write(tmp_path, "job.py", """
        from flagsmith import Flagsmith
        fs = Flagsmith(environment_key='x')
        flag = fs.get_experiment_flag('show_demo_button')
        fs.track_event('demo_button_clicked', identifier='u1')
    """)
    assert {("PF001", "error"), ("PF002", "error")} <= set(rules(lint(tmp_path, {"demo_button_clicked"})))


def test_skips_node_modules(tmp_path):
    write(tmp_path, "node_modules/x/index.js", GOOD_JS.replace("enableEvents: true", ""))
    assert lint(tmp_path) == []


# ---------------- gates ----------------------------------------------------

def test_chi2_matches_known_values():
    assert abs(_chi2_sf(3.841, 1) - 0.05) < 1e-3
    assert abs(_chi2_sf(10.828, 1) - 0.001) < 1e-4
    assert abs(_chi2_sf(5.991, 2) - 0.05) < 1e-3


def test_collecting_until_minimums():
    v = judge([Arm("control", 40, 3, 0.5, is_control=True), Arm("variant_b", 60, 9, 0.5, 0.99, 0.05, 0.4)])
    assert v.status == "collecting" and v.ship is None


def test_winner_needs_95_and_interval_clear_of_zero():
    arms = [Arm("control", 247, 75, 0.5, is_control=True), Arm("variant_b", 255, 89, 0.5, 0.97, 0.02, 0.44)]
    assert judge(arms).status == "winner"
    arms[1].ci_low = -0.14  # same win prob, interval crosses zero
    assert judge(arms).status == "inconclusive"


def test_blog_example_is_inconclusive():
    # Numbers from the WeMakeDevs blog example: 84% chance to win, CI -14.1%..+44.0%
    arms = [Arm("control", 247, 75, 0.5, is_control=True), Arm("variant_b", 255, 89, 0.5, 0.84, -0.141, 0.44)]
    v = judge(arms)
    assert v.status == "inconclusive" and v.ship is None


def test_srm_detected():
    arms = [Arm("control", 400, 40, 0.5, is_control=True), Arm("variant_b", 200, 30, 0.5, 0.99, 0.1, 0.9)]
    assert srm_p_value(arms) < 0.001
    assert judge(arms).status == "srm"


def test_event_names_in_constants_are_resolved(tmp_path):
    write(tmp_path, "src/App.tsx", """
        const FLAG = 'show_demo_button'
        const EVENT = 'demo_button_clicked'
        await flagsmith.init({ environmentID: 'x', enableEvents: true })
        await flagsmith.identify(id)
        flagsmith.getExperimentFlag(FLAG)
        flagsmith.trackEvent(EVENT)
    """)
    assert lint(tmp_path, {"demo_button_clicked"}) == []
    write(tmp_path, "src/App.tsx", (tmp_path / "src/App.tsx").read_text().replace("'demo_button_clicked'", "'Demo_button_clicked'"))
    assert any(f.rule == "PF003" and "case" in f.message for f in lint(tmp_path, {"demo_button_clicked"}))


def test_pf006_self_hosted_events_go_to_cloud(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS.replace("enableEvents: true", "enableEvents: true, api: 'http://localhost:8000/api/v1/'"))
    assert ("PF006", "error") in rules(lint(tmp_path, {"demo_button_clicked"}))
    write(tmp_path, "src/App.tsx", GOOD_JS.replace(
        "enableEvents: true", "enableEvents: true, api: 'http://localhost:8000/api/v1/', eventsApiUrl: 'http://localhost:8000/api/'"))
    assert lint(tmp_path, {"demo_button_clicked"}) == []


def test_pf006_python(tmp_path):
    write(tmp_path, "svc.py", """
        from flagsmith import Flagsmith
        fs = Flagsmith(environment_key='x', api_url='http://flags.internal/api/v1/', enable_events=True)
        fs.get_experiment_flag('show_demo_button', identifier='u1')
    """)
    assert ("PF006", "error") in rules(lint(tmp_path))


def test_cloud_api_url_is_not_pf006(tmp_path):
    write(tmp_path, "src/App.tsx", GOOD_JS.replace("enableEvents: true", "enableEvents: true, api: 'https://edge.api.flagsmith.com/api/v1/'"))
    assert lint(tmp_path, {"demo_button_clicked"}) == []
