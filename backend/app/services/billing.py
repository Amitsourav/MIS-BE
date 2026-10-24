"""Partner billing details (checked once at setup) and the arithmetic of the
invoices the MIS makes from them: numbering, GST split, amount in words."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# GST state codes (the first 2 characters of a GSTIN).
STATES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "97": "Other Territory",
}

_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_PIN = re.compile(r"^[1-9][0-9]{5}$")
_PREFIX = re.compile(r"^[A-Z0-9-]{1,12}$")
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CENT = Decimal("0.01")


def gstin_ok(gstin: str) -> bool:
    """Format and check digit of a GSTIN."""
    if not _GSTIN.match(gstin):
        return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        p = _B36.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return _B36[(36 - total % 36) % 36] == gstin[14]


def _clean(v: Any) -> str:
    return " ".join(str(v or "").split())


def check_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise billing details. Raises ValueError with a
    sentence the partner can read."""
    d = {k: _clean(v) for k, v in data.items() if k != "gst_registered"}
    reg = bool(data.get("gst_registered"))
    out: dict[str, Any] = {"gst_registered": reg}

    def need(key: str, label: str, max_len: int) -> str:
        v = d.get(key, "")
        if not v:
            raise ValueError(f"Enter {label}.")
        if len(v) > max_len:
            raise ValueError(f"{label[0].upper()}{label[1:]} can be at most {max_len} characters.")
        return v

    def opt(key: str, max_len: int) -> str | None:
        v = d.get(key, "")
        return v[:max_len] if v else None

    out["legal_name"] = need("legal_name", "your legal name" if not reg else "your company's legal name", 200)
    pan = d.get("pan", "").replace(" ", "").upper()
    if reg:
        gstin = d.get("gstin", "").replace(" ", "").upper()
        if not gstin:
            raise ValueError("Enter your GSTIN.")
        if not gstin_ok(gstin):
            raise ValueError("That GSTIN isn't valid. Check it on your GST certificate.")
        if gstin[:2] not in STATES:
            raise ValueError("That GSTIN's state code isn't a known state.")
        pan = pan or gstin[2:12]
        if pan != gstin[2:12]:
            raise ValueError("The PAN doesn't match the GSTIN (characters 3 to 12).")
        out["gstin"] = gstin
    else:
        out["gstin"] = None
    if not _PAN.match(pan):
        raise ValueError("Enter a valid PAN, like ABCDE1234F.")
    out["pan"] = pan

    out["address_line1"] = need("address_line1", "your address", 200)
    out["address_line2"] = opt("address_line2", 200)
    out["city"] = need("city", "your city", 100)
    state = d.get("state_code", "")
    if state not in STATES:
        raise ValueError("Pick your state.")
    if reg and out["gstin"][:2] != state:
        raise ValueError(f"Your GSTIN is registered in {STATES[out['gstin'][:2]]}. Pick that state.")
    out["state_code"] = state
    pin = d.get("pincode", "").replace(" ", "")
    if not _PIN.match(pin):
        raise ValueError("Enter a valid 6-digit PIN code.")
    out["pincode"] = pin

    email = need("email", "an email for invoices", 200).lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Enter a valid email.")
    out["email"] = email
    phone = re.sub(r"[\s-]", "", d.get("phone", ""))
    phone = phone[3:] if phone.startswith("+91") else phone
    if not re.match(r"^[6-9][0-9]{9}$", phone):
        raise ValueError("Enter a valid 10-digit mobile number.")
    out["phone"] = phone

    out["bank_account_name"] = need("bank_account_name", "the bank account holder's name", 200)
    acct = d.get("bank_account_number", "").replace(" ", "")
    if not re.match(r"^[0-9]{9,18}$", acct):
        raise ValueError("Enter a valid bank account number (9 to 18 digits).")
    out["bank_account_number"] = acct
    ifsc = d.get("bank_ifsc", "").replace(" ", "").upper()
    if not _IFSC.match(ifsc):
        raise ValueError("Enter a valid IFSC, like HDFC0001234.")
    out["bank_ifsc"] = ifsc
    out["bank_name"] = need("bank_name", "your bank's name", 100)
    out["bank_branch"] = opt("bank_branch", 100)

    prefix = d.get("invoice_prefix", "").replace(" ", "").upper()
    if not _PREFIX.match(prefix):
        raise ValueError("The invoice prefix can be 1 to 12 letters, digits or dashes, like ALT.")
    out["invoice_prefix"] = prefix
    out["signatory_name"] = opt("signatory_name", 100)
    return out


def financial_year(d: date) -> str:
    """Indian financial year (April to March) as "26-27"."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def next_invoice_number(prefix: str, on: date, existing: list[str]) -> str:
    """PREFIX/26-27/001: one more than the highest used this financial year."""
    head = f"{prefix}/{financial_year(on)}/"
    pat = re.compile(rf"^{re.escape(head)}(\d+)$", re.IGNORECASE)
    used = [int(m.group(1)) for n in existing if (m := pat.match((n or "").strip()))]
    return f"{head}{(max(used) + 1) if used else 1:03d}"


@dataclass
class Tax:
    rate: Decimal  # percent; 0 when not GST-registered
    cgst: Decimal
    sgst: Decimal
    igst: Decimal

    @property
    def total(self) -> Decimal:
        return self.cgst + self.sgst + self.igst


def tax_for(taxable: Decimal, rate: Decimal, supplier_state: str, recipient_state: str | None) -> Tax:
    """GST on the invoice, rounded half up like the CRM's gst_amount. Same
    state: CGST + SGST (half each, adding up exactly); else IGST."""
    gst = (taxable * rate / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
    zero = Decimal("0.00")
    if gst == 0:
        return Tax(rate, zero, zero, zero)
    if recipient_state and supplier_state == recipient_state:
        cgst = (gst / 2).quantize(_CENT, rounding=ROUND_HALF_UP)
        return Tax(rate, cgst, gst - cgst, zero)
    return Tax(rate, zero, zero, gst)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))


def _below_1000(n: int) -> str:
    h, rest = divmod(n, 100)
    parts = [f"{_ONES[h]} Hundred"] if h else []
    if rest:
        parts.append(_below_100(rest))
    return " ".join(parts)


def _indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (10**3, "Thousand")):
        q, n = divmod(n, size)
        if q:
            parts.append(f"{_indian_words(q) if q >= 100 and size == 10**7 else _below_100(q) if q < 100 else _below_1000(q)} {name}")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    """Rupees 1,23,456.50 -> "Rupees One Lakh Twenty Three Thousand Four Hundred
    Fifty Six and Fifty Paise Only"."""
    amount = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    words = f"Rupees {_indian_words(rupees)}"
    if paise:
        words += f" and {_below_100(paise)} Paise"
    return words + " Only"
