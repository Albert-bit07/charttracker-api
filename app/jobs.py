"""Single-process monitor lifecycle with durable tracking intent."""
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from app.models import StockRequest
from app.services.monitor import monitor_stock

logger = logging.getLogger(__name__)

class CapacityError(Exception):
    pass

class JobManager:
    def __init__(self, path: str, monitor=monitor_stock, capacity: int = 32):
        if capacity < 1:
            raise ValueError('capacity must be positive')
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._running = {}
        self._closing = False
        self._monitor = monitor
        self._capacity = capacity
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA journal_mode=WAL')
            self._db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            self._db.commit()
            rows = self._db.execute("SELECT id,payload FROM jobs WHERE status='active'").fetchall()
            if len(rows) > capacity:
                raise ValueError('Stored active jobs exceed configured capacity')
            # Validate all persisted requests before starting any worker.
            requests = [(row['id'], StockRequest(**json.loads(row['payload']))) for row in rows]
            with self._lock:
                for identifier, request in requests:
                    self._start(identifier, request)
        except BaseException:
            self.close()
            raise

    def _start(self, identifier, request):
        stop = threading.Event()
        worker = threading.Thread(target=self._run, args=(identifier, request, stop), daemon=True, name=f'tracker-{identifier}')
        self._running[identifier] = (stop, worker)
        try:
            worker.start()
        except BaseException:
            del self._running[identifier]
            self._db.execute("UPDATE jobs SET status='failed',error='worker_start_failed' WHERE id=?", (identifier,))
            self._db.commit()
            raise

    def _run(self, identifier, request, stop):
        error = None
        try:
            self._monitor(request, stop)
            if not stop.is_set():
                error = 'monitor_exited'
        except Exception:
            logger.exception('Monitor job %s failed', identifier)
            error = 'monitor_failed'
        finally:
            with self._lock:
                try:
                    if error:
                        self._db.execute("UPDATE jobs SET status='failed',error=? WHERE id=? AND status='active'", (error, identifier))
                        self._db.commit()
                except sqlite3.Error:
                    logger.exception('Could not persist terminal state for job %s', identifier)
                    try:
                        self._db.rollback()
                    except sqlite3.Error:
                        logger.exception('Could not roll back terminal state for job %s', identifier)
                finally:
                    self._running.pop(identifier, None)
                    if self._closing and not self._running and self._db is not None:
                        self._db.close()
                        self._db = None

    def create(self, request: StockRequest):
        with self._lock:
            if self._closing:
                raise RuntimeError('Job manager is shutting down')
            # Unrecorded worker failures retain restart intent; cancelled workers
            # may also still be finishing a provider call. Count their union.
            active = {row['id'] for row in self._db.execute("SELECT id FROM jobs WHERE status='active'")}
            if len(active | self._running.keys()) >= self._capacity:
                raise CapacityError('Monitor capacity reached')
            identifier = str(uuid.uuid4())
            self._db.execute('INSERT INTO jobs(id,payload,status) VALUES(?,?,?)', (identifier, json.dumps(request.model_dump()), 'active'))
            self._db.commit()
            self._start(identifier, request)
            return {'id': identifier, 'status': 'active', 'ticker': request.ticker}

    def list(self):
        with self._lock:
            result = []
            for row in self._db.execute('SELECT id,payload,status,error,created_at FROM jobs ORDER BY created_at,id'):
                item = dict(row)
                item['request'] = json.loads(item.pop('payload'))
                item['running'] = item['id'] in self._running
                result.append(item)
            return result

    def cancel(self, identifier):
        with self._lock:
            row = self._db.execute('SELECT status FROM jobs WHERE id=?', (identifier,)).fetchone()
            if row is None:
                return None
            if row['status'] != 'active':
                return row['status']
            self._db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (identifier,))
            self._db.commit()
            if identifier in self._running:
                self._running[identifier][0].set()
            return 'cancelled'

    def close(self):
        with self._lock:
            self._closing = True
            workers = list(self._running.values())
            for stop, _ in workers:
                stop.set()
        deadline = time.monotonic() + 15
        for _, worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        with self._lock:
            if not self._running and self._db is not None:
                self._db.close()
                self._db = None
            elif self._running:
                logger.warning('Provider calls still finishing; tracking intent remains durable')
