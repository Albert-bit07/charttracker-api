from fastapi import FastAPI
from app.routes import router as stock_router

charttracker = FastAPI(title="ChartTracker API",description='Stock price tracking and alert system')
charttracker.include_router(stock_router,prefix="/api",tags=["Stock Tracker"])
                       
