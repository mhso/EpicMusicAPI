import asyncio
import logging
from http import HTTPStatus
from contextlib import asynccontextmanager
from sys import stdout
from typing import Dict, Literal

from fastapi import FastAPI, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from loguru import logger

from epic_music.api.requests import RateLimitAPIClient
from epic_music.api.models import (
    ResponseFeedEntry,
    ListFeedRequest,
    ListFeedResponse,
    TaskStartResponse,
    TaskStatusResponse,
    FeedFilters,
    FeedSortOrders,
)
from epic_music.database.client import DatabaseClient, DatabaseCursor
from epic_music.discbot.client import DiscordClient, DISCORD_IDS

_SYNC_TASK_ID = "sync"

load_dotenv()

logger.remove()
logger.add(stdout, colorize=True, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{message}</level>")
logger.add("../log/log.log", serialize=True, rotation="5 MB")

# Create database client
logger.info("Starting database client...")
database_client = DatabaseClient()

# Create rate-limitting request client
logger.info("Starting API client...")
api_client = RateLimitAPIClient()

# Create and start Discord client
logger.info("Starting Discord client...")
discord_client = None#DiscordClient(database_client, api_client)
discord_task = None#asyncio.create_task(discord_client.start(environ["DISCORD_TOKEN"]))

background_tasks: Dict[str, asyncio.Task] = {}

@asynccontextmanager
async def fastapi_lifespan(app: FastAPI):
    logger.info("Starting FastAPI...")

    yield

    logger.info("Disconnecting from database...")
    database_client.engine.dispose()

    logger.info("Shutting down Discord bot...")
    # await discord_client.close()
    # while not discord_task.done():
    #     await asyncio.sleep(0.1)

app = FastAPI(debug=True, title="Epic Music API", lifespan=fastapi_lifespan)

loggers = (
    logging.getLogger(name)
    for name in logging.root.manager.loggerDict
    if name.startswith("uvicorn.")
)
for uvicorn_logger in loggers:
    uvicorn_logger.handlers = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = None
    request_str = request.method

    try:
        response: Response = await call_next(request)
    finally:
        if response:
            if response.status_code < 300:
                color = "green"
            elif response.status_code < 400:
                color = "blue"
            else:
                color = "red"

            status_code = response.status_code
            status_name = HTTPStatus(status_code).phrase

            path = request.url.path
            if request.url.query:
                path += f"?{request.url.query}"
            if request.url.fragment:
                path += f"#{request.url.fragment}"

            request_str += f"{path} - <{color}>{status_code} {status_name}</{color}>"
        else:
            request_str += "<red>500 Internal Server Error</red>"

        logger.debug(f"{request.method} {request_str}")

    return response

def _create_cursor():
    with database_client as cursor:
        return cursor

@app.get("/list")
async def list_entries(
    filters: Dict[FeedFilters, str] = {},
    sort_by: FeedSortOrders = "date_posted",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = 0,
    cursor: DatabaseCursor = Depends(_create_cursor)
) -> ListFeedResponse:
    """
    Load entries from the database, optionally filtered or sorted
    based on given parameters and with pagination support
    """
    request = ListFeedRequest(
        filters=filters,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page
    )

    entries, total = cursor.get_feed_entries(
        request.page,
        request.sort_by,
        request.sort_order == "asc",
        request.filters,
    )

    response_entries = []
    for entry in entries:
        extra = {
            "posted_by": DISCORD_IDS.get(entry.posted_by, "Unknown"),
            "avatar": await discord_client.get_avatar(entry.posted_by)
        }
        response_entries.append(ResponseFeedEntry.model_validate(entry, update=extra))

    return ListFeedResponse(entries=response_entries, total=total)

@app.post("/sync")
async def sync_entries() -> TaskStartResponse:
    """
    Initialize a sync of entries. Reads new messages from Discord feed
    and saves them to the database
    """
    if _SYNC_TASK_ID in background_tasks:
        return TaskStartResponse(status="already_running", task_id=_SYNC_TASK_ID)

    try:
        background_tasks[_SYNC_TASK_ID] = asyncio.create_task(discord_client.sync_messages())
    except Exception:
        logger.exception("Error when creating sync task!")
        status = "error"

    return TaskStartResponse(status=status, task_id=_SYNC_TASK_ID)

@app.get("/poll")
async def poll_status(task_id: str) -> TaskStatusResponse:
    """
    Returns the status of a running background task.
    """
    task: asyncio.Task | None = background_tasks.get(task_id)
    if not task:
        status = "missing"
    elif task.done():
        status = "error" if task.exception() else "success"
    else:
        status = "running"

    return TaskStatusResponse(status=status)
