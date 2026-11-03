from fastapi.testclient import TestClient
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.api import create_app
from server.database import LicenseStore

def test_admin_subscription_lifecycle(tmp_path):
    store=LicenseStore(tmp_path/'licenses.db',Ed25519PrivateKey.generate())
    with TestClient(create_app(store,PasswordHasher().hash('test-password-only'),'s'*40)) as c:
        assert c.get('/admin/licenses').status_code in (401,403)
        token=c.post('/admin/login',json={'username':'admin','password':'test-password-only'}).json()['token']
        c.headers['Authorization']='Bearer '+token
        issued=c.post('/admin/licenses',json={'plan':'month','days':15,'count':2}).json()
        assert len(issued)==2
        item=issued[0];route='/admin/licenses/'+item['id']
        assert c.patch(route,json={'plan':'year'}).status_code==200
        row=next(r for r in c.get('/admin/licenses').json() if r['id']==item['id'])
        assert row['plan']=='year' and row['expires']==item['expires_at']
        assert c.patch(route,json={'plan':'unknown','extend_days':30}).status_code==403
        assert c.post('/admin/licenses',json={'days':0}).status_code==422
        for machine in ['a','b']:
            assert c.post('/v1/activate',json={'key':item['key'],'machine_id':machine*64}).status_code==200
        assert c.post('/v1/activate',json={'key':item['key'],'machine_id':'c'*64}).status_code==403
        assert c.patch(route,json={'devices':1}).status_code==403
        assert c.delete(route+'/activations/'+'b'*64).status_code==200
        assert c.patch(route,json={'devices':1,'extend_days':30}).status_code==200
        row=next(r for r in c.get('/admin/licenses').json() if r['id']==item['id'])
        assert row['expires']==item['expires_at']+30*86400
        export=c.get('/admin/export.csv').text
        assert item['key'] not in export and item['id'] in export
        assert c.patch(route,json={'revoke':True}).status_code==200
        assert c.post('/v1/check',json={'key':item['key'],'machine_id':'a'*64}).status_code==403
    store.engine.dispose()
