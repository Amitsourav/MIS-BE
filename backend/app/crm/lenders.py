"""What a partner sees as the lender: the real bank, never the route or the
aggregator FMC sent the file through.

The CRM records the ROUTE a file went by ("UC PNB" = PNB via UniCred,
"Credila Hemant" = Credila via one DSA, "Axis Maharashtra" = one Axis
channel). Partners must only see the bank ("PNB", "Credila", "Axis"). A
file still parked on an aggregator ("UniCred", "Nomad Normal", ...) has no
known bank yet, so it shows no lender at all (None -> "—").

Like `stage_map`, this is the one place to edit when the CRM gains a route.
Unknown names pass through unchanged (a plain lender such as "SBI").
"""
from __future__ import annotations

# Exact route -> bank (lowercased keys). None = an aggregator: hide it.
_ROUTES: dict[str, str | None] = {
    "uc pnb": "PNB",
    "uc axis": "Axis",
    "axis direct (uc code)": "Axis",
    "axis maharashtra": "Axis",
    "credila hemant": "Credila",
    "unicred credila domestic": "Credila",
    "unicred": None,
    "unicred normal": None,
    "nomad": None,
    "nomad normal": None,
    "nomad us": None,
}

# Routes FMC adds later still follow the family naming: "UC <bank>" is a
# bank via UniCred; anything else under UniCred / Nomad has no named bank.
_VIA_UNICRED = "uc "
_AGGREGATOR_FAMILIES = ("unicred", "nomad")


def lender_display(bank_name: str | None) -> str | None:
    """The bank to show a partner for a CRM lender/route name, or None."""
    if bank_name is None or not bank_name.strip():
        return None
    name = " ".join(bank_name.split())
    key = name.lower()
    if key in _ROUTES:
        return _ROUTES[key]
    if key.startswith(_VIA_UNICRED):
        return name[len(_VIA_UNICRED):].strip() or None
    if key.split(" ", 1)[0] in _AGGREGATOR_FAMILIES:
        return None
    return name
