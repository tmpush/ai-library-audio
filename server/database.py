"""Shared SQLite/PostgreSQL implementation; row locks serialize device activation."""
import hashlib
import json
import secrets
import time
import uuid
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Integer,
                        ForeignKey, select, func, event)
from shared.license import sign


class LicenseError(ValueError):
    pass


metadata = MetaData()
licenses = Table('licenses', metadata,
    Column('id', String, primary_key=True), Column('key_hash', String, unique=True, nullable=False),
    Column('prefix', String, nullable=False), Column('plan', String, nullable=False),
    Column('expires', Integer, nullable=False), Column('max_devices', Integer, nullable=False),
    Column('revoked', Integer, nullable=False, default=0))
activations = Table('activations', metadata,
    Column('license_id', String, ForeignKey('licenses.id'), primary_key=True),
    Column('machine', String, primary_key=True), Column('created', Integer, nullable=False),
    Column('last_check', Integer, nullable=False))
events = Table('events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('license_id', String), Column('action', String, nullable=False), Column('at', Integer, nullable=False))


class LicenseStore:
    def __init__(self, path, signing_key, policy=None):
        self.signing_key = signing_key
        self.policy = policy or json.loads(Path(__file__).with_name('policy.json').read_text(encoding='utf-8'))
        if isinstance(path, Path) or '://' not in str(path):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            url = 'sqlite:///' + path.resolve().as_posix()
        else:
            url = str(path)
        if url.startswith('postgresql://'):
            url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={'timeout': 15} if url.startswith('sqlite:') else {})
        if url.startswith('sqlite:'):
            @event.listens_for(self.engine, 'connect')
            def sqlite_connect(db, record):
                db.isolation_level = None
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA foreign_keys=ON')
            @event.listens_for(self.engine, 'begin')
            def sqlite_begin(conn):
                conn.exec_driver_sql('BEGIN IMMEDIATE')
        metadata.create_all(self.engine)

    @staticmethod
    def digest(key):
        return hashlib.sha256(key.strip().encode()).hexdigest()

    @staticmethod
    def event(db, identifier, action, now):
        db.execute(events.insert().values(license_id=identifier, action=action, at=now))

    def issue(self, plan='month', devices=None, now=None):
        return self.issue_batch(plan,devices,1,now)[0]

    def issue_batch(self, plan='month', devices=None, count=1, now=None):
        now = int(time.time()) if now is None else now
        devices = self.policy['default_devices'] if devices is None else devices
        if plan not in self.policy['plans'] or not 1 <= devices <= 100 or not 1<=count<=100:
            raise LicenseError('Некорректный тариф или лимит устройств')
        expires = now + self.policy['plans'][plan]['days'] * 86400
        issued=[]
        with self.engine.begin() as db:
            for _ in range(count):
                raw = 'LBO-' + secrets.token_urlsafe(24)
                identifier = str(uuid.uuid4())
                db.execute(licenses.insert().values(id=identifier, key_hash=self.digest(raw), prefix=raw[:10], plan=plan, expires=expires, max_devices=devices, revoked=0))
                self.event(db, identifier, 'issued', now)
                issued.append(dict(id=identifier, key=raw, expires_at=expires, max_devices=devices, plan=plan))
        return issued

    def lease(self, raw_key, machine, *, activate=False, now=None):
        now = int(time.time()) if now is None else now
        if len(machine) != 64 or any(c not in '0123456789abcdef' for c in machine):
            raise LicenseError('Некорректный идентификатор устройства')
        with self.engine.begin() as db:
            row = db.execute(select(licenses).where(licenses.c.key_hash == self.digest(raw_key)).with_for_update()).mappings().first()
            if row is None or row['revoked'] or now >= row['expires']:
                raise LicenseError('Ключ недействителен, отозван или срок подписки истёк')
            condition = (activations.c.license_id == row['id']) & (activations.c.machine == machine)
            existing = db.execute(select(activations).where(condition)).first()
            if not existing:
                if not activate:
                    raise LicenseError('Устройство не активировано')
                count = db.execute(select(func.count()).select_from(activations).where(activations.c.license_id == row['id'])).scalar_one()
                if count >= row['max_devices']:
                    raise LicenseError('Достигнут лимит устройств')
                db.execute(activations.insert().values(license_id=row['id'], machine=machine, created=now, last_check=now))
                self.event(db, row['id'], 'activated', now)
            else:
                db.execute(activations.update().where(condition).values(last_check=now))
            self.event(db, row['id'], 'checked', now)
            payload = dict(version=1, license_id=row['id'], machine_id=machine, plan=row['plan'], issued_at=now,
                expires_at=row['expires'], offline_until=min(row['expires'], now+self.policy['offline_days']*86400),
                check_after=now+self.policy['check_hours']*3600)
            return sign(payload, self.signing_key)

    def list(self):
        with self.engine.begin() as db:
            count = select(func.count()).where(activations.c.license_id == licenses.c.id).scalar_subquery()
            return [dict(row) for row in db.execute(select(licenses.c.id, licenses.c.prefix, licenses.c.plan, licenses.c.expires,
                licenses.c.max_devices, licenses.c.revoked, count.label('activations')).order_by(licenses.c.expires.desc(), licenses.c.id)).mappings()]

    def update(self, identifier, *, revoke=False, extend_days=0, devices=None, now=None):
        now = int(time.time()) if now is None else now
        if extend_days < 0 or (devices is not None and not 1 <= devices <= 100):
            raise LicenseError('Некорректное изменение')
        with self.engine.begin() as db:
            row = db.execute(select(licenses).where(licenses.c.id == identifier).with_for_update()).mappings().first()
            if row is None:
                raise LicenseError('Ключ не найден')
            if devices is not None:
                active=db.execute(select(func.count()).select_from(activations).where(activations.c.license_id==identifier)).scalar_one()
                if devices<active:raise LicenseError('Сначала освободите лишние активации, затем уменьшите лимит')
            expires = max(now, row['expires'])+extend_days*86400 if extend_days else row['expires']
            db.execute(licenses.update().where(licenses.c.id == identifier).values(expires=expires,
                max_devices=row['max_devices'] if devices is None else devices, revoked=1 if revoke else row['revoked']))
            self.event(db, identifier, 'updated', now)

    def activations(self, identifier):
        with self.engine.begin() as db:
            return [dict(r) for r in db.execute(select(activations.c.machine, activations.c.created, activations.c.last_check).where(activations.c.license_id == identifier)).mappings()]

    def detach(self, identifier, machine):
        with self.engine.begin() as db:
            db.execute(select(licenses.c.id).where(licenses.c.id == identifier).with_for_update())
            db.execute(activations.delete().where((activations.c.license_id == identifier) & (activations.c.machine == machine)))
            self.event(db, identifier, 'detached', int(time.time()))

    def audit(self, limit=100):
        with self.engine.begin() as db:
            return [dict(r) for r in db.execute(select(events).order_by(events.c.id.desc()).limit(limit)).mappings()]
