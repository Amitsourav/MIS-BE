"""SQLAlchemy models for the MIS database.

Importing this package registers every model on `Base.metadata` so Alembic's
autogenerate and `create_all` (tests) see the full schema.
"""
from app.models.enums import Brand, CanonicalStage
from app.models.lead import MisLead
from app.models.metrics import ProviderDailyMetric
from app.models.payout import MisPayout
from app.models.provider import (
    AdminUser,
    Provider,
    ProviderSource,
    ProviderUser,
)
from app.models.sync import SyncState
from app.models.target import Target

__all__ = [
    "Brand",
    "CanonicalStage",
    "Provider",
    "ProviderUser",
    "AdminUser",
    "ProviderSource",
    "MisLead",
    "MisPayout",
    "ProviderDailyMetric",
    "SyncState",
    "Target",
]
