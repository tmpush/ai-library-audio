from pathlib import Path
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from server.api import create_app
from server.licenses import LicenseStore
from client.engine.api import create_app as engine_app


def test_license_api_auth_and_lifecycle(tmp_path):
    store = LicenseStore(tmp_path / "license.db", Ed25519PrivateKey.generate())
    api = TestClient(create_app(store, PasswordHasher().hash("correct-password"), "s" * 32))
    assert api.get("/admin/licenses").status_code in (401, 403)
    assert api.post("/admin/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    token = api.post("/admin/login", json={"username": "admin", "password": "correct-password"}).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    issued = api.post("/admin/licenses", headers=headers, json={"count": 2}).json()
    assert len(issued) == 2
    body = {"key": issued[0]["key"], "machine_id": "a" * 64}
    assert api.post("/v1/check", json=body).status_code == 403
    assert api.post("/v1/activate", json=body).status_code == 200
    assert api.post("/v1/check", json={**body, "book_text": "private"}).status_code == 422
    assert api.get("/admin/export.csv", headers=headers).status_code == 200
    api.patch("/admin/licenses/" + issued[0]["id"], headers=headers, json={"revoke": True})
    assert api.post("/v1/check", json=body).status_code == 403


def test_local_import_edit_and_prepare(tmp_path):
    api = TestClient(engine_app(tmp_path, "t" * 32))
    assert api.get("/projects").status_code == 401
    api.headers["Authorization"] = "Bearer " + "t" * 32
    sample = Path(__file__).resolve().parents[1] / "samples/night-keeper.fb2"
    response = api.post("/import", files={"file": (sample.name, sample.read_bytes())})
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    assert len(api.get("/projects").json()) == 1
    split = api.post(f"/projects/{identifier}/split", json={"chapter": 0, "at": 2, "title": "Новая"})
    assert len(split.json()["chapters"]) == 3
    assert api.post(f"/projects/{identifier}/prepare", json={"chapter": 0}).json()["chunks"]
    assert api.post(f"/projects/{identifier}/merge/0").status_code == 200
    assert api.delete(f"/projects/{identifier}").status_code == 200
    assert api.get(f"/projects/{identifier}").status_code == 404
