"""SQLite persistence. Every resource key includes tenant ownership, even internal calls."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from cryptography.fernet import Fernet

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid4().hex

class TenantIsolationViolationException(RuntimeError):
    pass

class Store:
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        keyfile = self.directory / 'secret.key'
        if not keyfile.exists():
            try:
                with keyfile.open('xb') as f:
                    f.write(Fernet.generate_key())
                keyfile.chmod(0o600)
            except FileExistsError:
                pass
        self.cipher = Fernet(keyfile.read_bytes())
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT);
            CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, owner TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS entities (tenant_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(tenant_id,kind,id));
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_scope ON events(tenant_id,run_id,seq);
            ''')

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.directory / 'harness.db', timeout=15)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def tenant(self, tenant_id: str, user_id: str):
        with self.db() as db:
            row = db.execute('SELECT data FROM tenants WHERE id=? AND owner=?', (tenant_id, user_id)).fetchone()
        if not row:
            raise TenantIsolationViolationException('Workspace access blocked. Select a workspace you own.')
        return json.loads(row['data'])

    def tenant_internal(self, tenant_id: str):
        if not tenant_id:
            raise TenantIsolationViolationException('A verified tenant is required.')
        with self.db() as db:
            row = db.execute('SELECT data FROM tenants WHERE id=?', (tenant_id,)).fetchone()
        if not row:
            raise TenantIsolationViolationException('Workspace access blocked.')
        return json.loads(row['data'])

    def put(self, tenant_id, kind, value):
        self.tenant_internal(tenant_id)
        if value.get('tenant_id', tenant_id) != tenant_id:
            raise TenantIsolationViolationException('Resource ownership mismatch.')
        value = {**value, 'tenant_id': tenant_id, 'updated_at': now()}
        with self.db() as db:
            db.execute('INSERT INTO entities VALUES(?,?,?,?) ON CONFLICT(tenant_id,kind,id) DO UPDATE SET data=excluded.data', (tenant_id, kind, value['id'], json.dumps(value)))
        return value

    def get(self, tenant_id, kind, entity_id):
        self.tenant_internal(tenant_id)
        with self.db() as db:
            row = db.execute('SELECT data FROM entities WHERE tenant_id=? AND kind=? AND id=?', (tenant_id, kind, entity_id)).fetchone()
        if not row:
            # Missing and foreign resources deliberately have indistinguishable responses.
            raise TenantIsolationViolationException('This resource is unavailable in the current workspace.')
        return json.loads(row['data'])

    def list(self, tenant_id, kind):
        self.tenant_internal(tenant_id)
        with self.db() as db:
            rows = db.execute('SELECT data FROM entities WHERE tenant_id=? AND kind=? ORDER BY rowid DESC', (tenant_id, kind)).fetchall()
        return [json.loads(r['data']) for r in rows]

    def session_summaries(self, tenant_id):
        """Each session's identity and last message, read in SQL rather than decoding every message and
        file diff: for the sidebar and the dashboard, which poll every few seconds."""
        self.tenant_internal(tenant_id)
        with self.db() as db:
            rows = db.execute("""SELECT json_extract(data,'$.id') AS id, json_extract(data,'$.project_id') AS project_id,
                json_extract(data,'$.name') AS name, json_extract(data,'$.updated_at') AS updated_at,
                json_extract(data,'$.archived') AS archived, json_extract(data,'$.team_parent') AS team_parent,
                json_array_length(data,'$.messages') AS count, json_extract(data,'$.messages[#-1].role') AS role,
                json_extract(data,'$.messages[#-1].status') AS status,
                substr(coalesce(nullif(json_extract(data,'$.messages[#-1].content'),''),json_extract(data,'$.messages[#-2].content'),''),1,400) AS content
                FROM entities WHERE tenant_id=? AND kind='sessions' ORDER BY rowid DESC""", (tenant_id,)).fetchall()
        return [{'id': r['id'], 'project_id': r['project_id'], 'name': r['name'], 'updated_at': r['updated_at'], 'archived': bool(r['archived']),
                 'team_parent': r['team_parent'], 'messages': [{'role': r['role'], 'status': r['status'], 'content': r['content']}] if r['count'] else []}
                for r in rows]

    def delete(self, tenant_id, kind, entity_id):
        self.get(tenant_id, kind, entity_id)
        with self.db() as db:
            db.execute('DELETE FROM entities WHERE tenant_id=? AND kind=? AND id=?', (tenant_id, kind, entity_id))

    def event(self, tenant_id, session_id, event_type, message, **details):
        # Events belong to a conversation. The column keeps its original name so an existing
        # store needs no migration; the scope it names is the session.
        run_id = session_id
        self.get(tenant_id, 'sessions', session_id)
        event = dict(type=event_type, message=message, time=now(), **details)
        with self.db() as db:
            cur = db.execute('INSERT INTO events(tenant_id,run_id,data) VALUES(?,?,?)', (tenant_id, run_id, json.dumps(event)))
            event['seq'] = cur.lastrowid
        return event

    def events(self, tenant_id, session_id, after=0):
        run_id = session_id
        self.tenant_internal(tenant_id)
        with self.db() as db:
            if not db.execute("SELECT 1 FROM entities WHERE tenant_id=? AND kind='sessions' AND id=?", (tenant_id, session_id)).fetchone():
                raise TenantIsolationViolationException('This resource is unavailable in the current workspace.')
            rows = db.execute('SELECT seq,data FROM events WHERE tenant_id=? AND run_id=? AND seq>? ORDER BY seq LIMIT 500', (tenant_id, run_id, after)).fetchall()
        return [{**json.loads(r['data']), 'seq': r['seq']} for r in rows]

    def encrypt(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.cipher.decrypt(value.encode()).decode()

def read_json(path, default=None):
    """A small settings file, or `default` when it is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def public_model(model):
    return {k: v for k, v in model.items() if k != 'encrypted_key'}
