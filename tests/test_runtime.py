import importlib
import unittest
from unittest.mock import patch
from pydantic import ValidationError
from app.models import StockRequest

class RuntimeTests(unittest.TestCase):
    def test_startup_does_not_open_audio_device(self):
        with patch('pygame.mixer.init', side_effect=RuntimeError('no audio device')):
            import app.services.notifier as notifier
            importlib.reload(notifier)

    def test_reject_invalid_requests(self):
        valid = dict(ticker='AAPL', buy_or_sell='buy', target_price=100, entry_price=90)
        for field, value in [('ticker', ''), ('buy_or_sell', 'hold'), ('target_price', -1), ('entry_price', float('nan')), ('target_price', float('inf'))]:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                StockRequest(**{**valid, field: value})

    def test_normalize_request(self):
        req = StockRequest(ticker=' aapl ', buy_or_sell=' BUY ', target_price=100, entry_price=90)
        self.assertEqual(req.ticker, 'AAPL')
        self.assertEqual(req.buy_or_sell, 'buy')

    def test_invalid_quotes_are_absent(self):
        import pandas as pd
        from app.utils.finance import get_current_price
        for values in [[], [float('nan')], [float('inf')], [-5], [0]]:
            with patch('app.utils.finance.yf.Ticker') as ticker:
                ticker.return_value.history.return_value = pd.DataFrame({'Close': values})
                self.assertIsNone(get_current_price('AAPL'))

    def test_failed_quote_does_not_busy_loop(self):
        from app.services.monitor import monitor_stock
        req = StockRequest(ticker='AAPL', buy_or_sell='buy', target_price=100, entry_price=90)
        with patch('app.services.monitor.get_current_price', return_value=None) as quote, patch('app.services.monitor.threading.Event') as event:
            event.return_value.is_set.return_value = False
            event.return_value.wait.side_effect = InterruptedError
            with self.assertRaises(InterruptedError):
                monitor_stock(req)
            self.assertEqual(quote.call_count, 1)
            event.return_value.wait.assert_called_once_with(60)
