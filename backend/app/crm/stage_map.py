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
    "docs_pending": CanonicalStage.IN_PROCESS,
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


# The CRM's own stage names, in the CRM's column order (copied from CRM-UI
# src/lib/constants.ts FMC_STAGE_CONFIG / ADMITVERSE_STAGE_CONFIG). Providers
# see exactly these labels, so the portal matches what the CRM team sees.
# Legacy / retired stages stay listed so old leads still get a real name.
_FMC_LABELS: dict[str, str] = {
    "created": "Created",
    "contacted": "Contacted",
    "dnp": "DNP",
    "qualified": "Qualified",
    "processing": "Processing",
    "docs_pending": "Docs Pending",
    "logged_in": "Logged In",
    "sanctioned": "Sanctioned",
    "pf_paid": "PF Paid",
    "disbursed": "Disbursed",
    "opportunity": "Opportunity",
    "lost": "Lost",
    # legacy
    "lead": "New Lead",
    "called": "Called",
    "connected": "Connected",
    "qualified_lead": "Qualified (legacy)",
    "won": "Won",
}

_AV_LABELS: dict[str, str] = {
    "created": "Created",
    "dnp_pre_qualified": "DNP",
    "qualified": "Hot",
    "warm": "Warm",
    "cold": "Cold",
    "opportunity": "Opportunity (Future)",
    "application_done": "Application Done",
    "deposit_paid": "Deposit Paid",
    "enrolled": "Enrolled",
    "lost": "Lost",
    # retired columns, still on older leads
    "contacted": "Contacted",
    "connected": "Connected",
    "dnp_post_qualified": "DNP (Post-Q)",
    "processing": "Processing",
    "important": "Important",
    "partial_docs_collected": "Partial Docs",
    "docs_collected": "Docs Collected",
    "conditional_draft": "Conditional Draft",
    "ucol": "UCOL",
    "cas_received": "CAS Received",
    "visa_applied": "Visa Applied",
}

_LABELS: dict[Brand, dict[str, str]] = {Brand.FMC: _FMC_LABELS, Brand.AV: _AV_LABELS}


def crm_stage_value(raw_stage: str | None) -> str | None:
    """The CRM stage as stored (lowercased, trimmed); None when the lead has none."""
    if raw_stage is None or not str(raw_stage).strip():
        return None
    return str(raw_stage).strip().lower()


def crm_stage_label(brand: Brand, raw_stage: str | None) -> str | None:
    """The label the CRM shows for this stage. A stage the CRM adds later
    still gets a readable name ("new_stage" -> "New Stage")."""
    value = crm_stage_value(raw_stage)
    if value is None:
        return None
    return _LABELS[brand].get(value) or value.replace("_", " ").title()


def crm_stage_order(brand: Brand, raw_stage: str | None) -> int:
    """Position in the CRM pipeline (unknown stages sort last)."""
    order = list(_LABELS[brand])
    value = crm_stage_value(raw_stage)
    return order.index(value) if value in order else len(order)


def raw_stages_for(brand: Brand, canonical: CanonicalStage) -> list[str]:
    """Every raw CRM stage string (lowercased) that maps to `canonical`.
    Used to translate canonical filters / milestones into SQL."""
    return sorted(k for k, v in _MAPS[brand].items() if v == canonical)


def known_raw_stages(brand: Brand) -> list[str]:
    """All raw stages with an explicit mapping (anything else is DELIVERED)."""
    return sorted(_MAPS[brand])


def map_stage(brand: Brand, raw_stage: str | None) -> CanonicalStage:
    """Map a raw CRM stage to canonical. Unknown / null raw stages fall back to
    DELIVERED (every lead is at least 'delivered')."""
    if not raw_stage:
        return CanonicalStage.DELIVERED
    return _MAPS[brand].get(raw_stage.strip().lower(), CanonicalStage.DELIVERED)


# --- CRM schema assumptions (override here if the real columns differ) ---
# The live CRM queries reference these. Adjust per brand without touching SQL logic.
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
    lead_deleted = "is_deleted"  # soft-deleted leads are excluded everywhere

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
