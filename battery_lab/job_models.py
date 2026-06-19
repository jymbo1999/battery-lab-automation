from __future__ import annotations

from datetime import datetime


try:
    from apps import db
except Exception:  # pragma: no cover - package can run outside the main Flask app
    db = None


if db is not None:

    class BatteryJob(db.Model):
        __tablename__ = "battery_jobs"
        __table_args__ = (
            db.Index("ix_battery_jobs_status_created", "status", "created_at"),
            db.Index("ix_battery_jobs_type_created", "job_type", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        job_type = db.Column(db.String(64), nullable=False)
        status = db.Column(db.String(32), nullable=False, default="queued", index=True)
        target = db.Column(db.String(64), nullable=False, default="")
        params_json = db.Column(db.Text, nullable=False, default="{}")
        result_json = db.Column(db.Text, nullable=False, default="{}")
        error_message = db.Column(db.Text, nullable=False, default="")
        created_by = db.Column(db.String(120), nullable=False, default="")
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        queued_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        started_at = db.Column(db.DateTime, nullable=True)
        finished_at = db.Column(db.DateTime, nullable=True)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

        logs = db.relationship(
            "BatteryJobLog",
            back_populates="job",
            cascade="all, delete-orphan",
            order_by="BatteryJobLog.id",
        )

    class BatteryJobLog(db.Model):
        __tablename__ = "battery_job_logs"
        __table_args__ = (db.Index("ix_battery_job_logs_job_created", "job_id", "created_at"),)

        id = db.Column(db.Integer, primary_key=True)
        job_id = db.Column(db.Integer, db.ForeignKey("battery_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
        level = db.Column(db.String(20), nullable=False, default="info")
        message = db.Column(db.Text, nullable=False)
        payload_json = db.Column(db.Text, nullable=False, default="{}")
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        job = db.relationship("BatteryJob", back_populates="logs")

    class BatteryAiRun(db.Model):
        __tablename__ = "battery_ai_runs"
        __table_args__ = (
            db.Index("ix_battery_ai_runs_status_created", "status", "created_at"),
            db.Index("ix_battery_ai_runs_source_created", "source_kind", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        run_type = db.Column(db.String(64), nullable=False, default="analysis_summary")
        source_kind = db.Column(db.String(64), nullable=False, default="battery_outputs")
        source_ref = db.Column(db.Text, nullable=False, default="")
        model = db.Column(db.String(120), nullable=False, default="")
        status = db.Column(db.String(32), nullable=False, default="draft", index=True)
        prompt_version = db.Column(db.String(40), nullable=False, default="")
        prompt_hash = db.Column(db.String(64), nullable=False, default="")
        input_summary_json = db.Column(db.Text, nullable=False, default="{}")
        request_json = db.Column(db.Text, nullable=False, default="{}")
        response_json = db.Column(db.Text, nullable=False, default="{}")
        output_text = db.Column(db.Text, nullable=False, default="")
        error_message = db.Column(db.Text, nullable=False, default="")
        input_tokens = db.Column(db.Integer, nullable=True)
        output_tokens = db.Column(db.Integer, nullable=True)
        timeout_seconds = db.Column(db.Integer, nullable=False, default=0)
        max_retries = db.Column(db.Integer, nullable=False, default=0)
        created_by = db.Column(db.String(120), nullable=False, default="")
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        started_at = db.Column(db.DateTime, nullable=True)
        finished_at = db.Column(db.DateTime, nullable=True)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

        annotations = db.relationship(
            "BatteryAiAnnotation",
            back_populates="run",
            cascade="all, delete-orphan",
            order_by="BatteryAiAnnotation.id",
        )

    class BatteryAiAnnotation(db.Model):
        __tablename__ = "battery_ai_annotations"
        __table_args__ = (
            db.Index("ix_battery_ai_annotations_target", "target_type", "target_ref"),
            db.Index("ix_battery_ai_annotations_run", "run_id", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        run_id = db.Column(db.Integer, db.ForeignKey("battery_ai_runs.id", ondelete="CASCADE"), nullable=False, index=True)
        annotation_type = db.Column(db.String(64), nullable=False, default="summary")
        target_type = db.Column(db.String(64), nullable=False, default="")
        target_ref = db.Column(db.Text, nullable=False, default="")
        payload_json = db.Column(db.Text, nullable=False, default="{}")
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        run = db.relationship("BatteryAiRun", back_populates="annotations")

else:
    BatteryJob = None
    BatteryJobLog = None
    BatteryAiRun = None
    BatteryAiAnnotation = None


def jobs_db_available() -> bool:
    return db is not None and BatteryJob is not None and BatteryJobLog is not None


def ai_db_available() -> bool:
    return db is not None and BatteryAiRun is not None and BatteryAiAnnotation is not None
