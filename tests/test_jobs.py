import json
import sqlite3
import tempfile
import threading
import unittest
import os
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from app.jobs import JobManager, CapacityError
from app.main import create_app
from app.models import StockRequest

REQUEST = StockRequest(ticker='AAPL', buy_or_sell='buy', target_price=100, entry_price=90)
TOKEN = 'test-only-job-token-' + 'x' * 32

class JobTests(unittest.TestCase):
    def setUp(self):
        token_environment = patch.dict(os.environ, {'TRACKER_API_TOKEN': TOKEN})
        token_environment.start()
        self.addCleanup(token_environment.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temporary.name) / 'jobs.sqlite3')
    def tearDown(self):
        self.temporary.cleanup()

    def test_database_is_closed_when_startup_fails(self):
        for failure_point in ('PRAGMA', 'CREATE TABLE', 'commit', 'SELECT id,payload'):
            with self.subTest(failure_point=failure_point):
                class FailingConnection(sqlite3.Connection):
                    def execute(self, sql, *args, **kwargs):
                        if sql.startswith(failure_point):
                            raise sqlite3.OperationalError('injected startup failure')
                        return super().execute(sql, *args, **kwargs)

                    def commit(self):
                        if failure_point == 'commit':
                            raise sqlite3.OperationalError('injected commit failure')
                        return super().commit()

                connection = sqlite3.connect(':memory:', factory=FailingConnection)
                try:
                    with patch('app.jobs.sqlite3.connect', return_value=connection):
                        with self.assertRaises(sqlite3.OperationalError):
                            JobManager(self.path, monitor=lambda *args: self.fail('Worker started during failed initialization'))
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute('SELECT 1')
                finally:
                    connection.close()

    def test_failure_write_errors_release_workers_and_preserve_durable_capacity(self):
        for failure_point in ('update', 'commit'):
            with self.subTest(failure_point=failure_point):
                path = str(Path(self.temporary.name) / (failure_point + '.sqlite3'))
                class FailingConnection(sqlite3.Connection):
                    fail_commit = False
                    def execute(self, sql, *args, **kwargs):
                        if sql.startswith("UPDATE jobs SET status='failed'"):
                            if failure_point == 'update':
                                raise sqlite3.OperationalError('injected update failure')
                            self.fail_commit = True
                        return super().execute(sql, *args, **kwargs)
                    def commit(self):
                        if self.fail_commit:
                            self.fail_commit = False
                            raise sqlite3.OperationalError('injected commit failure')
                        return super().commit()
                connection = sqlite3.connect(path, check_same_thread=False, factory=FailingConnection)
                release = threading.Event()
                def monitor(request, stop):
                    release.wait(2)
                    raise RuntimeError('injected provider failure')
                with patch('app.jobs.sqlite3.connect', return_value=connection):
                    manager = JobManager(path, monitor=monitor, capacity=1)
                try:
                    with self.assertLogs('app.jobs', level='ERROR'):
                        job = manager.create(REQUEST)
                        worker = manager._running[job['id']][1]
                        release.set()
                        worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(manager.list()[0]['running'])
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(manager.list()[0]['status'], 'active')
                    # The unrecorded failure still has durable recovery intent.
                    with self.assertRaises(CapacityError):
                        manager.create(REQUEST)
                finally:
                    release.set()
                    manager.close()
                    connection.close()
                self.assertIsNone(manager._db)
                recovered = JobManager(path, monitor=lambda request,stop:stop.wait(), capacity=1)
                try:
                    self.assertEqual(recovered.list()[0]['id'], job['id'])
                    self.assertTrue(recovered.list()[0]['running'])
                    self.assertEqual(recovered.cancel(job['id']), 'cancelled')
                finally:
                    recovered.close()

    def seed_history(self, manager):
        with manager._db:
            manager._db.executemany(
                "INSERT INTO jobs(id,payload,status,created_at) VALUES(?,?,?,?)",
                [(f'history-{i}', json.dumps(REQUEST.model_dump()),
                  'failed' if i % 2 else 'cancelled', '2020-01-01 00:00:00') for i in range(6)])

    def test_history_pruning_preserves_live_and_stopping_workers(self):
        release = threading.Event()
        manager = JobManager(self.path, monitor=lambda request,stop:release.wait(5))
        try:
            active = manager.create(REQUEST)
            stopping = manager.create(REQUEST)
            manager.cancel(stopping['id'])
            self.seed_history(manager)
            self.assertEqual(manager.prune_history(2), 4)
            rows = {row['id']:row for row in manager.list()}
            self.assertEqual(set(rows), {active['id'], stopping['id'], 'history-4', 'history-5'})
            self.assertTrue(rows[stopping['id']]['running'])
            self.assertEqual(manager.prune_history(2), 0)
            self.assertEqual(manager.prune_history(0), 2)
            self.assertEqual({row['id'] for row in manager.list()}, {active['id'], stopping['id']})
        finally:
            release.set()
            manager.close()
        recovered = JobManager(self.path, monitor=lambda request,stop:stop.wait())
        try:
            self.assertEqual(recovered.prune_history(0), 1)
            self.assertEqual([row['id'] for row in recovered.list()], [active['id']])
            self.assertTrue(recovered.list()[0]['running'])
        finally:
            recovered.close()

    def test_history_pruning_failure_rolls_back_and_rejects_invalid_limits(self):
        manager = JobManager(self.path)
        try:
            self.seed_history(manager)
            for invalid in (-1, 10001, True, 1.5, '2'):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    manager.prune_history(invalid)
            manager._db.execute("CREATE TRIGGER reject_prune BEFORE DELETE ON jobs WHEN OLD.id='history-2' BEGIN SELECT RAISE(ABORT,'injected prune failure'); END")
            manager._db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                manager.prune_history(0)
            self.assertEqual(len(manager.list()), 6)
            self.assertFalse(manager._db.in_transaction)
            manager._db.execute('DROP TRIGGER reject_prune')
            self.assertEqual(manager.prune_history(0), 6)
        finally:
            manager.close()

    def test_history_pruning_http_auth_validation_and_counts(self):
        app = create_app(lambda: JobManager(self.path))
        with TestClient(app) as client:
            self.seed_history(app.state.jobs)
            self.assertEqual(client.delete('/api/track/history?keep=0').status_code, 401)
            headers = {'Authorization':'Bearer '+TOKEN}
            for invalid in ('-1','10001','abc','1.5'):
                self.assertEqual(client.delete('/api/track/history?keep='+invalid,headers=headers).status_code,422)
            response = client.delete('/api/track/history?keep=2',headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'removed':4,'keep':2})
            self.assertEqual(client.delete('/api/track/history?keep=2',headers=headers).json()['removed'],0)
            self.assertEqual(client.delete('/api/track/missing',headers=headers).status_code,404)

    def test_capacity_cancel_and_restart_recovery(self):
        started, stopped = threading.Event(), threading.Event()
        def monitor(request, stop):
            started.set(); stop.wait(); stopped.set()
        first = JobManager(self.path, monitor=monitor, capacity=1)
        try:
            job = first.create(REQUEST)
            self.assertTrue(started.wait(1))
            with self.assertRaises(CapacityError): first.create(REQUEST)
        finally: first.close()
        self.assertTrue(stopped.is_set())
        started.clear(); stopped.clear()
        second = JobManager(self.path, monitor=monitor, capacity=1)
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(second.list()[0]['id'], job['id'])
            self.assertTrue(second.cancel(job['id']))
            self.assertTrue(second.cancel(job['id']))
            self.assertTrue(stopped.wait(1))
            self.assertFalse(second.cancel('missing'))
        finally: second.close()
        started.clear()
        third = JobManager(self.path, monitor=monitor, capacity=1)
        try:
            self.assertFalse(started.is_set())
            self.assertEqual(third.list()[0]['status'], 'cancelled')
        finally: third.close()

    def test_monitor_failure_is_persisted(self):
        failed = threading.Event()
        def monitor(request, stop):
            failed.set()
            raise RuntimeError('simulated provider crash')
        manager = JobManager(self.path, monitor=monitor)
        with self.assertLogs('app.jobs', level='ERROR'):
            manager.create(REQUEST)
            self.assertTrue(failed.wait(1))
            manager.close()
        recovered = JobManager(self.path, monitor=lambda request,stop:self.fail('Failed jobs must not auto-start'))
        try:
            self.assertEqual(recovered.list()[0]['status'], 'failed')
            self.assertEqual(recovered.list()[0]['error'], 'monitor_failed')
        finally: recovered.close()

    def test_http_job_lifecycle(self):
        app = create_app(lambda: JobManager(self.path, monitor=lambda request,stop:stop.wait(), capacity=1))
        with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
            response = client.post('/api/track', json=REQUEST.model_dump())
            self.assertEqual(response.status_code, 202)
            identifier = response.json()['id']
            self.assertEqual(client.post('/api/track', json=REQUEST.model_dump()).status_code, 429)
            self.assertEqual(client.get('/api/track').json()[0]['id'], identifier)
            self.assertEqual(client.delete('/api/track/'+identifier).status_code, 200)
            self.assertEqual(client.delete('/api/track/missing').status_code, 404)
            self.assertEqual(client.post('/api/track', json={'ticker':''}).status_code, 422)

    def test_cancel_preserves_terminal_failure_and_reports_actual_status(self):
        def fail(request, stop):
            raise RuntimeError('provider failure')
        manager = JobManager(self.path, monitor=fail)
        with self.assertLogs('app.jobs', level='ERROR'):
            job = manager.create(REQUEST)
            manager.close()
        app = create_app(lambda: JobManager(self.path, monitor=lambda *args: self.fail('Failed job restarted')))
        with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
            before = client.get('/api/track').json()[0]
            self.assertEqual(before['status'], 'failed')
            for _ in range(2):
                response = client.delete('/api/track/' + job['id'])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['status'], 'failed')
                self.assertEqual(client.get('/api/track').json()[0], before)
        recovered = JobManager(self.path, monitor=lambda *args: self.fail('Failed job restarted'))
        try:
            self.assertEqual(recovered.list()[0]['status'], 'failed')
            self.assertEqual(recovered.list()[0]['error'], 'monitor_failed')
        finally:
            recovered.close()

    def test_monitor_cancellation_wakes_poll_wait(self):
        from unittest.mock import patch
        from app.services.monitor import monitor_stock
        stop = threading.Event()
        polled = threading.Event()
        def quote(_):
            polled.set()
            return None
        with patch('app.services.monitor.get_current_price', side_effect=quote):
            thread = threading.Thread(target=monitor_stock, args=(REQUEST,stop))
            thread.start()
            self.assertTrue(polled.wait(1))
            stop.set(); thread.join(1)
            self.assertFalse(thread.is_alive())
