from asyncio import create_task, Task
from contextlib import asynccontextmanager
from os import environ
from sys import stdout
from typing import List

from fastapi import FastAPI
from dotenv import load_dotenv
from loguru import logger

from epic_music.api.requests import RateLimitAPIClient, handle_list_entries
from epic_music.api.models import ProcessedFeedEntry, ListFeedRequest, TaskStartResponse, TaskStatusResponse
from epic_music.database.client import DatabaseClient
from epic_music.discbot.client import DiscordClient

_SYNC_TASK_ID = "sync"

load_dotenv()

logger.remove()
logger.add(stdout, colorize=True, format="<green>{time}</green> <level>{message}</level>")
logger.add("../log/log.log", serialize=True, rotation="5 MB")

@asynccontextmanager
async def fastapi_lifespan(app: FastAPI):
    # Create database client
    logger.info("Starting database client...")
    database_client = DatabaseClient()

    # Create rate-limitting request client
    logger.info("Starting API client...")
    api_client = RateLimitAPIClient()

    # Create and start Discord client
    logger.info("Starting Discord client...")
    discord_client = DiscordClient(database_client, api_client)
    await discord_client.start(environ["DISCORD_TOKEN"])

    # Attach clients to the FastAPI app
    app.extra["database_client"] = database_client
    app.extra["discord_client"] = discord_client
    app.extra["api_client"] = api_client
    app.extra["background_tasks"] = {}

    logger.info("Starting FastAPI...")

    yield

    logger.info("Shutting down FastAPI...")

    database_client.engine.dispose()
    await discord_client.close()

app = FastAPI(title="Epic Music API", lifespan=fastapi_lifespan)

@app.get("/list")
async def list_entries(request: ListFeedRequest) -> List[ProcessedFeedEntry]:
    """
    Load entries from the database, optionally filtered or sorted
    based on given parameters and with pagination support
    """
    return await handle_list_entries(request, app.extra["database_client"])

@app.post("/sync")
async def sync_entries() -> TaskStartResponse:
    """
    Initialize a sync of entries. Reads new messages from Discord feed
    and saves them to the database
    """
    sync_task = app.extra["background_tasks"].get(_SYNC_TASK_ID)

    if sync_task:
        return TaskStartResponse(status="already_running", task_id=_SYNC_TASK_ID)

    try:
        sync_task[_SYNC_TASK_ID] = create_task(app.extra["discord_client"].sync_messages())
    except Exception:
        logger.exception("Error when creating sync task!")
        status = "error"

    return TaskStartResponse(status=status, task_id=_SYNC_TASK_ID)

@app.post("/poll")
async def poll_status(task_id: str) -> TaskStatusResponse:
    """
    Returns the status of a running background task.
    """
    task: Task = app.extra["background_tasks"].get(task_id)
    if not task:
        status = "missing"
    elif task.done():
        status = "error" if task.exception() else "success"
    else:
        status = "running"

    return TaskStatusResponse(status=status)
