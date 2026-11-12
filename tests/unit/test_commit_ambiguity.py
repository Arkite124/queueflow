from sqlalchemy import select

import backend.app.main as api_module
from backend.app.models import Job, Outbox
from tests.conftest import submit


def test_upload_preserves_input_when_commit_response_is_lost(local, monkeypatch):
    _, sessions, storage, client = local
    original = api_module.create_job

    def committed_but_response_lost(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("simulated loss after durable commit")

    monkeypatch.setattr(api_module, "create_job", committed_but_response_lost)
    response = submit(client, key="ambiguous-commit")
    assert response.status_code == 503
    with sessions() as session:
        job = session.scalar(select(Job))
        assert job is not None
        assert storage.path(job.input_file_path).is_file()
        assert session.scalar(select(Outbox).where(Outbox.job_id == job.id)) is not None
    monkeypatch.setattr(api_module, "create_job", original)
    retried = submit(client, key="ambiguous-commit")
    assert retried.status_code == 202 and retried.json()["job_id"] == job.id
