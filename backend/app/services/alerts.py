"""Re-match and alert generation for monitoring-detected changes (002 US2,
FR-ALERT-1..6; design in ADR-0007).

For each stored change (`ChangeEvent`) this selects the affected owners,
re-matches them through the SAME `match_scholarship` the on-demand route uses
(never a reimplementation), decides deterministically whether the outcome is
alert-worthy, and dispatches through pluggable `NotificationChannel`s.

No LLM is called from this module. Hard constraints are computed
deterministically inside `match_scholarship`; the soft-preference LLM call that
lives there is accepted (ADR-0007) and is why an alert is never triggered by a
verdict change alone.
"""

import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Literal, Mapping, Protocol

from sqlalchemy.orm import Session

from app.core.config import settings
from app.data.repositories import alert_repo, match_repo, profile_repo, scholarship_repo
from app.models.alert import Alert
from app.models.user import User
from app.schemas.match import MatchVerdict
from app.services.matching import match_scholarship

logger = logging.getLogger(__name__)

__all__ = [
    "AlertContext",
    "ChangeEvent",
    "EmailChannel",
    "InAppChannel",
    "NotificationChannel",
    "ProcessSummary",
    "RematchBudget",
    "decide_alert",
    "default_channels",
    "dispatch_alert",
    "process_changes",
]

# D1: for an owner with no prior match, only these verdicts are worth an alert.
_FAVORABLE = frozenset({"eligible", "likely", "possibly"})
_SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class ChangeEvent:
    """One stored change: a brand-new scholarship, or an update to the
    monitored fields (`fields`, from `services/listing_diff.py`)."""

    scholarship_id: uuid.UUID | None
    kind: Literal["new", "updated"]
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertContext:
    """What a channel needs beyond the alert row. Scholarship id and name only
    (no link / base URL — there is no frontend to link to yet)."""

    recipient_email: str | None
    scholarship_id: uuid.UUID
    scholarship_name: str


class NotificationChannel(Protocol):
    """Same Protocol-plus-implementations shape as `StorageBackend`. A channel
    records its own delivery outcome on the alert row's columns and returns
    whether delivery succeeded. It must not raise."""

    name: str

    def send(self, alert: Alert, context: AlertContext) -> bool: ...


class InAppChannel:
    name = "in_app"

    def send(self, alert: Alert, context: AlertContext) -> bool:
        # In-app "delivery" is the row existing and being listable via GET /alerts.
        alert.delivered_in_app_at = datetime.now(timezone.utc)
        return True


def _email_body(alert: Alert, context: AlertContext) -> str:
    summary = alert.change_summary or {}
    headline = {
        "new_match": "A new scholarship may match your profile.",
        "eligibility_changed": "A scholarship you were matched against changed in a way that affects your eligibility.",
        "details_changed": "A scholarship you applied for has changed.",
    }.get(summary.get("kind"), "A scholarship relevant to you has changed.")
    lines = [headline, "", f"Scholarship: {context.scholarship_name}", f"Scholarship ID: {context.scholarship_id}"]
    if summary.get("fields"):
        lines.append("What changed: " + ", ".join(summary["fields"]))
    if summary.get("hard_constraints_changed"):
        lines.append("Eligibility criteria affected: " + ", ".join(summary["hard_constraints_changed"]))
    if summary.get("verdict"):
        lines.append(f"Current eligibility verdict: {summary['verdict']}")
    return "\n".join(lines) + "\n"


class EmailChannel:
    """stdlib `smtplib` (no new dependency). Every failure — including an unset
    `SMTP_HOST` — is captured in `email_error`; a later successful send on the
    same row clears it."""

    name = "email"

    def send(self, alert: Alert, context: AlertContext) -> bool:
        if not settings.smtp_host:
            alert.email_error = "SMTP_HOST is not configured; email alert was not sent"
            return False
        if not context.recipient_email:
            alert.email_error = "recipient has no email address"
            return False
        if not getattr(settings, "alerts_from_email", None):
            alert.email_error = "ALERTS_FROM_EMAIL is not configured; email alert was not sent"
            return False

        message = EmailMessage()
        message["Subject"] = f"Scholarship alert: {context.scholarship_name}"
        message["From"] = settings.alerts_from_email
        message["To"] = context.recipient_email
        message.set_content(_email_body(alert, context))
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password or "")
                smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001 - delivery failure is data, never an exception
            alert.email_error = f"{type(exc).__name__}: {exc}"[:500]
            return False
        alert.delivered_email_at = datetime.now(timezone.utc)
        alert.email_error = None
        return True


def default_channels() -> dict[str, NotificationChannel]:
    """Registration point: adding a channel is one entry here (OD-8)."""
    return {"in_app": InAppChannel(), "email": EmailChannel()}


class RematchBudget:
    """Run-wide cap on owner x event re-matches (ADR-0007 / D4)."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self.exhaustion_logged = False

    def try_consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass
class ProcessSummary:
    alerts_created: int = 0
    rematched: int = 0
    skipped_duplicates: int = 0
    errors: int = 0
    cap_hit: bool = False


# --- decision rule (ADR-0007, D1 + D3) -----------------------------------------------


def _hard_pairs(items) -> frozenset[tuple[str, str]]:
    pairs = set()
    for item in items:
        criterion = item["criterion"] if isinstance(item, dict) else item.criterion
        result = item["result"] if isinstance(item, dict) else item.result
        pairs.add((criterion, getattr(result, "value", result)))
    return frozenset(pairs)


def decide_alert(event: ChangeEvent, prior, verdict: MatchVerdict) -> dict | None:
    """`change_summary` if this outcome is alert-worthy, else None.

    Prior match exists: alert only when the hard-constraint result set changed
    (deterministic). A verdict change with an unchanged set is soft-preference
    drift and never alerts; both verdicts are recorded as information.
    No prior match: alert only on a favorable verdict.
    """
    new_verdict = verdict.eligibility_verdict.value
    base = {"fields": list(event.fields), "verdict": new_verdict}

    if prior is not None:
        before, after = _hard_pairs(prior.hard_constraints), _hard_pairs(verdict.hard_constraints)
        if before == after:
            return None
        previous = getattr(prior.eligibility_verdict, "value", prior.eligibility_verdict)
        differing = sorted({criterion for criterion, _ in before ^ after})
        return {
            "kind": "eligibility_changed",
            **base,
            "previous_verdict": previous,
            "hard_constraints_changed": differing,
        }

    if new_verdict not in _FAVORABLE:
        return None
    return {"kind": "new_match" if event.kind == "new" else "details_changed", **base}


# --- dispatch ---------------------------------------------------------------------------


def dispatch_alert(
    db: Session, alert: Alert, context: AlertContext, channels: Mapping[str, NotificationChannel]
) -> Alert:
    """Send through every requested channel and persist the outcome on the SAME
    row — also the retry path (a later call updates, never duplicates)."""
    for name in alert.channels_requested:
        channel = channels.get(name)
        if channel is None:
            logger.warning("alert %s requested unknown channel %r", alert.id, name)
            continue
        try:
            channel.send(alert, context)
        except Exception:  # noqa: BLE001 - a channel must not break generation
            logger.exception("channel %r raised for alert %s", name, alert.id)
    return alert_repo.save(db, alert)


# --- owner selection + generation ---------------------------------------------------------


def _owners_for(db: Session, event: ChangeEvent) -> list[uuid.UUID]:
    if event.kind == "new":
        return alert_repo.profile_user_ids(db)
    return alert_repo.user_ids_with_match_or_application(db, event.scholarship_id)


def _enabled(preferences, channel_name: str) -> bool:
    # A channel with no preference column (a future one) defaults to enabled.
    return bool(getattr(preferences, f"{channel_name}_enabled", True))


def _process_owner(
    db: Session,
    run_id: uuid.UUID,
    event: ChangeEvent,
    scholarship,
    user_id: uuid.UUID,
    channels: Mapping[str, NotificationChannel],
) -> bool:
    """Re-match one owner; returns True when an alert row was created."""
    profile = profile_repo.get_by_user_id(db, user_id)
    if profile is None:
        return False

    # Read the baseline BEFORE re-matching: match_scholarship appends a new row.
    prior = match_repo.latest_for_user_and_scholarship(db, user_id, scholarship.id)
    verdict = match_scholarship(db, user_id, profile, scholarship)

    summary = decide_alert(event, prior, verdict)
    if summary is None:
        return False

    preferences = alert_repo.get_preferences(db, user_id)
    requested = [name for name in channels if _enabled(preferences, name)]
    if not requested:  # FR-ALERT-5: re-match already ran; only delivery is suppressed
        return False

    latest = match_repo.latest_for_user_and_scholarship(db, user_id, scholarship.id)
    alert = alert_repo.create(
        db,
        Alert(
            user_id=user_id,
            scholarship_id=scholarship.id,
            match_id=latest.id if latest is not None else None,
            monitoring_run_id=run_id,
            change_summary={**summary, "scholarship_name": scholarship.name},
            channels_requested=requested,
        ),
    )
    user = db.get(User, user_id)
    context = AlertContext(
        recipient_email=user.email if user is not None else None,
        scholarship_id=scholarship.id,
        scholarship_name=scholarship.name,
    )
    dispatch_alert(db, alert, context, channels)
    return True


def process_changes(
    db: Session,
    run_id: uuid.UUID,
    events: list[ChangeEvent],
    *,
    budget: RematchBudget,
    channels: Mapping[str, NotificationChannel] | None = None,
) -> ProcessSummary:
    """Re-match and alert for a source's stored changes. A failure for one
    owner is logged and isolated; hitting the budget logs once and stops."""
    channels = channels if channels is not None else default_channels()
    summary = ProcessSummary()

    for event in events:
        scholarship = scholarship_repo.get_by_id(db, event.scholarship_id)
        if scholarship is None:
            continue
        for user_id in _owners_for(db, event):
            # D6: application-level duplicate check on (user, scholarship, run).
            # NOT race-safe across concurrent runs; adequate only for the
            # current single-run-per-source model (see ADR-0007).
            if alert_repo.exists_for_run(db, user_id, scholarship.id, run_id):
                summary.skipped_duplicates += 1
                continue
            if not budget.try_consume():
                summary.cap_hit = True
                if not budget.exhaustion_logged:
                    budget.exhaustion_logged = True
                    logger.warning(
                        "alert re-match cap (%d owner x event pairs) reached; no further alerts this run",
                        budget.limit,
                    )
                return summary
            summary.rematched += 1
            try:
                if _process_owner(db, run_id, event, scholarship, user_id, channels):
                    summary.alerts_created += 1
            except Exception:  # noqa: BLE001 - one owner must not stop the rest
                logger.exception("re-match/alert failed for user %s, scholarship %s", user_id, scholarship.id)
                db.rollback()
                summary.errors += 1
    return summary
