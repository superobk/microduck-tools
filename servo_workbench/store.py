"""Durable authority. No browser state is used to infer execution."""
import copy
import os
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from .profiles import Refused


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory/"workbench.sqlite3",check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, session TEXT NOT NULL,
                body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)
        self.db.commit()

    def save(self, s, kind, details=None):
        with self.lock, self.db:
            s["revision"] = s.get("revision",0)+1
            s["updated_at"] = time.time()
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)",
                            (s["id"],json.dumps(s,allow_nan=False)))
            cur=self.db.execute("INSERT INTO events(session,at,kind,body) VALUES (?,?,?,?)",
                                (s["id"],time.time(),kind,json.dumps(details or {},allow_nan=False)))
            return cur.lastrowid

    def meta(self, key, value=None):
        with self.lock, self.db:
            if value is not None:
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(key,json.dumps(value,allow_nan=False)))
                return value
            row=self.db.execute('SELECT body FROM metadata WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else None

    def get(self, ident):
        with self.lock:
            row=self.db.execute("SELECT body FROM sessions WHERE id=?",(ident,)).fetchone()
        if not row: raise Refused("SESSION","批次不存在")
        return json.loads(row[0])

    def list(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM sessions ORDER BY rowid DESC")]

    def events(self, ident, after=0):
        with self.lock:
            return [dict(seq=r[0],at=r[1],kind=r[2],details=json.loads(r[3]))
                    for r in self.db.execute("SELECT seq,at,kind,body FROM events WHERE session=? AND seq>? ORDER BY seq LIMIT 200",
                                             (ident,after))]

    def job(self, ident):
        with self.lock:
            row=self.db.execute("SELECT body FROM jobs WHERE id=?",(ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def jobs(self, ident, limit=50):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT body FROM jobs WHERE session=? ORDER BY rowid DESC LIMIT ?", (ident,limit))]

    def job_count(self, ident):
        with self.lock:
            return self.db.execute('SELECT count(*) FROM jobs WHERE session=?',(ident,)).fetchone()[0]

    def save_job(self, job):
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)",
                            (job["id"],job["session"],json.dumps(job,allow_nan=False)))

    def finish(self, s, job, kind, details=None):
        """Commit completion and its receipt in the same durable transaction."""
        with self.lock, self.db:
            s["revision"] = s.get("revision",0)+1
            s["updated_at"] = time.time()
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)",
                            (s["id"],json.dumps(s,allow_nan=False)))
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)",
                            (job["id"],job["session"],json.dumps(job,allow_nan=False)))
            self.db.execute("INSERT INTO events(session,at,kind,body) VALUES (?,?,?,?)",
                            (s["id"],time.time(),kind,json.dumps(details or {},allow_nan=False)))

    def unfinished(self, session):
        with self.lock:
            rows = self.db.execute("SELECT body FROM jobs WHERE session=?",(session,)).fetchall()
            return [j for row in rows if (j:=json.loads(row[0])).get("state")=="running"]

    def close(self):
        with self.lock:self.db.close()


def new_id():
    return uuid.uuid4().hex


def clone(value):
    return copy.deepcopy(value)


class AuthorityLock:
    """Kernel-held per-directory lock; a crashed process cannot leave a stale lease."""
    def __init__(self, directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        self.file=open(directory/'authority.lock','a+b')
        try:
            if self.file.seek(0,2)==0:self.file.write(b'0');self.file.flush()
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise Refused('AUTHORITY_BUSY','此状态目录已有工作台后端；请在已有页面继续') from error

    def close(self):
        self.file.close()
