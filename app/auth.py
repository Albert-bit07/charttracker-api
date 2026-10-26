"""Shared operator authentication for the single-instance tracking service."""
import hashlib
import hmac
import os
import re

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False)


def load_token_digest() -> bytes:
    token = os.environ.get('TRACKER_API_TOKEN', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('TRACKER_API_TOKEN must contain 32–128 URL-safe token characters')
    return hashlib.sha256(token.encode('ascii')).digest()


def require_operator(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is not None:
        supplied = hashlib.sha256(credentials.credentials.encode('utf-8')).digest()
        if hmac.compare_digest(supplied, request.app.state.token_digest):
            return
    raise HTTPException(status_code=401, detail='Valid bearer token required',
                        headers={'WWW-Authenticate': 'Bearer'})
