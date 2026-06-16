from datetime import datetime
from os import environ
from threading import get_ident
from typing import Dict, List, Literal, Sequence, Tuple

from sqlalchemy import Engine
from sqlalchemy.sql.functions import max, count
from sqlmodel import SQLModel, Session, create_engine, select

from epic_music.api.models import FeedEntry, FeedFilters, FeedSortOrders

_ENTRIES_PER_PAGE = 30

class DatabaseCursor:
    def __init__(self, engine: Engine):
        self.session = Session(engine)

    def get_feed_entries(
        self,
        page: int = 0,
        order_by: Literal[FeedSortOrders] = "date_posted",
        asc: bool = False,
        filters: Dict[FeedFilters, str] = {}
    ) -> Tuple[Sequence[FeedEntry], int]:
        stmt = select(count()).select_from(FeedEntry)
        total = self.session.exec(stmt).one()

        stmt = select(FeedEntry)
        for k, v in filters.items():
            stmt = stmt.where(getattr(FeedEntry, k) == v)

        order_attr = getattr(FeedEntry, order_by)
        stmt = stmt.order_by(order_attr.asc() if asc else order_attr.desc())
        stmt = stmt.offset(page * _ENTRIES_PER_PAGE)

        return self.session.exec(stmt).all(), total

    def get_latest_entry_timestamp(self) -> datetime | None:
        statement = select(max(FeedEntry.date_posted)).select_from(FeedEntry)

        return self.session.exec(statement).one_or_none()

    def save_feed_entries(self, feed_entries: List[FeedEntry]):
        self.session.add_all(feed_entries)
        self.session.commit()

    def update_reaction_count(self, message_id: int, emoji: str, count: int):
        stmt_1 = select(FeedEntry).where(FeedEntry.message_id == message_id)

        result = self.session.exec(stmt_1).one_or_none()
        if result is None:
            return

        for reaction_model in result.reactions:
            if reaction_model.emoji == emoji:
                reaction_model.count = count
                break

        self.session.commit()

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
