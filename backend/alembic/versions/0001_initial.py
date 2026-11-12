"""Initial durable job, attempt, artifact, event, outbox and heartbeat schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    op.create_table("jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("input_file_path", sa.String(512), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("processing_options", sa.JSON, nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("processed_rows", sa.Integer, nullable=False),
        sa.Column("total_rows", sa.Integer),
        sa.Column("error_rows", sa.Integer, nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_expires_at", sa.Float),
        sa.Column("fencing_token", sa.Integer, nullable=False),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("started_at", sa.Float),
        sa.Column("completed_at", sa.Float),
        sa.Column("next_retry_at", sa.Float),
        sa.Column("last_enqueued_at", sa.Float),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(512)),
        sa.Column("files_expired", sa.Boolean, nullable=False),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_jobs_owner_idempotency"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','CANCELLED')"),
        sa.CheckConstraint("attempt_count >= 0 AND attempt_count <= max_attempts"))
    for name, columns in [("ix_jobs_status_retry", ["status", "next_retry_at"]),
                          ("ix_jobs_owner_created", ["owner_id", "created_at"]),
                          ("ix_jobs_lease", ["status", "lease_expires_at"])]:
        op.create_index(name, "jobs", columns)
    op.create_table("job_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("fencing_token", sa.Integer, nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("started_at", sa.Float, nullable=False),
        sa.Column("finished_at", sa.Float),
        sa.Column("failure_reason", sa.String(64)),
        sa.Column("duration_ms", sa.Float), sa.UniqueConstraint("job_id", "attempt_number"))
    op.create_index("ix_job_attempts_job_id", "job_attempts", ["job_id"])
    op.create_table("job_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("detail", sa.JSON, nullable=False))
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_table("job_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("artifact_type", sa.String(24), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False), sa.UniqueConstraint("job_id", "artifact_type"))
    op.create_index("ix_job_artifacts_job_id", "job_artifacts", ["job_id"])
    op.create_table("outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("published_at", sa.Float),
        sa.Column("stream_id", sa.String(64)))
    op.create_index("ix_outbox_events_job_id", "outbox_events", ["job_id"])
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at", "created_at"])
    op.create_table("worker_heartbeats",
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("seen_at", sa.Float, nullable=False),
        sa.Column("current_job_id", sa.String(36)))


def downgrade():
    for table in ["worker_heartbeats", "outbox_events", "job_artifacts", "job_events", "job_attempts", "jobs"]:
        op.drop_table(table)
