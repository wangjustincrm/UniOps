from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import api_router
from app.core.config import settings

app = FastAPI(
    title="UniOps File Server",
    description="Centralized file storage for all document attachments",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.ALLOWED_ORIGINS, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)
app.include_router(api_router, prefix="/files/v1")
