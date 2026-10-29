"""Create local secrets once. Never commit generated files."""
import base64
import getpass
import secrets
from pathlib import Path
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

root=Path(__file__).resolve().parents[1]
target=root/'.env'
if target.exists():
    raise SystemExit('.env already exists; refusing to overwrite signing keys')
password=getpass.getpass('Admin password (at least 12 characters): ')
if len(password)<12:
    raise SystemExit('Use at least 12 characters')
key=Ed25519PrivateKey.generate()
# Compose interpolates dollar signs; single quoted .env values preserve Argon2 hash.
values={'ADMIN_USERNAME':'admin','ADMIN_PASSWORD_HASH':PasswordHasher().hash(password),'JWT_SECRET':secrets.token_urlsafe(48),'LICENSE_PRIVATE_KEY':base64.b64encode(key.private_bytes_raw()).decode(),'LICENSE_DB':'/data/licenses.db','POSTGRES_PASSWORD':secrets.token_urlsafe(32)}
target.write_text(''.join(f"{k}='{v}'\n" for k,v in values.items()),encoding='utf-8')
(root/'server/public-key.txt').write_text(base64.b64encode(key.public_key().public_bytes_raw()).decode(),encoding='ascii')
print('Created .env and server/public-key.txt. Keep .env private and back it up.')
