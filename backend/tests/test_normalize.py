"""Unit tests for stage mapping, phone validation, and scorecard math."""
from __future__ import annotations

from app.models.enums import Brand, CanonicalStage
from app.services.scorecard import compute_scorecard
from app.crm.normalize import normalize_phone, validate_phone
from app.crm.stage_map import map_stage


def test_stage_mapping_both_brands():
    assert map_stage(Brand.FMC, "disbursed") == CanonicalStage.CONVERTED
    assert map_stage(Brand.AV, "enrolled") == CanonicalStage.CONVERTED
    assert map_stage(Brand.FMC, "sanctioned") == CanonicalStage.IN_PROCESS
    assert map_stage(Brand.AV, "cas_received") == CanonicalStage.IN_PROCESS
    assert map_stage(Brand.AV, "dnp_post_qualified") == CanonicalStage.DNP
    # unknown / null -> delivered
    assert map_stage(Brand.FMC, "something_new") == CanonicalStage.DELIVERED
    assert map_stage(Brand.FMC, None) == CanonicalStage.DELIVERED


def test_phone_normalization_and_validation():
    assert normalize_phone("+91 90000-00001") == "9000000001"
    assert normalize_phone("09000000001") == "9000000001"
    assert normalize_phone("") is None

    assert validate_phone("9000000001") == (False, None)
    assert validate_phone(None)[0] is True
    assert validate_phone("123")[0] is True            # too short
    assert validate_phone("9999999999")[0] is True     # repeated digit


def test_scorecard_grades_high_and_low():
    targets: dict = {}  # use defaults
    strong = compute_scorecard(
        {
            "validity": 0.98,
            "qualification_rate": 0.30,
            "conversion_rate": 0.06,
            "volume_reliability": 0.95,
            "dispute_rate": 0.0,
        },
        targets,
    )
    assert strong.grade == "A"
    assert strong.score_pct == 100.0

    weak = compute_scorecard(
        {
            "validity": 0.50,
            "qualification_rate": 0.01,
            "conversion_rate": 0.0,
            "volume_reliability": 0.1,
            "dispute_rate": 0.5,
        },
        targets,
    )
    assert weak.grade == "F"
    assert weak.score_pct < 50
