from fastapi import APIRouter,BackgroundTasks
from app.models import StockRequest
from app.services.monitor import monitor_stock

router = APIRouter()
@router.post("/track")
async def track_stock(stock_req:StockRequest,background_tasks:BackgroundTasks):
    background_tasks.add_task(monitor_stock,stock_req)
    return{f"message":f"Started tracking{stock_req.ticker}.."}
