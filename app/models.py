from pydantic import BaseModel

class StockRequest(BaseModel):
    ticker:str
    buy_or_sell:str
    target_price:float
    entry_price:float

