from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship

class Base(DeclarativeBase):
    pass

class FeedEntry(Base):
    __tablename__ = "feed_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[Optional[str]] = mapped_column(String(256))
    album: Mapped[Optional[str]] = mapped_column(String(256))
    site_name: Mapped[str] = mapped_column(String(128))
    youtube_title: Mapped[str] = mapped_column(String(512))
    youtube_url: Mapped[str] = mapped_column(String(512))
    original_url: Mapped[str] = mapped_column(String(512))
    message_id: Mapped[int] = mapped_column(Integer)
    posted_by: Mapped[str] = mapped_column(String(128))
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now())
 
    artists = relationship("TrackArtist", back_populates="feed_entry", order_by="TrackArtist.artist.asc()")
    genres = relationship("TrackGenre", back_populates="feed_entry", order_by="TrackGenre.genre.asc()")
    reactions = relationship("Reaction", back_populates="feed_entry", order_by="Reaction.count.desc()")

class TrackArtist(Base):
    __tablename__ = "track_artists"

    feed_id: Mapped[str] = mapped_column(String(64), ForeignKey("feed_entries.id"), primary_key=True)
    artist: Mapped[str] = mapped_column(String(512), primary_key=True)

    feed_entry = relationship("FeedEntry", back_populates="artists")

class TrackGenre(Base):
    __tablename__ = "track_genres"

    feed_id: Mapped[str] = mapped_column(String(64), ForeignKey("feed_entries.id"), primary_key=True)
    genre: Mapped[str] = mapped_column(String(256), primary_key=True)

    feed_entry = relationship("FeedEntry", back_populates="genres")

class Reaction(Base):
    __tablename__ = "reactions"

    feed_id: Mapped[str] = mapped_column(String(64), ForeignKey("feed_entries.id"), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    feed_entry = relationship("FeedEntry", back_populates="reactions")
