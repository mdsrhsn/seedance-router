"""Credential selection and failure handling.

Two rules do most of the work here:

1. Quota does not pool. A job runs entirely on one key, so a key is only eligible
   if it alone can cover the job's estimated cost. Three keys holding 15 each
   cannot between them run a job costing 17.

2. Concurrency is enforced before dispatch, not discovered through 429s. Failover
   is the safety net; the semaphore is the actual mechanism.
"""

from datetime import timedelta

import config
from models import Credential, UsageLog, utcnow


def eligible_credentials(session, estimated_cost):
    """Every credential that could run this job right now."""
    candidates = session.query(Credential).filter(
        Credential.routing_enabled.is_(True),
        Credential.status == "active",
    ).all()

    return [
        c for c in candidates
        if c.is_available and float(c.quota_remaining or 0) >= float(estimated_cost)
    ]


def select_credential(session, estimated_cost, strategy="priority"):
    """Pick the key to send this job to, or None if nothing can take it.

    strategy:
        priority     - strict cascade. Drain the lowest priority number first.
        least_loaded - spread work across keys by current concurrency use.
    """
    candidates = eligible_credentials(session, estimated_cost)
    if not candidates:
        return None

    if strategy == "least_loaded":
        candidates.sort(key=lambda c: (c.load_ratio, c.priority, -float(c.quota_remaining or 0)))
    else:
        candidates.sort(key=lambda c: (c.priority, -float(c.quota_remaining or 0)))

    return candidates[0]


def blocked_reason(session, estimated_cost):
    """Explain why nothing was eligible, so the dashboard can say something useful
    instead of 'no key available'."""
    all_creds = session.query(Credential).all()
    if not all_creds:
        return "No credentials configured yet."

    routable = [c for c in all_creds if c.routing_enabled and c.status == "active"]
    if not routable:
        return "Every credential is disabled, exhausted, or switched off for routing."

    if not any(c.is_available for c in routable):
        return "All active credentials are in cooldown or at their concurrency limit."

    best = max((float(c.quota_remaining or 0) for c in routable), default=0)
    return (
        f"This job needs {float(estimated_cost):.2f} quota on a single key, but the "
        f"largest available balance is {best:.2f}. Quota does not combine across keys — "
        "top up one account."
    )


def mark_dispatched(session, credential, job):
    credential.in_flight = (credential.in_flight or 0) + 1
    credential.quota_remaining = float(credential.quota_remaining or 0) - float(
        job.estimated_cost or 0
    )
    credential.quota_updated_at = utcnow()
    session.add(
        UsageLog(
            credential_id=credential.id,
            job_id=job.id,
            event="submitted",
            amount=job.estimated_cost or 0,
            detail=f"task {job.task_id}",
        )
    )


def release(session, credential, job, event, refund=False):
    """Job left the in-flight state. Optionally give the quota back."""
    if credential:
        credential.in_flight = max((credential.in_flight or 0) - 1, 0)
        if refund:
            credential.quota_remaining = float(credential.quota_remaining or 0) + float(
                job.estimated_cost or 0
            )
    session.add(
        UsageLog(
            credential_id=credential.id if credential else None,
            job_id=job.id,
            event=event,
            amount=0 if refund else (job.estimated_cost or 0),
            detail=job.error or "",
        )
    )


def handle_api_error(session, credential, error):
    """Translate a ModelArk failure into a credential state change.

    Returns True if the job should be retried on a different key.
    """
    code = error.status_code
    credential.last_error = f"{utcnow():%Y-%m-%d %H:%M} — {error.message}"[:500]

    if code == 429:
        credential.status = "cooling"
        credential.cooldown_until = utcnow() + timedelta(
            seconds=config.RATE_LIMIT_COOLDOWN_SECONDS
        )
        return True

    if code in (401, 403):
        credential.status = "disabled"
        credential.routing_enabled = False
        return True

    if code == 402 or "insufficient" in (error.message or "").lower() or "balance" in (
        error.message or ""
    ).lower():
        credential.status = "exhausted"
        credential.quota_remaining = 0
        return True

    if code >= 500:
        credential.cooldown_until = utcnow() + timedelta(
            seconds=config.SERVER_ERROR_COOLDOWN_SECONDS
        )
        return True

    # 4xx that is not about the key — bad prompt, rejected content, bad model ID.
    # Retrying on another key would just burn more quota.
    return False


def revive_cooled_credentials(session):
    """Move keys out of cooling once their cooldown window has passed."""
    now = utcnow()
    cooled = session.query(Credential).filter(Credential.status == "cooling").all()
    for credential in cooled:
        if not credential.cooldown_until or credential.cooldown_until <= now:
            credential.status = "active"
            credential.cooldown_until = None
