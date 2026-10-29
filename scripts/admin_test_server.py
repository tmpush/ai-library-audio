"""Ephemeral loopback server exclusively for UI tests; never deploy this script."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secrets
import tempfile
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.api import create_app
from server.licenses import LicenseStore
import uvicorn

with tempfile.TemporaryDirectory(prefix='lbo-admin-test-') as directory:
    store = LicenseStore(Path(directory)/'licenses.db', Ed25519PrivateKey.generate())
    app = create_app(store, PasswordHasher().hash('local-test-password-only'), secrets.token_urlsafe(48))
    uvicorn.run(app,host='127.0.0.1',port=8766)
