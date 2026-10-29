"""Create owner-only local server credentials once. Never print secret values."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.database import LicenseStore

directory=Path(os.environ['APPDATA'])/'LocalBooksLicenseServer'
directory.mkdir(parents=True,exist_ok=True)
config_path=directory/'config.json'
if config_path.exists():
    raise SystemExit('Existing server configuration retained; refusing to replace signing keys')
port=8791
with socket.socket() as sock:sock.bind(('127.0.0.1',port))
key=Ed25519PrivateKey.generate()
password=secrets.token_urlsafe(24)
config={'port':port,'private_key':base64.b64encode(key.private_bytes_raw()).decode(),
    'public_key':base64.b64encode(key.public_key().public_bytes_raw()).decode(),
    'admin_username':'admin','admin_hash':PasswordHasher().hash(password),'jwt_secret':secrets.token_urlsafe(48)}
store=LicenseStore(directory/'licenses.db',key)
issued=store.issue(plan='year',devices=2)
store.engine.dispose()
config_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
credentials={'url':f'http://127.0.0.1:{port}','username':'admin','password':password,'license':issued}
(directory/'owner-access.json').write_text(json.dumps(credentials,indent=2),encoding='utf-8')
(directory/'public-key.txt').write_text(config['public_key'],encoding='ascii')
(directory/'server.log').touch(exist_ok=True)
print(json.dumps({'url':credentials['url'],'plan':issued['plan'],'devices':issued['max_devices'],
    'expires_at':issued['expires_at'],'credentials_file':str(directory/'owner-access.json')}))
