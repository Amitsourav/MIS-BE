"""Seed helpers — create the first admin and (optionally) default targets.

Usage:
    # Super-admin (manages BOTH companies) — omit the brand:
    python -m scripts.seed admin you@example.com 'a-strong-password'
    # Company admin (one company only) — pass fmc or av:
    python -m scripts.seed admin fmc-admin@example.com 'a-strong-password' fmc
    python -m scripts.seed admin av-admin@example.com  'a-strong-password' av
    python -m scripts.seed targets        # write default scorecard bands/weights
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import AdminUser, Target
from app.models.enums import Brand
from app.services.scorecard import DEFAULTS, DEFAULT_GRADE_BANDS


async def create_admin(email: str, password: str, brand: str | None = None) -> None:
    email = email.lower().strip()
    brand_enum = Brand(brand.lower()) if brand else None
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if existing:
            print(f"admin {email} already exists")
            return
        db.add(
            AdminUser(
                email=email,
                password_hash=hash_password(password),
                brand=brand_enum,
            )
        )
        await db.commit()
        scope = brand_enum.value if brand_enum else "super-admin (both companies)"
        print(f"created admin {email} — scope: {scope}")


async def seed_targets() -> None:
    """Write the default weights / bands / grade thresholds into `targets` so the
    admin has a starting point to override (NOT industry benchmarks)."""
    async with SessionLocal() as db:
        existing = {
            k for (k,) in (await db.execute(select(Target.metric_key))).all()
        }
        added = 0
        for crit, (weight, ok, good, _inverse) in DEFAULTS.items():
            for key, val in (
                (f"weight.{crit}", weight),
                (f"band.{crit}.ok", ok),
                (f"band.{crit}.good", good),
            ):
                if key not in existing:
                    db.add(Target(brand=None, metric_key=key, target_value=val))
                    added += 1
        for grade, thresh in DEFAULT_GRADE_BANDS.items():
            key = f"grade.{grade}"
            if key not in existing:
                db.add(Target(brand=None, metric_key=key, target_value=thresh))
                added += 1
        await db.commit()
        print(f"seeded {added} default target rows")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "admin" and len(sys.argv) in (4, 5):
        brand = sys.argv[4] if len(sys.argv) == 5 else None
        asyncio.run(create_admin(sys.argv[2], sys.argv[3], brand))
    elif cmd == "targets":
        asyncio.run(seed_targets())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
