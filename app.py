"""Flask dashboard for the Seedance router."""

import functools

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)

import config
import crypto_util
import modelark
import router
from models import Credential, Job, ModelDef, SessionLocal, UsageLog, init_db, utcnow

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not flask_session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        password = request.form.get("password", "")
        if user == config.ADMIN_USER and password == config.ADMIN_PASSWORD:
            flask_session["user"] = user
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Those credentials did not match.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    db = SessionLocal()
    try:
        credentials = db.query(Credential).order_by(Credential.priority).all()
        recent = db.query(Job).order_by(Job.created_at.desc()).limit(15).all()

        totals = {
            "keys": len(credentials),
            "available": sum(1 for c in credentials if c.is_available),
            "quota": sum(float(c.quota_remaining or 0) for c in credentials),
            "largest": max(
                (float(c.quota_remaining or 0) for c in credentials), default=0
            ),
            "queued": db.query(Job).filter(Job.status == "queued").count(),
            "running": db.query(Job).filter(Job.status == "running").count(),
            "blocked": db.query(Job).filter(Job.status == "blocked").count(),
        }
        return render_template(
            "dashboard.html",
            credentials=credentials,
            recent=recent,
            totals=totals,
            low_threshold=config.LOW_QUOTA_THRESHOLD,
        )
    finally:
        db.close()


# ---------------------------------------------------------------- credentials


@app.route("/credentials")
@login_required
def credentials_list():
    db = SessionLocal()
    try:
        credentials = db.query(Credential).order_by(Credential.priority).all()
        masked = {}
        for credential in credentials:
            try:
                masked[credential.id] = crypto_util.mask(
                    crypto_util.decrypt(credential.encrypted_key)
                )
            except RuntimeError:
                masked[credential.id] = "unreadable — re-add"
        return render_template(
            "credentials.html", credentials=credentials, masked=masked
        )
    finally:
        db.close()


@app.route("/credentials/add", methods=["POST"])
@login_required
def credentials_add():
    db = SessionLocal()
    try:
        api_key = request.form.get("api_key", "").strip()
        if not api_key:
            flash("An API key is required.", "error")
            return redirect(url_for("credentials_list"))

        credential = Credential(
            label=request.form.get("label", "").strip() or "Unnamed",
            account_email=request.form.get("account_email", "").strip(),
            encrypted_key=crypto_util.encrypt(api_key),
            base_url=request.form.get("base_url", "").strip(),
            priority=int(request.form.get("priority") or 100),
            max_concurrent=int(request.form.get("max_concurrent") or 2),
            quota_remaining=float(request.form.get("quota_remaining") or 0),
        )
        db.add(credential)
        db.commit()
        flash(f"Added {credential.label}.", "ok")
    finally:
        db.close()
    return redirect(url_for("credentials_list"))


@app.route("/credentials/<int:credential_id>/update", methods=["POST"])
@login_required
def credentials_update(credential_id):
    db = SessionLocal()
    try:
        credential = db.get(Credential, credential_id)
        if not credential:
            flash("That credential no longer exists.", "error")
            return redirect(url_for("credentials_list"))

        credential.label = request.form.get("label", credential.label).strip()
        credential.priority = int(request.form.get("priority") or credential.priority)
        credential.max_concurrent = int(
            request.form.get("max_concurrent") or credential.max_concurrent
        )
        new_quota = request.form.get("quota_remaining")
        if new_quota not in (None, ""):
            credential.quota_remaining = float(new_quota)
            credential.quota_updated_at = utcnow()
            db.add(
                UsageLog(
                    credential_id=credential.id,
                    event="quota_sync",
                    amount=float(new_quota),
                    detail="Set manually from the dashboard",
                )
            )

        new_key = request.form.get("api_key", "").strip()
        if new_key:
            credential.encrypted_key = crypto_util.encrypt(new_key)
            credential.status = "active"
            credential.last_error = ""

        db.commit()
        flash(f"Updated {credential.label}.", "ok")
    finally:
        db.close()
    return redirect(url_for("credentials_list"))


@app.route("/credentials/<int:credential_id>/toggle", methods=["POST"])
@login_required
def credentials_toggle(credential_id):
    db = SessionLocal()
    try:
        credential = db.get(Credential, credential_id)
        if credential:
            credential.routing_enabled = not credential.routing_enabled
            if credential.routing_enabled and credential.status == "disabled":
                credential.status = "active"
            db.commit()
    finally:
        db.close()
    return redirect(url_for("credentials_list"))


@app.route("/credentials/<int:credential_id>/health", methods=["POST"])
@login_required
def credentials_health(credential_id):
    db = SessionLocal()
    try:
        credential = db.get(Credential, credential_id)
        if credential:
            api_key = crypto_util.decrypt(credential.encrypted_key)
            healthy, message = modelark.check_health(credential.api_url, api_key)
            if not healthy:
                credential.status = "disabled"
                credential.last_error = message
            credential.last_error = message
            db.commit()
            flash(f"{credential.label}: {message}", "ok" if healthy else "error")
    finally:
        db.close()
    return redirect(url_for("credentials_list"))


@app.route("/credentials/<int:credential_id>/delete", methods=["POST"])
@login_required
def credentials_delete(credential_id):
    db = SessionLocal()
    try:
        credential = db.get(Credential, credential_id)
        if credential:
            db.delete(credential)
            db.commit()
            flash(f"Removed {credential.label}.", "ok")
    finally:
        db.close()
    return redirect(url_for("credentials_list"))


# --------------------------------------------------------------------- models


@app.route("/models", methods=["GET", "POST"])
@login_required
def models_page():
    db = SessionLocal()
    try:
        if request.method == "POST":
            model = ModelDef(
                model_id=request.form.get("model_id", "").strip(),
                display_name=request.form.get("display_name", "").strip(),
                workflow=request.form.get("workflow", "t2v"),
                estimated_cost=float(request.form.get("estimated_cost") or 0),
                notes=request.form.get("notes", "").strip(),
            )
            if not model.model_id:
                flash("Paste the exact model ID from the ModelArk console.", "error")
            else:
                db.add(model)
                db.commit()
                flash(f"Added {model.display_name}.", "ok")
            return redirect(url_for("models_page"))

        entries = db.query(ModelDef).order_by(ModelDef.display_name).all()
        return render_template("models.html", models=entries)
    finally:
        db.close()


@app.route("/models/<int:model_pk>/delete", methods=["POST"])
@login_required
def models_delete(model_pk):
    db = SessionLocal()
    try:
        model = db.get(ModelDef, model_pk)
        if model:
            db.delete(model)
            db.commit()
    finally:
        db.close()
    return redirect(url_for("models_page"))


# ----------------------------------------------------------------------- jobs


@app.route("/jobs")
@login_required
def jobs_list():
    db = SessionLocal()
    try:
        status_filter = request.args.get("status", "")
        query = db.query(Job)
        if status_filter:
            query = query.filter(Job.status == status_filter)
        jobs = query.order_by(Job.created_at.desc()).limit(100).all()
        labels = {c.id: c.label for c in db.query(Credential).all()}
        return render_template(
            "jobs.html", jobs=jobs, labels=labels, status_filter=status_filter
        )
    finally:
        db.close()


@app.route("/generate", methods=["GET", "POST"])
@login_required
def generate():
    db = SessionLocal()
    try:
        models = db.query(ModelDef).filter(ModelDef.enabled.is_(True)).all()

        if request.method == "POST":
            model_id = request.form.get("model_id", "")
            model = db.query(ModelDef).filter(ModelDef.model_id == model_id).first()
            if not model:
                flash("Pick a model first. Add one under Models if the list is empty.", "error")
                return redirect(url_for("generate"))

            count = min(int(request.form.get("count") or 1), 20)
            estimated = float(model.estimated_cost or 0)

            preview = router.select_credential(db, estimated)
            if preview is None:
                flash(router.blocked_reason(db, estimated), "error")

            for _ in range(count):
                db.add(
                    Job(
                        prompt=request.form.get("prompt", "").strip(),
                        model_id=model.model_id,
                        workflow=model.workflow,
                        image_url=request.form.get("image_url", "").strip(),
                        duration=int(request.form.get("duration") or 5),
                        resolution=request.form.get("resolution", "720p"),
                        estimated_cost=estimated,
                    )
                )
            db.commit()
            flash(f"Queued {count} job(s). The worker picks them up next tick.", "ok")
            return redirect(url_for("jobs_list"))

        credentials = db.query(Credential).order_by(Credential.priority).all()
        return render_template("generate.html", models=models, credentials=credentials)
    finally:
        db.close()


@app.route("/jobs/<int:job_id>/requeue", methods=["POST"])
@login_required
def jobs_requeue(job_id):
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job:
            job.status = "queued"
            job.attempts = 0
            job.error = ""
            job.task_id = ""
            job.credential_id = None
            db.commit()
            flash(f"Job {job.id} back in the queue.", "ok")
    finally:
        db.close()
    return redirect(url_for("jobs_list"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
