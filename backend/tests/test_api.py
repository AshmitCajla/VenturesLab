import time

import fitz
import pytest
from fastapi.testclient import TestClient

import main


def make_pdf(text: str) -> bytes:
    doc = fitz.open()
    for para in text.split("\n\n"):
        doc.new_page().insert_text((72, 72), para)
    return doc.tobytes()


@pytest.fixture
def client(fake_llm):
    with TestClient(main.app) as c:
        yield c


def wait(client, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
    assert len(client.get("/agents").json()) == 12


def test_requires_some_input(client):
    assert client.post("/analyze", data={}).status_code == 422


def test_rejects_non_pdf(client):
    r = client.post("/analyze", files={"file": ("deck.pdf", b"hello", "application/pdf")})
    assert r.status_code == 415


def test_blocks_private_urls(client):
    r = client.post("/analyze", data={"idea": "x", "url": "http://127.0.0.1:8000/secret"})
    body = wait(client, r.json()["job_id"])
    assert body["status"] == "done"
    assert any("Could not read" in w for w in body["warnings"])


def test_job_with_pitch_deck(client, fake_llm):
    pdf = make_pdf("ClinicFlow pitch\n\nProblem: no-shows\n\nFinancials: revenue $2M, burn $40k/month")
    r = client.post("/analyze", data={"idea": "Clinic scheduling"},
                    files={"file": ("deck.pdf", pdf, "application/pdf")})
    assert r.status_code == 202
    body = wait(client, r.json()["job_id"])
    assert body["status"] == "done", body["error"]
    result = body["result"]
    assert result["recommendation"] == "BUILD"
    assert result["evaluation_summary"]["total"] > 20
    assert body["progress"][0]["node"] == "ingest"
    assert len([p for p in body["progress"] if p.get("status") == "ok"]) >= 13
    fin_prompt = next(p for name, p in fake_llm.prompts if name == "FinancialModel")
    assert "burn $40k" in fin_prompt
