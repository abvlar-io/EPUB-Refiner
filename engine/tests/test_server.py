from fastapi.testclient import TestClient

from epub_refiner_engine.server import app

client = TestClient(app)


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_info_lists_only_txt_and_epub() -> None:
    r = client.get("/api/info")
    assert r.status_code == 200
    assert r.json()["supported_extensions"] == [".epub", ".txt"]
