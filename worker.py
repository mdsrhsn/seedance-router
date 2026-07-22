"""Background worker.

Runs two passes on a loop:
  dispatch  - queued jobs get a credential and are submitted
  poll      - running jobs are checked using the credential that submitted them

Run locally:   python worker.py
On Railway:    add a second service with start command `python worker.py`
"""

import logging
import time

import config
import crypto_util
import modelark
import router
from models import Credential, Job, SessionLocal, init_db, utcnow

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s"
)
log = logging.getLogger("worker")


def dispatch_queued(session):
    queued = (
        session.query(Job)
        .filter(Job.status.in_(["queued", "blocked"]))
        .order_by(Job.created_at)
        .limit(20)
        .all()
    )

    for job in queued:
        if job.attempts >= config.MAX_ATTEMPTS_PER_JOB:
            job.status = "failed"
            job.error = f"Gave up after {job.attempts} attempts. Last error: {job.error}"
            continue

        credential = router.select_credential(session, job.estimated_cost)
        if credential is None:
            if job.status != "blocked":
                job.status = "blocked"
                job.error = router.blocked_reason(session, job.estimated_cost)
                log.warning("Job %s blocked: %s", job.id, job.error)
            continue

        api_key = crypto_util.decrypt(credential.encrypted_key)
        content = modelark.build_content(
            job.prompt, job.workflow, job.image_url, job.duration, job.resolution
        )
        job.attempts = (job.attempts or 0) + 1

        try:
            task_id, _ = modelark.submit_task(
                credential.api_url, api_key, job.model_id, content
            )
        except modelark.ArkError as exc:
            job.error = exc.message
            retriable = router.handle_api_error(session, credential, exc)
            job.status = "queued" if retriable else "failed"
            log.warning(
                "Submit failed on %s (%s): %s", credential.label, exc.status_code, exc.message
            )
            session.commit()
            continue
        except Exception as exc:  # network blip
            job.error = str(exc)
            job.status = "queued"
            log.warning("Submit error on %s: %s", credential.label, exc)
            session.commit()
            continue

        job.task_id = task_id
        job.credential_id = credential.id
        job.status = "running"
        job.error = ""
        router.mark_dispatched(session, credential, job)
        log.info("Job %s -> %s (task %s)", job.id, credential.label, task_id)
        session.commit()


def poll_running(session):
    running = session.query(Job).filter(Job.status == "running").limit(50).all()

    for job in running:
        credential = session.get(Credential, job.credential_id)
        if credential is None:
            job.status = "failed"
            job.error = "The credential that submitted this job no longer exists."
            continue

        api_key = crypto_util.decrypt(credential.encrypted_key)

        try:
            status, video_url, raw = modelark.get_task(
                credential.api_url, api_key, job.task_id
            )
        except modelark.ArkError as exc:
            # A poll failure is not a submission failure. Leave the job running and
            # try again next tick unless the key itself is the problem.
            if exc.status_code in (401, 403):
                router.handle_api_error(session, credential, exc)
            log.warning("Poll failed for job %s: %s", job.id, exc.message)
            session.commit()
            continue
        except Exception as exc:
            log.warning("Poll error for job %s: %s", job.id, exc)
            continue

        if status == "succeeded":
            job.status = "succeeded"
            job.output_url = video_url
            job.actual_cost = job.estimated_cost
            router.release(session, credential, job, "succeeded")
            log.info("Job %s succeeded on %s", job.id, credential.label)

        elif status == "failed":
            job.status = "failed"
            error_block = raw.get("error") or {}
            job.error = (
                error_block.get("message")
                if isinstance(error_block, dict)
                else str(error_block)
            ) or "Generation failed without a message."
            # ModelArk generally does not bill a failed generation, so give it back.
            router.release(session, credential, job, "failed", refund=True)
            log.info("Job %s failed on %s: %s", job.id, credential.label, job.error)

        job.updated_at = utcnow()
        session.commit()


def tick():
    session = SessionLocal()
    try:
        router.revive_cooled_credentials(session)
        session.commit()
        dispatch_queued(session)
        session.commit()
        poll_running(session)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("Worker tick failed")
    finally:
        session.close()


def main():
    init_db()
    log.info("Worker started. Interval: %ss", config.WORKER_INTERVAL_SECONDS)
    while True:
        tick()
        time.sleep(config.WORKER_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
