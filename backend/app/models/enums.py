"""Domain enums shared by models, schemas, and the sync layer."""
from __future__ import annotations

import enum


class Brand(str, enum.Enum):
    FMC = "fmc"
    AV = "av"


class CanonicalStage(str, enum.Enum):
    DELIVERED = "delivered"
    CONTACTED = "contacted"
    CONNECTED = "connected"
    QUALIFIED = "qualified"
    IN_PROCESS = "in_process"
    CONVERTED = "converted"
    OPPORTUNITY = "opportunity"
    DNP = "dnp"
    LOST = "lost"


# Linear funnel rank. Side states (opportunity, dnp, lost) are intentionally
# absent — they are off the linear funnel and handled via derived timestamps.
FUNNEL_RANK: dict[CanonicalStage, int] = {
    CanonicalStage.DELIVERED: 0,
    CanonicalStage.CONTACTED: 1,
    CanonicalStage.CONNECTED: 2,
    CanonicalStage.QUALIFIED: 3,
    CanonicalStage.IN_PROCESS: 4,
    CanonicalStage.CONVERTED: 5,
}

# Ordered list for funnel display (delivered → … → converted).
FUNNEL_ORDER: list[CanonicalStage] = [
    CanonicalStage.DELIVERED,
    CanonicalStage.CONTACTED,
    CanonicalStage.CONNECTED,
    CanonicalStage.QUALIFIED,
    CanonicalStage.IN_PROCESS,
    CanonicalStage.CONVERTED,
]
