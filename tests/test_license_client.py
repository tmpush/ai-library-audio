import base64
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.licenses import LicenseStore
from client.engine.licensing import LicenseClient


class Vault:
    def __init__(self): self.state = {}
    def load(self): return self.state.copy()
    def save(self, value): self.state = value.copy()


def test_online_offline_revoke_and_expiry(tmp_path):
    key = Ed25519PrivateKey.generate()
    store = LicenseStore(tmp_path / 'server.db', key)
    now = [1000]
    online = [True]
    issued = store.issue(now=now[0])
    def request(req):
        import json
        if not online[0]: raise httpx.ConnectError('offline')
        body=json.loads(req.content)
        try:
            lease=store.lease(body['key'],body['machine_id'],activate=req.url.path.endswith('activate'),now=now[0])
            return httpx.Response(200,json=lease)
        except ValueError:
            return httpx.Response(403,json={'detail':'denied'})
    client=LicenseClient('http://127.0.0.1',base64.b64encode(key.public_key().public_bytes_raw()).decode(),'a'*64,Vault(),httpx.Client(transport=httpx.MockTransport(request)),lambda:now[0])
    client.activate(issued['key'])
    online[0]=False
    now[0]+=6*86400
    assert client.authorize(force_check=True)
    now[0]+=86400
    with pytest.raises(ValueError):client.authorize(force_check=True)
    online[0]=True
    assert client.authorize(force_check=True)
    store.update(issued['id'],revoke=True,now=now[0])
    with pytest.raises(ValueError):client.authorize(force_check=True)
    online[0]=False
    with pytest.raises(ValueError):client.authorize(force_check=True)


def test_https_required():
    with pytest.raises(ValueError): LicenseClient('http://external.example','', 'a'*64,Vault())


def test_typo_in_new_key_does_not_erase_active_license():
    key=Ed25519PrivateKey.generate()
    vault=Vault();vault.save({'key':'existing','lease':{'signed':'state'}})
    client=LicenseClient('http://127.0.0.1',base64.b64encode(key.public_key().public_bytes_raw()).decode(),'a'*64,vault,
                         httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(403))))
    with pytest.raises(ValueError):client.activate('invalid key')
    assert vault.load()['key']=='existing'
