"""Honest verdicts for a running experiment.

Thresholds come from the Flagsmith statistics docs (fetched 2026-11-21):
  - an arm is "collecting data" until it has >= 50 identities and, for conversion
    metrics, >= 5 conversions
  - chance-to-beat-control > 95% = winning, < 5% = losing, otherwise inconclusive
  - a 95% credible interval that crosses zero is inconclusive
  - sample-ratio mismatch (SRM) is checked from 100 identities; warn at p < 0.001
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

MIN_IDENTITIES = 50
MIN_CONVERSIONS = 5
WIN = 0.95
LOSE = 0.05
SRM_MIN_TOTAL = 100
SRM_P = 0.001


@dataclass
class Arm:
    name: str
    identities: int
    conversions: int | None = None
    expected_share: float | None = None  # 0-1, from the experiment's allocation
    win_probability: float | None = None  # chance to beat control, 0-1 (None for control)
    ci_low: float | None = None  # relative lift credible interval, e.g. -0.14
    ci_high: float | None = None
    is_control: bool = False


@dataclass
class Verdict:
    status: str  # collecting | srm | winner | loser | inconclusive
    headline: str
    reasons: list[str] = field(default_factory=list)
    ship: str | None = None  # arm name it is safe to ship, if any

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _chi2_sf(x: float, df: int) -> float:
    """Survival function of the chi-square distribution (regularised upper gamma)."""
    if x <= 0:
        return 1.0
    a, z = df / 2.0, x / 2.0
    if z < a + 1:  # series for the lower gamma, then complement
        term = total = 1.0 / a
        n = 1
        while n < 500:
            term *= z / (a + n)
            total += term
            if term < total * 1e-14:
                break
            n += 1
        lower = total * math.exp(-z + a * math.log(z) - math.lgamma(a))
        return max(0.0, 1.0 - lower)
    # continued fraction for the upper gamma (Lentz)
    tiny = 1e-300
    b = z + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-14:
            break
    return h * math.exp(-z + a * math.log(z) - math.lgamma(a))


def srm_p_value(arms: list[Arm]) -> float | None:
    total = sum(a.identities for a in arms)
    if total < SRM_MIN_TOTAL or any(a.expected_share is None for a in arms):
        return None
    shares = sum(a.expected_share for a in arms)  # type: ignore[misc]
    chi = 0.0
    for a in arms:
        exp = total * a.expected_share / shares  # type: ignore[operator]
        if exp > 0:
            chi += (a.identities - exp) ** 2 / exp
    return _chi2_sf(chi, len(arms) - 1)


def judge(arms: list[Arm], conversion_metric: bool = True) -> Verdict:
    if not arms or not any(a.is_control for a in arms):
        return Verdict("collecting", "No control arm data yet.", ["Flagsmith has not returned results for a control arm."])

    short = []
    for a in arms:
        if a.identities < MIN_IDENTITIES:
            short.append(f"{a.name}: {a.identities}/{MIN_IDENTITIES} identities")
        if conversion_metric and (a.conversions or 0) < MIN_CONVERSIONS:
            short.append(f"{a.name}: {a.conversions or 0}/{MIN_CONVERSIONS} conversions")

    p = srm_p_value(arms)
    if p is not None and p < SRM_P:
        return Verdict("srm", "Traffic split is broken (sample-ratio mismatch). Do not trust these results.",
                       [f"SRM p-value {p:.2e} < {SRM_P}. Identities per arm: "
                        + ", ".join(f"{a.name}={a.identities}" for a in arms),
                        "Usual causes: users not identified consistently, a variant without a key, or bots/caching on one arm."])

    if short:
        return Verdict("collecting", "Not enough data yet. Do not call a winner.", short)

    treatments = [a for a in arms if not a.is_control]
    best = max(treatments, key=lambda a: a.win_probability or 0)
    reasons = []
    for a in treatments:
        wp = a.win_probability
        ci = (f", lift 95% CI [{a.ci_low:+.1%}, {a.ci_high:+.1%}]"
              if a.ci_low is not None and a.ci_high is not None else "")
        reasons.append(f"{a.name}: chance to beat control {wp:.1%}{ci}" if wp is not None else f"{a.name}: no win probability yet")

    wp = best.win_probability
    crosses_zero = best.ci_low is not None and best.ci_high is not None and best.ci_low < 0 < best.ci_high
    if wp is not None and wp > WIN and not crosses_zero:
        return Verdict("winner", f"{best.name} wins. Safe to roll it out on the same flag.", reasons, ship=best.name)
    if all((a.win_probability is not None and a.win_probability < LOSE) for a in treatments):
        return Verdict("loser", "Control wins. Keep control and remove the variation.", reasons,
                       ship=next(a.name for a in arms if a.is_control))
    if crosses_zero:
        reasons.append("The credible interval crosses zero, so the direction of the effect is not settled.")
    return Verdict("inconclusive", "No clear winner yet. Keep the experiment running or accept no difference.", reasons)
