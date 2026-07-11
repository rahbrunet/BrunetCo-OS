"""Health endpoint round-trips a typed payload (the contract-loop proof, API side)."""
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_ok() -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "service": "brunetco-api", "version": "0.7.0"}
