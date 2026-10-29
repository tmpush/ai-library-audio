"""Persistent, loopback-only license server for the owner's Windows installation."""
import base64
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from server.api import create_app
from server.database import LicenseStore


def main():
    directory=Path(os.environ['APPDATA'])/'LocalBooksLicenseServer'
    handler=RotatingFileHandler(directory/'server.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8')
    logging.basicConfig(handlers=[handler],level=logging.INFO)
    config=json.loads((directory/'config.json').read_text(encoding='utf-8'))
    sock=socket.socket()
    if os.name=='nt':sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
    try:sock.bind(('127.0.0.1',config['port']))
    except OSError:
        logging.exception('Cannot bind local license server; another instance may already be running')
        sock.close()
        raise
    key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(config['private_key'],validate=True))
    store=LicenseStore(directory/'licenses.db',key)
    app=create_app(store,config['admin_hash'],config['jwt_secret'],config['admin_username'])
    try:
        uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=config['port'],
            log_config=None,access_log=False,proxy_headers=False)).run(sockets=[sock])
    finally:
        sock.close()
        store.engine.dispose()


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
