"""Provider scorecard — fully configurable weights, rating bands, and grades.

NOTHING here is an industry benchmark. The numbers below are *initial operational
defaults* the company overrides via the `targets` table (admin → /admin/targets).
Convention for `targets.metric_key`:

  weight.<criterion>            -> criterion weight (should sum to 1.0)
  band.<criterion>.ok           -> value earning rating 3
  band.<criterion>.good         -> value earning rating 5
  grade.A / grade.B / grade.C / grade.D  -> score% thresholds

`dispute_rate` is inverse (lower is better) and defaults to 0 until Phase 2.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.target import Target
from app.schemas.metrics import Scorecard

# criterion -> (default weight, default band.ok, default band.good, inverse?)
DEFAULTS: dict[str, tuple[float, float, float, bool]] = {
    "validity": (0.25, 0.80, 0.95, False),
    "qualification_rate": (0.30, 0.10, 0.25, False),
    "conversion_rate": (0.30, 0.02, 0.05, False),
    "volume_reliability": (0.10, 0.50, 0.90, False),
    "dispute_rate": (0.05, 0.05, 0.02, True),  # inverse: good <= 0.02
}

DEFAULT_GRADE_BANDS: dict[str, float] = {"A": 85, "B": 70, "C": 55, "D": 40}


async def load_targets(db: AsyncSession, brand=None) -> dict[str, float]:
    """Flatten the targets table into a metric_key -> value dict.

    Global rows (brand IS NULL) form the base; if a `brand` is given, that
    company's brand-specific rows are layered on top (company overrides global).
    This is what makes each company able to run its own scorecard.
    """
    rows = await db.execute(select(Target.brand, Target.metric_key, Target.target_value))
    base: dict[str, float] = {}
    override: dict[str, float] = {}
    for b, k, v in rows.all():
        if b is None:
            base[k] = float(v)
        elif brand is not None and b == brand:
            override[k] = float(v)
    return {**base, **override}


def _rating(value: float, ok: float, good: float, inverse: bool) -> int:
    if inverse:
        if value <= good:
            return 5
        if value <= ok:
            return 3
        return 1
    if value >= good:
        return 5
    if value >= ok:
        return 3
    return 1


def _grade(score_pct: float, bands: dict[str, float]) -> str:
    if score_pct >= bands.get("A", DEFAULT_GRADE_BANDS["A"]):
        return "A"
    if score_pct >= bands.get("B", DEFAULT_GRADE_BANDS["B"]):
        return "B"
    if score_pct >= bands.get("C", DEFAULT_GRADE_BANDS["C"]):
        return "C"
    if score_pct >= bands.get("D", DEFAULT_GRADE_BANDS["D"]):
        return "D"
    return "F"


def compute_scorecard(values: dict[str, float], targets: dict[str, float]) -> Scorecard:
    """`values` holds the raw criterion measurements (validity, qualification_rate,
    conversion_rate, volume_reliability, dispute_rate). `targets` is the flattened
    config dict from `load_targets`."""
    criteria: dict[str, dict] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    for crit, (d_weight, d_ok, d_good, inverse) in DEFAULTS.items():
        weight = targets.get(f"weight.{crit}", d_weight)
        ok = targets.get(f"band.{crit}.ok", d_ok)
        good = targets.get(f"band.{crit}.good", d_good)
        value = float(values.get(crit, 0.0))
        rating = _rating(value, ok, good, inverse)
        criteria[crit] = {
            "value": round(value, 4),
            "rating": rating,
            "weight": weight,
        }
        weighted_sum += rating * weight
        total_weight += weight

    # Score% = Σ(rating × weight) / 5 × 100, normalized by total weight so that
    # custom weights that don't sum to 1.0 still yield a 0–100 scale.
    score_pct = round((weighted_sum / (5 * total_weight)) * 100, 1) if total_weight else 0.0

    grade_bands = {
        g: targets.get(f"grade.{g}", DEFAULT_GRADE_BANDS[g]) for g in DEFAULT_GRADE_BANDS
    }
    return Scorecard(
        score_pct=score_pct, grade=_grade(score_pct, grade_bands), criteria=criteria
    )
