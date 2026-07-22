"""Database models."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

import config


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Credential(Base):
    """One BytePlus account's API key, plus everything the router needs to decide
    whether to send the next job to it."""

    __tablename__ = "credentials"

    id = Column(Integer, primary_key=True)
    label = Column(String(80), nullable=False)          # "Mudassar", "Ali", "Asad"
    account_email = Column(String(200), default="")
    encrypted_key = Column(LargeBinary, nullable=False)
    base_url = Column(String(200), default="")           # blank = use global default

    priority = Column(Integer, default=100)              # lower number is tried first
    max_concurrent = Column(Integer, default=2)
    in_flight = Column(Integer, default=0)               # jobs currently submitted

    quota_remaining = Column(Numeric(14, 4), default=0)
    quota_updated_at = Column(DateTime(timezone=True), default=utcnow)

    # active | cooling | exhausted | disabled
    status = Column(String(20), default="active")
    routing_enabled = Column(Boolean, default=True)      # manual on/off switch
    cooldown_until = Column(DateTime(timezone=True))
    last_error = Column(Text, default="")

    created_at = Column(DateTime(timezone=True), default=utcnow)

    jobs = relationship("Job", back_populates="credential")

    @property
    def api_url(self):
        return (self.base_url or config.ARK_BASE_URL).rstrip("/")

    @property
    def is_available(self):
        if not self.routing_enabled or self.status != "active":
            return False
        if self.cooldown_until and self.cooldown_until > utcnow():
            return False
        return (self.in_flight or 0) < (self.max_concurrent or 1)

    @property
    def load_ratio(self):
        return (self.in_flight or 0) / max(self.max_concurrent or 1, 1)


class ModelDef(Base):
    """Catalogue of usable models. Editable from the dashboard so a new Seedance
    release needs a database row, not a code change."""

    __tablename__ = "model_defs"

    id = Column(Integer, primary_key=True)
    model_id = Column(String(120), nullable=False, unique=True)   # exact ModelArk ID
    display_name = Column(String(120), nullable=False)
    workflow = Column(String(40), default="t2v")   # t2v | i2v | f2f | r2v
    estimated_cost = Column(Numeric(10, 4), default=0)
    enabled = Column(Boolean, default=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """One generation request. `credential_id` is sticky: the task ID returned by
    ModelArk only exists inside the account that created it, so polling must use
    the same key that submitted."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    prompt = Column(Text, nullable=False)
    model_id = Column(String(120), nullable=False)
    workflow = Column(String(40), default="t2v")
    image_url = Column(Text, default="")
    duration = Column(Integer, default=5)
    resolution = Column(String(20), default="720p")

    estimated_cost = Column(Numeric(10, 4), default=0)
    actual_cost = Column(Numeric(10, 4), default=0)

    credential_id = Column(Integer, ForeignKey("credentials.id"))
    task_id = Column(String(200), default="")

    # queued | running | succeeded | failed | blocked
    status = Column(String(20), default="queued")
    attempts = Column(Integer, default=0)
    output_url = Column(Text, default="")
    error = Column(Text, default="")

    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    credential = relationship("Credential", back_populates="jobs")


class UsageLog(Base):
    """Append-only record of what each credential spent and why."""

    __tablename__ = "usage_log"

    id = Column(Integer, primary_key=True)
    credential_id = Column(Integer, ForeignKey("credentials.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    event = Column(String(40))            # submitted | succeeded | failed | quota_sync
    amount = Column(Numeric(10, 4), default=0)
    detail = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)


engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db():
    Base.metadata.create_all(engine)
