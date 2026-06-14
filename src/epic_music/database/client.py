from datetime import datetime
from os import environ
from typing import Dict, List, Literal, Sequence, Tuple

from sqlalchemy.sql.functions import max, count
from sqlmodel import SQLModel, Session, create_engine, select

from epic_music.api.models import FeedEntry, FeedFilters, FeedSortOrders

_ENTRIES_PER_PAGE = 30

class DatabaseCursor:
    def __init__(self, session: Session):
        self.session = session

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
        statement = select(max(FeedEntry.posted_at)).select_from(FeedEntry)

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
        self.engine = create_engine(f"sqlite:///{environ['DATABASE_PATH']}")
        self.session: Session | None = None

    def __enter__(self) -> DatabaseCursor:
        if not self.session:
            self.session = Session(self.engine)

        SQLModel.metadata.create_all(bind=self.engine)

        return DatabaseCursor(self.session)

    def __exit__(self, exc_type, exc, tb):
        if self.session:
            self.session.close()
            self.session = None
