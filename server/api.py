import base64
import csv
import io
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from .licenses import LicenseStore, LicenseError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Activation(StrictModel):
    key: str = Field(min_length=20, max_length=100)
    machine_id: str = Field(pattern=r"^[0-9a-f]{64}$")

class Login(StrictModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=1024)

class Issue(StrictModel):
    plan: str = "month"
    devices: int | None = Field(default=None, ge=1, le=100)
    count: int = Field(default=1, ge=1, le=100)

class Update(StrictModel):
    revoke: bool = False
    extend_days: int = Field(default=0, ge=0, le=3650)
    devices: int | None = Field(default=None, ge=1, le=100)


def create_app(store: LicenseStore, admin_hash: str, jwt_secret: str, username="admin"):
    if len(jwt_secret) < 32 or not admin_hash:
        raise ValueError("Configure admin password hash and JWT secret (at least 32 characters)")
    app = FastAPI(title="Local Books License API", docs_url=None, redoc_url=None)
    bearer = HTTPBearer()
    attempts = defaultdict(deque)

    @app.get("/", include_in_schema=False)
    def panel():
        return Response(Path(__file__).with_name("admin.html").read_text(encoding="utf-8"), media_type="text/html",
                        headers={"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'", "Cache-Control": "no-store"})

    @app.get("/admin.js", include_in_schema=False)
    def panel_script():
        return Response(Path(__file__).with_name("admin.js").read_text(encoding="utf-8"), media_type="application/javascript")

    def limited(request, bucket, limit):
        now = time.monotonic()
        identity = request.client.host if request.client else "unknown"
        # In-memory limits are per process; production should also rate-limit at the proxy.
        key = (bucket, identity)
        queue = attempts[key]
        while queue and queue[0] < now - 60:
            queue.popleft()
        if len(queue) >= limit:
            raise HTTPException(429, "Слишком много запросов. Попробуйте через минуту")
        queue.append(now)
        if len(attempts) > 10000:
            for old in list(attempts):
                if not attempts[old] or attempts[old][-1] < now - 60:
                    del attempts[old]

    def admin(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        try:
            payload = jwt.decode(credentials.credentials, jwt_secret, algorithms=["HS256"], issuer="local-books-admin", options={"require": ["exp", "iat", "sub", "iss"]})
            if payload["sub"] != username:
                raise ValueError()
        except Exception:
            raise HTTPException(401, "Сессия истекла")

    @app.exception_handler(LicenseError)
    async def license_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/activate")
    def activate(body: Activation, request: Request):
        limited(request, "license", 60)
        return store.lease(body.key, body.machine_id, activate=True)

    @app.post("/v1/check")
    def check(body: Activation, request: Request):
        limited(request, "license", 60)
        return store.lease(body.key, body.machine_id)

    @app.post("/admin/login")
    def login(body: Login, request: Request):
        limited(request, "login", 5)
        try:
            PasswordHasher().verify(admin_hash, body.password)
            if not secrets.compare_digest(body.username, username):
                raise ValueError()
        except Exception:
            raise HTTPException(401, "Неверный логин или пароль")
        now = int(time.time())
        return {"token": jwt.encode({"sub": username, "iss": "local-books-admin", "iat": now, "exp": now + 3600}, jwt_secret, algorithm="HS256")}

    @app.get("/admin/licenses", dependencies=[Depends(admin)])
    def licenses():
        return store.list()

    @app.get("/admin/policy", dependencies=[Depends(admin)])
    def policy():
        return store.policy

    @app.get("/admin/events", dependencies=[Depends(admin)])
    def audit():
        return store.audit()

    @app.post("/admin/licenses", dependencies=[Depends(admin)])
    def issue(body: Issue):
        return store.issue_batch(body.plan,body.devices,body.count)

    @app.patch("/admin/licenses/{identifier}", dependencies=[Depends(admin)])
    def update(identifier: str, body: Update):
        store.update(identifier, **body.model_dump())
        return {"ok": True}

    @app.get("/admin/licenses/{identifier}/activations", dependencies=[Depends(admin)])
    def activations(identifier: str):
        return store.activations(identifier)

    @app.delete("/admin/licenses/{identifier}/activations/{machine}", dependencies=[Depends(admin)])
    def detach(identifier: str, machine: str):
        store.detach(identifier, machine)
        return {"ok": True}

    @app.get("/admin/export.csv", dependencies=[Depends(admin)])
    def export():
        rows = store.list()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["id", "prefix", "plan", "expires", "max_devices", "revoked", "activations"])
        writer.writeheader()
        writer.writerows(rows)
        return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="licenses.csv"'})

    return app


def from_environment():
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["LICENSE_PRIVATE_KEY"], validate=True))
    store = LicenseStore(os.getenv("DATABASE_URL") or Path(os.getenv("LICENSE_DB", "data/licenses.db")), key)
    return create_app(store, os.environ["ADMIN_PASSWORD_HASH"], os.environ["JWT_SECRET"], os.getenv("ADMIN_USERNAME", "admin"))
