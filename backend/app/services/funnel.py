"""Funnel-achievement logic: given a lead, has it *ever reached* each milestone.

Uses derived timestamps where available and falls back to the linear funnel rank
of the current canonical stage. Side states (dnp/lost/opportunity) have no linear
rank, so for those we rely on the timestamps captured during sync.
"""
from __future__ import annotations

from app.models.enums import FUNNEL_RANK, CanonicalStage
from app.models.lead import MisLead


def _rank(stage: CanonicalStage) -> int:
    return FUNNEL_RANK.get(stage, -1)


def reached_contacted(lead: MisLead) -> bool:
    return lead.contacted_at is not None or _rank(lead.canonical_stage) >= 1


def reached_connected(lead: MisLead) -> bool:
    # No connected_at column; if qualified, it was necessarily connected.
    return lead.qualified_at is not None or _rank(lead.canonical_stage) >= 2


def reached_qualified(lead: MisLead) -> bool:
    return lead.qualified_at is not None or _rank(lead.canonical_stage) >= 3


def reached_converted(lead: MisLead) -> bool:
    return (
        lead.converted_at is not None
        or lead.canonical_stage == CanonicalStage.CONVERTED
    )
