from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook

from backend.app.models import Artifact, Job
from sqlalchemy import select
from tests.conftest import submit
from worker.main import execute
from worker.recovery.ownership import claim_job


def completed(local):
    settings, sessions, storage, client = local
    job = submit(client, content=b"id,email\n1,a@example.test\n2,wrong\n",
                 options='{"column_types":{"email":"email"}}', filename="../../unsafe.csv").json()
    _, claim = claim_job(sessions, settings, job["job_id"], "export-test-worker")
    execute(sessions, settings, storage, claim)
    return job["job_id"]


def test_results_zip_contains_committed_files_and_readable_excel(local):
    job_id = completed(local)
    client = local[3]
    response = client.get(f"/api/v1/jobs/{job_id}/exports/results.zip")
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"].split(", ")
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"cleaned.csv", "errors.csv", "summary.json", "report.xlsx"}
        assert archive.read("cleaned.csv") == client.get(f"/api/v1/jobs/{job_id}/artifacts/cleaned").content
        assert archive.read("errors.csv") == client.get(f"/api/v1/jobs/{job_id}/artifacts/errors").content
        workbook = load_workbook(BytesIO(archive.read("report.xlsx")))
        assert workbook.active["A2"].value == "처리한 행"
        assert workbook.active["B2"].value == 2
        assert workbook.active["B3"].value == 1
        assert workbook.active["B4"].value == 1
        assert workbook.active["B4"].data_type == "n"
        workbook.close()
    report = client.get(f"/api/v1/jobs/{job_id}/exports/report.xlsx")
    assert report.status_code == 200
    assert "spreadsheetml" in report.headers["content-type"]
    assert 'filename="report.xlsx"' in report.headers["content-disposition"]


def test_exports_enforce_owner_and_retention(local):
    job_id = completed(local)
    _, sessions, _, client = local
    with sessions.begin() as session:
        session.get(Job, job_id).owner_id = "other"
    for kind in ("report.xlsx", "results.zip"):
        assert client.get(f"/api/v1/jobs/{job_id}/exports/{kind}").status_code == 404
    with sessions.begin() as session:
        job = session.get(Job, job_id)
        job.owner_id = "local"
        job.files_expired = True
    for kind in ("report.xlsx", "results.zip"):
        assert client.get(f"/api/v1/jobs/{job_id}/exports/{kind}").status_code == 410


def test_missing_results_never_produce_partial_zip(local):
    job_id = completed(local)
    _, sessions, storage, client = local
    with sessions() as session:
        artifact = session.scalar(select(Artifact).where(Artifact.job_id == job_id, Artifact.artifact_type == "errors"))
        storage.delete(artifact.storage_key)
    assert client.get(f"/api/v1/jobs/{job_id}/exports/results.zip").status_code == 410
    queued = submit(client).json()["job_id"]
    assert client.get(f"/api/v1/jobs/{queued}/exports/results.zip").status_code == 404
