from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.auth import require_operator
from app.models import StockRequest
from app.jobs import CapacityError

router = APIRouter(dependencies=[Depends(require_operator)])

@router.post('/track', status_code=202)
def track_stock(stock_req: StockRequest, request: Request):
    try:
        job = request.app.state.jobs.create(stock_req)
    except CapacityError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    return {**job, 'message': f'Started tracking {stock_req.ticker}'}

@router.get('/track')
def list_jobs(request: Request):
    return request.app.state.jobs.list()

@router.delete('/track/history')
def prune_history(request: Request, keep: int = Query(default=1000, ge=0, le=10000)):
    return {'removed': request.app.state.jobs.prune_history(keep), 'keep': keep}

@router.delete('/track/{job_id}')
def cancel_job(job_id: str, request: Request):
    status = request.app.state.jobs.cancel(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail='Tracking job not found')
    return {'id': job_id, 'status': status}
