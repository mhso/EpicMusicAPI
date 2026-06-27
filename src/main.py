import asyncio
import logging
import re
from http import HTTPStatus
from contextlib import asynccontextmanager
from sys import stdout
from typing import Dict, List, Literal
from os import environ

from fastapi import Cookie, FastAPI, HTTPException, Header, Request, Response, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from loguru import logger

from epic_music.api.requests import RateLimitAPIClient
from epic_music.api.models import (
    FeedEntry,
    ResponseFeedEntry,
    ListFeedResponse,
    TaskStartResponse,
    TaskStatusResponse,
    FeedSortOrders,
    TrackArtist,
    TrackGenre,
    Cookies,
    UserResponse,
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
discord_client = DiscordClient(database_client, api_client)

background_tasks: Dict[str, asyncio.Task] = {}

@asynccontextmanager
async def fastapi_lifespan(app: FastAPI):
    logger.info("Starting FastAPI...")
    
    logger.info("Starting Discord bot...")
    await discord_client.login(environ["DISCORD_TOKEN"])
    discord_task = asyncio.create_task(discord_client.connect())

    await discord_client.wait_until_ready()

    yield

    logger.info("Disconnecting from database...")
    database_client.engine.dispose()

    logger.info("Shutting down Discord bot...")
    await discord_client.close()
    while not discord_task.done():
        await asyncio.sleep(0.1)

app = FastAPI(title="Epic Music API", lifespan=fastapi_lifespan, root_path="/epic-music-api")

loggers = (
    logging.getLogger(name)
    for name in logging.root.manager.loggerDict
    if name.startswith("uvicorn.")
)
for uvicorn_logger in loggers:
    uvicorn_logger.handlers = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://mhooge.com:5008/epic-music"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=environ["STATIC_PATH"]), name="static")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if request.url.query:
        path += f"?{request.url.query}"
    if request.url.fragment:
        path += f"#{request.url.fragment}"

    request_str = f"{request.method} {path}"

    response = None
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

            request_str += f" - <{color}>{status_code} {status_name}</{color}>"
        else:
            request_str += " - <red>500 Internal Server Error</red>"

        logger.debug(f"{request_str}")

    return response

def _create_cursor():
    with database_client as cursor:
        return cursor

def _verify_token(authentication: str = Header()):
    if not authentication:
        raise HTTPException(401, detail="Missing access token.")

    if not authentication.startswith("Bearer "):
        raise HTTPException(401, detail="Invalid access token.")

    token = authentication.removeprefix("Bearer ")

    with database_client as cursor:
        if cursor.get_user_by_token(token) is None:
            raise HTTPException(401, detail="Unauthorized.")

    return token

@app.get("/list")
async def list_entries(
    response: Response,
    site_names: List[str] = Query([]),
    artists: List[str] = Query([]),
    genres: List[str] = Query([]),
    posters: List[str] = Query([]),
    sort_by: FeedSortOrders = "date_posted",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = 0,
    cursor: DatabaseCursor = Depends(_create_cursor),
    token: str = Depends(_verify_token),
    cookies: Cookies = Cookie(),
) -> ListFeedResponse:
    """
    Load entries from the database, optionally filtered or sorted
    based on given parameters and with pagination support
    """
    disc_id_reverse_lookup = {v: k for k, v in DISCORD_IDS.items()}

    filters = {
        "site_name": (FeedEntry, site_names),
        "posted_by": (FeedEntry, [disc_id_reverse_lookup[poster] for poster in posters]),
        "genre": (TrackGenre, genres),
        "artist": (TrackArtist, artists),
    }

    feed_data = cursor.get_feed_entries(
        page,
        sort_by,
        sort_order == "asc",
        filters,
    )

    response_entries = []
    for entry in feed_data["entries"]:
        extra = {
            "posted_by": DISCORD_IDS.get(entry.posted_by, "Unknown"),
            "avatar": await discord_client.get_avatar(entry.posted_by)
        }
        response_entry = ResponseFeedEntry.model_validate(entry, update=extra)

        for reaction in response_entry.reactions:
            if (match := re.match(r"\<a?\:(.+)\:(\d+)\>", reaction.emoji)):
                emoji_id = int(match.group(2))
                emoji = discord_client.get_emoji(emoji_id)
                if emoji is None or not emoji.available or emoji.animated:
                    reaction.emoji = "❓"
                else:
                    reaction.emoji_url = emoji.url

        response_entries.append(response_entry)

    feed_data["entries"] = response_entries
    feed_data["unique_posters"] = [DISCORD_IDS.get(poster, "Unknown") for poster in feed_data["unique_posters"]]

    if not cookies.epic_music_token:
        response.set_cookie("epic_music_token", token, max_age=60 * 60 * 24 * 365)#, secure=True, httponly=True)

    return ListFeedResponse(**feed_data)

@app.get("/search", dependencies=[Depends(_verify_token)])
def search_in_entries(search_term: str, token: str) -> ListFeedResponse:
    """
    Search for entries in the database in a hypertextualized way.
    """
    return ListFeedResponse(entries=[], total=0)

@app.post("/sync", dependencies=[Depends(_verify_token)])
async def sync_entries() -> TaskStartResponse:
    """
    Initialize a sync of entries. Reads new messages from Discord feed
    and saves them to the database
    """
    if _SYNC_TASK_ID in background_tasks:
        return TaskStartResponse(status="already_running", task_id=_SYNC_TASK_ID)

    try:
        background_tasks[_SYNC_TASK_ID] = asyncio.create_task(discord_client.sync_messages())
        status = "success"
    except Exception:
        logger.exception("Error when creating sync task!")
        status = "error"

    return TaskStartResponse(status=status, task_id=_SYNC_TASK_ID)

@app.get("/poll", dependencies=[Depends(_verify_token)])
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

@app.get("/user")
def active_user(token = Depends(_verify_token)):
    with database_client as cursor:
        disc_id = cursor.get_user_by_token(token)

    name = DISCORD_IDS.get(disc_id) if disc_id else None

    return UserResponse(name=name)
