import logging
import math
import yfinance as yf

logger = logging.getLogger(__name__)


def get_current_price(ticker: str) -> float | None:
    try:
        data = yf.Ticker(ticker).history(period="1d", interval="1m")
        if data.empty:
            return None
        price = float(data["Close"].iloc[-1])
        return price if math.isfinite(price) and price > 0 else None
    except Exception:
        logger.warning("Could not fetch price for %s", ticker, exc_info=True)
        return None
