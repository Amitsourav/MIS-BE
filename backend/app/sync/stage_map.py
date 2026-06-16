"""Per-brand CRM stage → canonical funnel mapping, in ONE config module.

Keep all brand-specific knowledge here so adding/adjusting a brand is a
local change. Verify the raw strings against each CRM's live `lead_stage`
enum before trusting in production (FMC pipeline was revamped May 2026; AV
has a ~17-stage pipeline).
"""
from __future__ import annotations

from app.models.enums import Brand, CanonicalStage

# raw CRM stage string (lowercased) -> canonical stage
_FMC_MAP: dict[str, CanonicalStage] = {
    "contacted": CanonicalStage.CONTACTED,
    # FMC has no explicit "connected" stage; engaged/qualified-eligible leads
    # are treated as connected at qualify time (handled via funnel rank).
    "qualified": CanonicalStage.QUALIFIED,
    "processing": CanonicalStage.IN_PROCESS,
    "logged_in": CanonicalStage.IN_PROCESS,
    "shared_to_bank": CanonicalStage.IN_PROCESS,
    "doc_pending": CanonicalStage.IN_PROCESS,
    "sanctioned": CanonicalStage.IN_PROCESS,
    "pf_paid": CanonicalStage.IN_PROCESS,
    "disbursed": CanonicalStage.CONVERTED,
    "opportunity": CanonicalStage.OPPORTUNITY,
    "dnp": CanonicalStage.DNP,
    "lost": CanonicalStage.LOST,
}

_AV_MAP: dict[str, CanonicalStage] = {
    "contacted": CanonicalStage.CONTACTED,
    "connected": CanonicalStage.CONNECTED,
    "qualified": CanonicalStage.QUALIFIED,
    "processing": CanonicalStage.IN_PROCESS,
    "partial_docs_collected": CanonicalStage.IN_PROCESS,
    "docs_collected": CanonicalStage.IN_PROCESS,
    "application_done": CanonicalStage.IN_PROCESS,
    "conditional_draft": CanonicalStage.IN_PROCESS,
    "ucol": CanonicalStage.IN_PROCESS,
    "deposit_paid": CanonicalStage.IN_PROCESS,
    "cas_received": CanonicalStage.IN_PROCESS,
    "visa_applied": CanonicalStage.IN_PROCESS,
    "enrolled": CanonicalStage.CONVERTED,
    "opportunity": CanonicalStage.OPPORTUNITY,
    "dnp_pre_qualified": CanonicalStage.DNP,
    "dnp_post_qualified": CanonicalStage.DNP,
    "lost": CanonicalStage.LOST,
}

_MAPS: dict[Brand, dict[str, CanonicalStage]] = {
    Brand.FMC: _FMC_MAP,
    Brand.AV: _AV_MAP,
}


def map_stage(brand: Brand, raw_stage: str | None) -> CanonicalStage:
    """Map a raw CRM stage to canonical. Unknown / null raw stages fall back to
    DELIVERED (every lead is at least 'delivered')."""
    if not raw_stage:
        return CanonicalStage.DELIVERED
    return _MAPS[brand].get(raw_stage.strip().lower(), CanonicalStage.DELIVERED)


# --- CRM schema assumptions (override here if the real columns differ) ---
# The sync queries reference these. Adjust per brand without touching SQL logic.
class CrmSchema:
    leads_table = "public.leads"
    lead_sources_table = "public.lead_sources"
    stage_logs_table = "public.lead_stage_logs"

    # leads columns
    lead_id = "id"
    lead_serial = "serial_no"
    lead_name = "full_name"
    lead_phone = "phone"
    lead_source_fk = "lead_source_id"
    lead_stage = "current_stage"
    lead_created = "created_at"
    lead_updated = "updated_at"

    # lead_sources columns
    source_id = "id"
    source_name = "name"

    # lead_stage_logs columns
    log_lead_fk = "lead_id"
    log_stage = "to_stage"
    log_created = "created_at"

    def __init__(self, **overrides):
        # Per-brand column overrides if a CRM ever diverges from the shared shape.
        for key, value in overrides.items():
            setattr(self, key, value)


# Both CRMs run the same software (verified live 2026-06): leads.current_stage
# and lead_stage_logs.to_stage. Override per brand here only if one diverges.
CRM_SCHEMAS: dict[Brand, CrmSchema] = {
    Brand.FMC: CrmSchema(),
    Brand.AV: CrmSchema(),
}
