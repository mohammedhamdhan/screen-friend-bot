import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, checkins, groups, leaderboard, limits, requests, screen_time, votes, webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _background_init(app: FastAPI, application) -> None:
    """Initialize the bot application in the background with retries."""
    from bot.main import initialize_application

    for attempt in range(1, 6):
        try:
            await initialize_application(application)
            app.state.bot_initialized = True
            logger.info("Background init succeeded on attempt %d", attempt)
            return
        except Exception as exc:
            logger.warning("Background init attempt %d/5 failed: %s", attempt, exc)
            if attempt < 5:
                await asyncio.sleep(3)
    logger.error("Background init failed after 5 attempts — bot will not respond")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_task = None

    try:
        from bot.main import create_application

        application = create_application()
        app.state.application = application
        app.state.bot_initialized = False
        init_task = asyncio.create_task(_background_init(app, application))
    except Exception as exc:
        logger.error("Failed to build bot application: %s", exc)
        app.state.application = None

    logger.info("Application startup complete")

    yield

    if init_task is not None and not init_task.done():
        init_task.cancel()
        try:
            await init_task
        except asyncio.CancelledError:
            pass

    application = getattr(app.state, "application", None)
    if application is not None:
        try:
            await application.shutdown()
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
app.include_router(screen_time.router, prefix="/api/v1")
app.include_router(webhook.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
