from contextlib import asynccontextmanager
import asyncio
import os
from fastapi import FastAPI
from app.jobs import JobManager
from app.routes import router as stock_router


def create_app(manager_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        factory = manager_factory or (lambda: JobManager(os.environ.get('TRACKER_DB', 'charttracker.sqlite3')))
        app.state.jobs = factory()
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.jobs.close)
    app = FastAPI(title='ChartTracker API', description='Stock price tracking and alert system', lifespan=lifespan)
    app.include_router(stock_router, prefix='/api', tags=['Stock Tracker'])
    return app

charttracker = create_app()
