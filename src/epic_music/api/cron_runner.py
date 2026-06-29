import asyncio
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, Tuple
from uuid import uuid4

from sqlmodel import SQLModel, Field, select

from epic_music.database.client import DatabaseClient


class ScheduledTask(SQLModel, table=True):
    __tablename__: str = "scheduled_tasks"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(unique=True)
    interval: float | None = Field(default=None)
    run_at: datetime | None = Field(default=None)
    last_run: datetime | None = Field(default=None)
    fired: bool = Field(default=False)


class CronRunner:
    def __init__(self, database_client: DatabaseClient):
        self._database = database_client
        self._tasks: Dict[str, Tuple[Callable[..., Coroutine], Tuple, Dict]] = {}
        self._loop_task: asyncio.Task | None = None

    def add_task(
        self,
        func: Callable[..., Coroutine],
        args: Tuple[Any, ...] = (),
        kwargs: Dict[str, Any] | None = {},
        *,
        name: str | None = None,
        minute: int | None = None,
        hour: int | None = None,
        day: int | None = None,
        at: datetime | None = None,
    ):
        task_name = name or func.__qualname__

        if at is not None:
            interval = None
            run_at = at
        else:
            interval = float(
                (minute or 0) * 60
                + (hour or 0) * 3600
                + (day or 0) * 86400
            )
            if interval == 0:
                raise ValueError("Specify at least one of: second, minute, hour, day, or at")

            run_at = None

        self._tasks[task_name] = (func, args, kwargs)

        with self._database as cursor:
            existing = cursor.session.exec(
                select(ScheduledTask).where(ScheduledTask.name == task_name)
            ).one_or_none()

            if existing is not None:
                existing.interval = interval
                existing.run_at = run_at
            else:
                cursor.session.add(ScheduledTask(
                    name=task_name,
                    interval=interval,
                    run_at=run_at,
                ))

            cursor.session.commit()

    async def _run_loop(self):
        with self._database as cursor:
            while True:
                now = datetime.now()

                tasks = cursor.session.exec(select(ScheduledTask)).all()

                for task in tasks:
                    if task.name not in self._tasks:
                        continue

                    should_run = False

                    if task.run_at is not None:
                        if not task.fired and now >= task.run_at:
                            task.fired = True
                            should_run = True
                    elif task.interval is not None:
                        if (
                            task.last_run is None
                            or (now - task.last_run).total_seconds() >= task.interval
                        ):
                            task.last_run = now
                            should_run = True

                    if should_run:
                        func, func_args, func_kwargs = self._tasks[task.name]
                        asyncio.create_task(func(*func_args, **func_kwargs))

                cursor.session.commit()

                await asyncio.sleep(60)

    async def start(self):
        if self._loop_task is not None and not self._loop_task.done():
            return

        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

            self._loop_task = None
