from datetime import datetime
from os import environ
from threading import get_ident
from typing import Dict, List, Literal, Sequence, Tuple

from sqlalchemy import Engine, or_
from sqlalchemy.sql.functions import count, max
from sqlmodel import SQLModel, Session, create_engine, select, desc, asc

from epic_music.api.models import EntryReaction, FeedEntry, FeedSortOrders

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
    ) -> Tuple[Sequence[FeedEntry], int]:
        stmt = select(FeedEntry).distinct()

        # Add filters, if any are given
        if filters:
            for key, (model, terms) in filters.items():
                if not terms:
                    continue

                clauses = [getattr(model, key) == term for term in terms]
                if model is not FeedEntry:
                    stmt = stmt.join(model, getattr(model, "feed_id") == FeedEntry.id)

                stmt = stmt.where(or_(*clauses))

        # Join reaction table, if ordering by it
        if order_by == "reactions":
            stmt = stmt.join(
                EntryReaction,
                EntryReaction.feed_id == FeedEntry.id,
                isouter=True
            )

        cte = stmt.cte("entries")

        # Add ordering clause
        order_attr = getattr(FeedEntry, order_by)
        order_func = asc if order_asc else desc

        count_stmt = select(count()).select_from(cte)
        select_stmt = select(FeedEntry).join_from(
            FeedEntry, cte, FeedEntry.id == cte.c.id
        ).offset(
            page * _ENTRIES_PER_PAGE
        ).limit(
            _ENTRIES_PER_PAGE
        ).order_by(
            order_func(order_attr)
        )

        entries = self.session.exec(select_stmt).all()
        total = self.session.exec(count_stmt).one()

        return entries, total

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
