from datetime import datetime
from os import environ
from threading import get_ident
from typing import Any, Dict, List, Literal, Tuple

from sqlalchemy import Engine, delete, or_, text
from sqlalchemy.sql.functions import count, sum, max
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import SQLModel, Session, create_engine, select, desc, asc, distinct

from epic_music.api.models import  FeedEntry, TrackArtist, TrackGenre, EntryReaction, FeedSortOrders, User

_ENTRIES_PER_PAGE = 60

class DatabaseCursor:
    def __init__(self, engine: Engine):
        self.session = Session(engine)

    def get_feed_entries(
        self,
        page: int = 0,
        order_by: Literal[FeedSortOrders] = "date_posted",
        order_asc: bool = False,
        filters: Dict[str, Tuple[SQLModel, List[str]]] | None = None
    ) -> Dict[str, Any]:
        stmt = select(
            FeedEntry
        ).distinct().join(
            TrackArtist, TrackArtist.feed_id == FeedEntry.id, isouter=True
        ).join(
            TrackGenre, TrackGenre.feed_id == FeedEntry.id, isouter=True
        ).join(
            EntryReaction, EntryReaction.feed_id == FeedEntry.id, isouter=True
        )

        # Add filters, if any are given
        if filters:
            for key, (model, terms) in filters.items():
                if not terms:
                    continue

                clauses = [getattr(model, key) == term for term in terms]
                stmt = stmt.where(or_(*clauses))

        if order_by == "reactions":
            sub_query = select(EntryReaction.feed_id, sum(EntryReaction.count)).group_by(EntryReaction.feed_id).subquery("sub")
            stmt = stmt.join(sub_query, sub_query.c.feed_id == FeedEntry.id, isouter=True)
            order_attr = text("sub.sum_1")
        else:
            order_attr = getattr(FeedEntry, order_by)

        order_func = asc if order_asc else desc

        # Select clause for the results
        sub_query = stmt.subquery()
        count_stmt = select(count(distinct(sub_query.c.id))).select_from(sub_query)
        select_stmt = stmt.offset(
            page * _ENTRIES_PER_PAGE
        ).limit(
            _ENTRIES_PER_PAGE
        ).order_by(order_func(order_attr))

        # Select clause for unique artists
        artists_stmt = select(TrackArtist.artist).order_by(TrackArtist.artist).distinct()

        # Select clause for unique genres
        genres_stmt = select(TrackGenre.genre).order_by(TrackGenre.genre).distinct()

        # Select clause for unique posters
        posters_stmt = select(FeedEntry.posted_by).order_by(FeedEntry.posted_by).distinct()

        entries = self.session.exec(select_stmt).all()
        unique_artists = self.session.exec(artists_stmt).all()
        unique_genres = self.session.exec(genres_stmt).all()
        unique_posters = self.session.exec(posters_stmt).all()
        total = self.session.exec(count_stmt).one()

        return {
            "entries": entries,
            "unique_artists": list(unique_artists),
            "unique_genres": list(unique_genres),
            "unique_posters": list(unique_posters),
            "total": total
        }

    def get_latest_entry_timestamp(self) -> datetime | None:
        statement = select(max(FeedEntry.date_posted)).select_from(FeedEntry)

        return self.session.exec(statement).one_or_none()

    def save_feed_entries(self, feed_entries: List[FeedEntry]):
        self.session.add_all(feed_entries)
        self.session.commit()

    def update_reaction_count(self, message_id: int, emoji: str, count: int):
        stmt_1 = select(FeedEntry).where(FeedEntry.message_id == message_id)

        feed_entry = self.session.exec(stmt_1).one_or_none()
        if feed_entry is None:
            return

        model = None
        for reaction_model in feed_entry.reactions:
            if reaction_model.emoji == emoji:
                model = reaction_model
                break

        if model is None:
            self.session.add(EntryReaction(feed_id=feed_entry.id, emoji=emoji, count=count))
        elif count == 0:
            self.session.delete(model)
        else:
            reaction_model.count = count

        self.session.commit()

    def insert_users_if_missing(self, discord_ids: List[int]):
        for disc_id in discord_ids:
            stmt = insert(User).values(id=disc_id).on_conflict_do_nothing(index_elements=["id"])
            self.session.exec(stmt)

        self.session.commit()

    def get_user_token(self, disc_id: int):
        return self.session.exec(select(User.token).where(User.id == disc_id)).one_or_none()

    def get_user_by_token(self, token: str):
        return self.session.exec(select(User.id).where(User.token == token)).one_or_none()

class DatabaseClient:
    def __init__(self) -> None:
        self.engine = create_engine(f"sqlite:///{environ['RESOURCES_PATH']}/database/database.db")
        self.cursors: Dict[int, DatabaseCursor] = {}

        SQLModel.metadata.create_all(bind=self.engine)

    def __enter__(self) -> DatabaseCursor:
        thread_id = get_ident()
        if thread_id in self.cursors:
            return self.cursors[thread_id]

        cursor = DatabaseCursor(self.engine)
        self.cursors[get_ident()] = cursor

        return cursor

    def __exit__(self, exc_type, exc, tb):
        thread_id = get_ident()
        cursor = self.cursors.get(thread_id)
        if cursor:
            cursor.session.close()
            del self.cursors[thread_id]
