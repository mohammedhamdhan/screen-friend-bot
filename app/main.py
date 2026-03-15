from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, checkins, groups, leaderboard, limits, requests, votes, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    logger = logging.getLogger(__name__)

    from bot.main import create_application

    application = create_application()
    app.state.application = application
    logger.info("Application startup complete")

    yield

    if getattr(app.state, "application", None) is not None:
        try:
            await app.state.application.shutdown()
        except Exception:
            pass


app = FastAPI(title="ScreenGate API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(groups.router, prefix="/api/v1")
app.include_router(checkins.router, prefix="/api/v1")
app.include_router(limits.router, prefix="/api/v1")
app.include_router(requests.router, prefix="/api/v1")
app.include_router(votes.router, prefix="/api/v1")
app.include_router(leaderboard.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
