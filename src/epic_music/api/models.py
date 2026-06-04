from datetime import datetime
from typing import Dict, List, Literal, Tuple
from uuid import uuid4

from pydantic.alias_generators import to_camel
from pydantic import BaseModel

from sqlmodel import SQLModel, Field, Relationship

# SQL Models
class FeedEntry(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str | None
    album: str | None
    site_name: str
    original_url: str
    youtube_url: str
    youtube_title: str
    original_url: str
    message_id: int
    posted_by: str
    posted_at: datetime = Field(default_factory=lambda: datetime.now())

    artists: List["TrackArtist"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackArtist.artist.asc()"})
    genres: List["TrackGenre"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackGenre.artist.asc()"})
    reactions: List["EntryReaction"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "Reaction.artist.asc()"})

class TrackArtist(SQLModel, table=True):
    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    artist: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="artists")

class TrackGenre(SQLModel, table=True):
    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    genre: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="genres")

class EntryReaction(SQLModel, table=True):
    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    emoji: str = Field(primary_key=True)
    count: int = Field(default=1)

    feed_entry: FeedEntry = Relationship(back_populates="reactions")

# Pydantic FastAPI models
FeedFilters = Literal["site_name", "artist", "genre", "posted_by"]

class ListFeedRequest(BaseModel):
    filters: Dict[FeedFilters, str]
    sort_by: Literal["date_posted"]
    sort_order: Literal["asc", "desc"]
    page: int = 0

    model_config = {"alias_generator": to_camel}

class TaskStartResponse(BaseModel):
    status: Literal["success", "already_running", "error"]
    task_id: str

    model_config = {"alias_generator": to_camel}

class TaskStatusResponse(BaseModel):
    status: Literal["success", "running", "missing", "error"]

    model_config = {"alias_generator": to_camel}
