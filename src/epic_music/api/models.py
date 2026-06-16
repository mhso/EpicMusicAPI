from datetime import datetime
from typing import Dict, List, Literal
from uuid import uuid4

from pydantic.alias_generators import to_camel
from pydantic import BaseModel, model_serializer, SerializerFunctionWrapHandler

from sqlmodel import SQLModel, Field, Relationship
from sqlmodel._compat import SQLModelConfig

_MODEL_CONFIG: SQLModelConfig = {
    "alias_generator": to_camel,
    "populate_by_name": True,
}

# /*************************\
#  |      SQLA Models       |
# \*************************/
class FeedEntryBase(SQLModel):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str | None
    album: str | None
    site_name: str
    original_url: str
    youtube_id: str
    youtube_title: str
    message_id: int
    date_posted: datetime = Field(default_factory=lambda: datetime.now())

    model_config = _MODEL_CONFIG

class FeedEntry(FeedEntryBase, table=True):
    __tablename__: str = "feed_entries"

    posted_by: int

    artists: List["TrackArtist"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackArtist.artist.asc()"})
    genres: List["TrackGenre"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "TrackGenre.genre.asc()"})
    reactions: List["EntryReaction"] = Relationship(back_populates="feed_entry", sa_relationship_kwargs={"order_by": "EntryReaction.emoji.asc()"})

class ResponseFeedEntry(FeedEntryBase):
    avatar: str
    posted_by: str

    artists: List["TrackArtist"] = []
    genres: List["TrackGenre"] = []
    reactions: List["EntryReaction"] = []

    @model_serializer(mode="wrap")
    def serialize_model(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        serialized = handler(self)

        serialized["avatar"] = self.avatar
        serialized["datePosted"] = self.date_posted.isoformat()
        serialized["artists"] = [artist.artist for artist in self.artists]
        serialized["genres"] = [genre.genre for genre in self.genres]
        serialized["reactions"] = [reaction.model_dump_json() for reaction in self.reactions]

        return serialized

class TrackArtist(SQLModel, table=True):
    __tablename__: str = "track_artists"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    artist: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="artists")

    model_config = _MODEL_CONFIG

class TrackGenre(SQLModel, table=True):
    __tablename__: str = "track_genres"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    genre: str = Field(primary_key=True)

    feed_entry: FeedEntry = Relationship(back_populates="genres")

    model_config = _MODEL_CONFIG

class EntryReaction(SQLModel, table=True):
    __tablename__: str = "entry_reactions"

    feed_id: str | None = Field(default=None, foreign_key="feed_entries.id", primary_key=True)
    emoji: str = Field(primary_key=True)
    count: int = Field(default=1)

    feed_entry: FeedEntry = Relationship(back_populates="reactions")

    model_config = _MODEL_CONFIG


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

    model_config = _MODEL_CONFIG

class ListFeedResponse(BaseModel):
    entries: List[ResponseFeedEntry]
    total: int

class TaskStartResponse(BaseModel):
    status: Literal["success", "already_running", "error"]
    task_id: str

    model_config = _MODEL_CONFIG

class TaskStatusResponse(BaseModel):
    status: Literal["success", "running", "missing", "error"]

    model_config = _MODEL_CONFIG
