import yfinance as yf
import time

def get_current_price(ticker:str)->float |None:
    try:
        stock= yf.Ticker(ticker)
        data= stock.history(period="1d",interval="1m")
        return data['Close'].iloc[-1]
    except Exception as e:
        print(f"[Error]Fectching data for{ticker}:{e}")
        time.sleep(60)
        return None
