import asyncio

import pytest

from backend.app.main import BodyLimit
from fastapi import HTTPException
from tests.conftest import submit


def test_chunked_transfer_limit_before_parser():
    async def exercise():
        received = []
        chunks = iter([{"type": "http.request", "body": b"123456", "more_body": True},
                       {"type": "http.request", "body": b"789012", "more_body": False}])

        async def receive():
            return next(chunks)

        async def send(message):
            received.append(message)

        async def application(scope, limited_receive, send):
            await limited_receive()
            with pytest.raises(HTTPException) as exc:
                await limited_receive()
            assert exc.value.status_code == 413

        await BodyLimit(application, max_bytes=10)({"type": "http", "headers": []}, receive, send)
    asyncio.run(exercise())


def test_content_length_limit_rejects_without_reading_body():
    async def exercise():
        sent = []
        async def unexpected(*_):
            pytest.fail("Oversized declared body should not be read or parsed")
        async def send(message):
            sent.append(message)
        await BodyLimit(unexpected, max_bytes=10)(
            {"type": "http", "headers": [(b"content-length", b"11")]}, unexpected, send)
        assert sent[0]["status"] == 413
    asyncio.run(exercise())


def test_upload_rate_limit_is_explicit(local):
    _, _, _, client = local
    for _ in range(30):
        assert submit(client, options="invalid-json").status_code == 422
    response = submit(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "UPLOAD_RATE_LIMIT"


def test_chunked_multipart_request_is_limited_end_to_end(local):
    settings, _, _, client = local
    def chunks():
        yield b'--bound\r\nContent-Disposition: form-data; name="file"; filename="large.csv"\r\nContent-Type: text/csv\r\n\r\n'
        for _ in range((settings.max_file_bytes + 262144) // 65536):
            yield b"a" * 65536
        yield b"\r\n--bound--\r\n"
    response = client.post("/api/v1/jobs", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=bound"})
    assert response.status_code == 413, response.text
