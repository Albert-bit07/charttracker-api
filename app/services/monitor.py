import time
from app.models import StockRequest
from app.utils.finance import get_current_price
from app.services.notifier import notify, playsound
def monitor_stock(stock_req:StockRequest):
    last_price = None
    last_alert_time=0
    alert_interval=300
    print(f"📈Started tracking {stock_req.ticker} (Target: {stock_req.target_price})")
    while True:
        current_price=get_current_price(stock_req.ticker)
        if current_price is None:
            continue
        current_time=time.time()

        if last_price is not None:
            crossed=False
            direction= None
            if last_price < stock_req.target_price<= current_price:
                crossed=True
                direction="upward"
                
                
            elif last_price>stock_req.target_price>=current_price:
                crossed=True
                direction="downward"

            if crossed and (current_time - last_alert_time>=alert_interval):
                notify(
                    title=f"{stock_req.ticker}Alert",
                    message=f"{stock_req.ticker} crossed {stock_req.target_price} {direction}")
                last_alert_time=current_time

                if (stock_req.buy_or_sell.lower()=='buy' and current_price > stock_req.entry_price
                or stock_req.buy_or_sell.lower() =='sell' and current_price < stock_req.entry_price) :
                    print(f"{stock_req.ticker}:In the money")
                    playsound("win")

                else:
                    print(f"{stock_req.ticker}:Out of the money")
                    playsound("lose")

        last_price=current_price
        time.sleep(60)
            

