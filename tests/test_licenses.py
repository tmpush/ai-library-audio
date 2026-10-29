import concurrent.futures
import os
import uuid
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.licenses import LicenseStore, LicenseError
from shared.license import verify

@pytest.fixture(params=['sqlite','postgres'])
def store(tmp_path,request):
    if request.param=='sqlite':
        instance=LicenseStore(tmp_path / "licenses.db", Ed25519PrivateKey.generate())
        yield instance
        instance.engine.dispose()
        return
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('PostgreSQL integration requires TEST_DATABASE_URL')
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema, DropSchema
    schema='lbo_test_'+uuid.uuid4().hex
    admin=create_engine(url)
    with admin.begin() as connection:connection.execute(CreateSchema(schema))
    instance=None
    try:
        isolated=make_url(url).update_query_dict({'options':'-csearch_path='+schema})
        instance=LicenseStore(isolated.render_as_string(hide_password=False),Ed25519PrivateKey.generate())
        yield instance
    finally:
        if instance:instance.engine.dispose()
        with admin.begin() as connection:connection.execute(DropSchema(schema,cascade=True))
        admin.dispose()

def test_devices_idempotency_and_grace(store):
    key = store.issue(now=1000)["key"]
    lease = store.lease(key, "a" * 64, activate=True, now=1000)
    store.lease(key, "a" * 64, activate=True, now=1000)
    store.lease(key, "b" * 64, activate=True, now=1000)
    with pytest.raises(LicenseError):
        store.lease(key, "c" * 64, activate=True, now=1000)
    assert verify(lease, store.signing_key.public_key(), "a" * 64, 1000 + 7 * 86400 - 1)
    with pytest.raises(ValueError):
        verify(lease, store.signing_key.public_key(), "a" * 64, 1000 + 7 * 86400)

def test_forgery_and_wrong_machine(store):
    key = store.issue(now=1000)["key"]
    lease = store.lease(key, "a" * 64, activate=True, now=1000)
    with pytest.raises(ValueError):
        verify(lease, store.signing_key.public_key(), "b" * 64, 1001)
    lease["payload"]["offline_until"] += 86400
    with pytest.raises(ValueError):
        verify(lease, store.signing_key.public_key(), "a" * 64, 1001)

def test_revocation_renewal_and_detach(store):
    issued = store.issue(now=1000, devices=1)
    store.lease(issued["key"], "a" * 64, activate=True, now=1000)
    store.detach(issued["id"], "a" * 64)
    with pytest.raises(LicenseError):
        store.lease(issued["key"], "a" * 64, now=1001)
    store.update(issued["id"], extend_days=30, now=1001)
    assert store.list()[0]["expires"] == issued["expires_at"] + 30 * 86400
    store.update(issued["id"], revoke=True)
    with pytest.raises(LicenseError):
        store.lease(issued["key"], "b" * 64, activate=True, now=1001)

def test_concurrent_activation_limit(store):
    key = store.issue(now=1000)["key"]
    def activate(n):
        try:
            store.lease(key, f"{n:064x}", activate=True, now=1001)
            return True
        except LicenseError:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(activate, range(8))) == 2


def test_reducing_limit_requires_detach(store):
    issued=store.issue(now=1000)
    for machine in ['a'*64,'b'*64]:store.lease(issued['key'],machine,activate=True,now=1001)
    with pytest.raises(LicenseError,match='освободите'):store.update(issued['id'],devices=1)
    assert store.list()[0]['max_devices']==2
    store.detach(issued['id'],'b'*64)
    store.update(issued['id'],devices=1)
    assert store.list()[0]['max_devices']==1


def test_batch_failure_rolls_back_every_key(store,monkeypatch):
    original=store.event
    calls=[]
    def fail_on_second(db,identifier,action,now):
        calls.append(identifier)
        if len(calls)==2:raise RuntimeError('simulated storage failure')
        original(db,identifier,action,now)
    monkeypatch.setattr(store,'event',fail_on_second)
    with pytest.raises(RuntimeError):store.issue_batch(count=3)
    assert store.list()==[] and store.audit()==[]
