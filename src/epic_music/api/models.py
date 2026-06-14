from datetime import datetime
from typing import Any, Dict, List, Literal
from uuid import uuid4

from pydantic.alias_generators import to_camel
from pydantic import BaseModel, field_serializer

from sqlmodel import SQLModel, Field, Relationship


# /*************************\
#  |      SQLA Models       |
# \*************************/
class FeedEntry(SQLModel, table=True):
    __tablename__: str = "feed_entries"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str | None
    album: str | None
    site_name: str
    original_url: str
    youtube_id: str
    youtube_title: str
    message_id: int
    posted_by: str
    posted_at: datetime = Field(default_factory=lambda: datetime.now())

    artists: List["TrackArtist"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackArtist.artist.asc()"})
    genres: List["TrackGenre"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackGenre.genre.asc()"})
    reactions: List["EntryReaction"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "EntryReaction.emoji.asc()"})

    model_config = {"alias_generator": to_camel}

    @field_serializer("posted_at", mode="plain", when_used="json")  
    def format_datetime(self, value: datetime) -> str:
        return value.isoformat()

class TrackArtist(SQLModel, table=True):
    __tablename__: str = "track_artists"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    artist: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="artists")

    model_config = {"alias_generator": to_camel}

class TrackGenre(SQLModel, table=True):
    __tablename__: str = "track_genres"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    genre: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="genres")

    model_config = {"alias_generator": to_camel}

class EntryReaction(SQLModel, table=True):
    __tablename__: str = "entry_reactions"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    emoji: str = Field(primary_key=True)
    count: int = Field(default=1)

    feed_entry: FeedEntry = Relationship(back_populates="reactions")

    model_config = {"alias_generator": to_camel}

# /*************************\
# | Pydantic FastAPI models |
# \*************************/
FeedFilters = Literal["site_name", "artist", "genre", "posted_by"]
FeedSortOrders = Literal["date_posted", "reactions"]

class ListFeedRequest(BaseModel):
    filters: Dict[FeedFilters, str]
    sort_by: Literal[FeedSortOrders]
    sort_order: Literal["asc", "desc"]
    page: int = 0

    model_config = {"alias_generator": to_camel}

class ListFeedResponse(BaseModel):
    entries: List[FeedEntry]
    total: int

class TaskStartResponse(BaseModel):
    status: Literal["success", "already_running", "error"]
    task_id: str

    model_config = {"alias_generator": to_camel}

class TaskStatusResponse(BaseModel):
    status: Literal["success", "running", "missing", "error"]

    model_config = {"alias_generator": to_camel}
