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
