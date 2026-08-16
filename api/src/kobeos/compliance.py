"""Pre-dial compliance checks.

Every outbound call passes through :func:`check_dialable` before the dialler
places it. The checks are deliberately fail-closed: anything we cannot prove is
allowed is refused, because the cost of a wrongly-blocked call is a retry while
the cost of a wrongly-placed one is a regulatory complaint.

Nothing here is legal advice, and the thresholds are defaults rather than a
jurisdiction survey — but the *shape* (DNC, consent, local calling window) is the
part that is expensive to add later, so it exists now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ConsentRecord, Contact, DoNotCall

# US TCPA restricts telephone solicitation to 08:00-21:00 in the *called party's*
# local time. Other jurisdictions differ; override per tenant when that matters.
DEFAULT_WINDOW_START = time(8, 0)
DEFAULT_WINDOW_END = time(21, 0)


class Reason(str):
    pass


DNC_LISTED = "dnc_listed"
NO_CONSENT = "no_consent"
CONSENT_REVOKED = "consent_revoked"
OUTSIDE_WINDOW = "outside_calling_window"
UNKNOWN_TIMEZONE = "unknown_timezone"
ALLOWED = "allowed"


@dataclass(frozen=True)
class Decision:
    """Outcome of a pre-dial check."""

    allowed: bool
    reason: str
    detail: str | None = None

    def __bool__(self) -> bool:  # let callers write `if decision:`
        return self.allowed


def local_time_for(contact: Contact | None, now: datetime | None = None) -> tuple[datetime, str] | None:
    """Return the called party's local time, or None if the zone is unusable."""
    if contact is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        # A naive "now" would be silently treated as local time by astimezone and
        # could shift the window by hours.
        now = now.replace(tzinfo=timezone.utc)
    zone_name = contact.timezone_name or "UTC"
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return now.astimezone(zone), zone_name


def within_calling_window(
    contact: Contact | None,
    now: datetime | None = None,
    start: time = DEFAULT_WINDOW_START,
    end: time = DEFAULT_WINDOW_END,
) -> Decision:
    resolved = local_time_for(contact, now)
    if resolved is None:
        # No contact record, or a timezone we can't resolve: we cannot show the
        # call was inside legal hours, so we don't place it.
        return Decision(False, UNKNOWN_TIMEZONE, "no resolvable timezone for contact")
    local, zone_name = resolved
    if start <= local.time() < end:
        return Decision(True, ALLOWED)
    return Decision(
        False,
        OUTSIDE_WINDOW,
        f"local time {local.strftime('%H:%M')} in {zone_name} is outside {start}-{end}",
    )


def is_dnc_listed(session: Session, tenant_id: str, e164: str) -> bool:
    stmt = select(DoNotCall).where(
        DoNotCall.tenant_id == tenant_id, DoNotCall.e164 == e164
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def has_valid_consent(session: Session, tenant_id: str, e164: str) -> Decision:
    """Most recent non-revoked consent wins; revocation is never overridden."""
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.tenant_id == tenant_id, ConsentRecord.e164 == e164)
        .order_by(ConsentRecord.captured_at.desc())
    )
    records = list(session.execute(stmt).scalars())
    if not records:
        return Decision(False, NO_CONSENT, "no consent record on file")
    # A revocation anywhere in the history blocks the number: consent given
    # earlier does not resurrect after the contact opts out.
    if any(r.revoked_at is not None for r in records):
        return Decision(False, CONSENT_REVOKED, "consent was revoked")
    return Decision(True, ALLOWED)


def check_dialable(
    session: Session,
    *,
    tenant_id: str,
    e164: str,
    contact: Contact | None = None,
    now: datetime | None = None,
    require_consent: bool = True,
) -> Decision:
    """Full pre-dial gate. Order matters: cheapest and most absolute check first."""
    if is_dnc_listed(session, tenant_id, e164):
        return Decision(False, DNC_LISTED, "number is on the tenant do-not-call list")

    if require_consent:
        consent = has_valid_consent(session, tenant_id, e164)
        if not consent.allowed:
            return consent

    return within_calling_window(contact, now=now)
