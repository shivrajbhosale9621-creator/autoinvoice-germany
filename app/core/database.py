import os
import uuid
import sqlite3
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Any, Optional, List, Type

from app.core.config import settings
from chat_store import init_db

logger = logging.getLogger(__name__)


def _resolve_sqlite_file() -> str:
    chat_db = os.getenv("CHAT_DB_PATH")
    if chat_db and "://" not in chat_db:
        return os.path.abspath(chat_db)
    url = settings.SYNC_DATABASE_URL or settings.DATABASE_URL or "./invoices.db"
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            path_part = url[len(prefix):]
            return os.path.abspath(path_part)
    return os.path.abspath("./invoices.db")


try:
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.orm import DeclarativeBase
    _HAS_SQLALCHEMY = True

    class Base(DeclarativeBase):
        pass

    async_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    )

    AsyncSessionLocal = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

except ImportError:
    _HAS_SQLALCHEMY = False

    class Base:
        pass

    class _ColumnDescriptor:
        def __init__(self, model_cls: Type, name: str):
            self.model_cls = model_cls
            self.name = name

        def __eq__(self, other: Any):
            return ("eq", self.name, other)

        def __gt__(self, other: Any):
            return ("gt", self.name, other)

        def __lt__(self, other: Any):
            return ("lt", self.name, other)

        def __ge__(self, other: Any):
            return ("ge", self.name, other)

        def __le__(self, other: Any):
            return ("le", self.name, other)

        def desc(self):
            return ("desc", self.name)

    class SelectStmt:
        def __init__(self, model_cls: Type):
            self.model_cls = model_cls
            self.conditions: List[Any] = []
            self.order_col: Optional[tuple] = None
            self.limit_val: Optional[int] = None

        def where(self, *conds: Any) -> "SelectStmt":
            for c in conds:
                if isinstance(c, list):
                    self.conditions.extend(c)
                else:
                    self.conditions.append(c)
            return self

        def order_by(self, order_spec: Any) -> "SelectStmt":
            if isinstance(order_spec, _ColumnDescriptor):
                self.order_col = ("asc", order_spec.name)
            elif isinstance(order_spec, tuple):
                self.order_col = order_spec
            return self

        def limit(self, n: int) -> "SelectStmt":
            self.limit_val = int(n)
            return self

    class UpdateStmt:
        def __init__(self, model_cls: Type):
            self.model_cls = model_cls
            self.conditions: List[Any] = []
            self.update_values: dict = {}

        def where(self, *conds: Any) -> "UpdateStmt":
            for c in conds:
                self.conditions.append(c)
            return self

        def values(self, **kwargs: Any) -> "UpdateStmt":
            self.update_values.update(kwargs)
            return self

    def select(model_cls: Type) -> SelectStmt:
        return SelectStmt(model_cls)

    def update(model_cls: Type) -> UpdateStmt:
        return UpdateStmt(model_cls)

    def desc(col: Any) -> tuple:
        if isinstance(col, _ColumnDescriptor):
            return ("desc", col.name)
        return ("desc", str(col))

    class _QueryResult:
        def __init__(self, rows: List[Any]):
            self._rows = rows

        def scalar_one_or_none(self) -> Any:
            return self._rows[0] if self._rows else None

        def scalar_one(self) -> Any:
            if not self._rows:
                raise ValueError("No row found")
            return self._rows[0]

        def scalars(self) -> "_QueryResult":
            return self

        def all(self) -> List[Any]:
            return list(self._rows)

    def _serialize_val(v: Any) -> Any:
        if isinstance(v, uuid.UUID):
            return v.hex
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()
        if hasattr(v, "value"):
            return v.value
        if isinstance(v, bool):
            return 1 if v else 0
        return v

    class AsyncSession:
        """Lightweight async SQLite session compatible with SQLAlchemy AsyncSession calls."""

        def __init__(self, db_path: Optional[str] = None):
            self.db_path = db_path or _resolve_sqlite_file()
            self._pending_adds: List[Any] = []

        async def __aenter__(self) -> "AsyncSession":
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            await self.close()

        def _conn(self) -> sqlite3.Connection:
            if not os.path.exists(self.db_path) or os.path.getsize(self.db_path) == 0:
                init_db(self.db_path)
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn

        def add(self, obj: Any) -> None:
            self._pending_adds.append(obj)

        async def delete(self, obj: Any) -> None:
            table = getattr(obj, "__tablename__", "")
            pk_val = getattr(obj, "id", None)
            if not table or pk_val is None:
                return
            conn = self._conn()
            try:
                conn.execute(f"DELETE FROM {table} WHERE id = ?", (_serialize_val(pk_val),))
                if table == "users":
                    conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (_serialize_val(pk_val),))
                    conn.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (_serialize_val(pk_val),))
                    conn.execute("DELETE FROM invoices WHERE user_id = ?", (_serialize_val(pk_val),))
                conn.commit()
            finally:
                conn.close()

        async def execute(self, stmt: Any) -> _QueryResult:
            await self._flush_pending()
            conn = self._conn()
            try:
                if isinstance(stmt, SelectStmt):
                    table = stmt.model_cls.__tablename__
                    where_parts = []
                    params = []
                    for op_name, col, val in stmt.conditions:
                        s_val = _serialize_val(val)
                        if op_name == "eq":
                            if col in ("id", "user_id") and isinstance(val, uuid.UUID):
                                where_parts.append(f"({col} = ? OR {col} = ?)")
                                params.extend([val.hex, str(val)])
                            else:
                                where_parts.append(f"{col} = ?")
                                params.append(s_val)
                        elif op_name == "gt":
                            where_parts.append(f"{col} > ?")
                            params.append(s_val)
                        elif op_name == "lt":
                            where_parts.append(f"{col} < ?")
                            params.append(s_val)
                        elif op_name == "ge":
                            where_parts.append(f"{col} >= ?")
                            params.append(s_val)
                        elif op_name == "le":
                            where_parts.append(f"{col} <= ?")
                            params.append(s_val)
                    sql = f"SELECT * FROM {table}"
                    if where_parts:
                        sql += " WHERE " + " AND ".join(where_parts)
                    if stmt.order_col:
                        direction, col_name = stmt.order_col
                        sql += f" ORDER BY {col_name} {'DESC' if direction == 'desc' else 'ASC'}"
                    if stmt.limit_val is not None:
                        sql += f" LIMIT {stmt.limit_val}"
                    rows = conn.execute(sql, params).fetchall()
                    objs = [stmt.model_cls._from_row(dict(r)) for r in rows]
                    return _QueryResult(objs)

                elif isinstance(stmt, UpdateStmt):
                    table = stmt.model_cls.__tablename__
                    set_parts = []
                    params = []
                    for k, v in stmt.update_values.items():
                        set_parts.append(f"{k} = ?")
                        params.append(_serialize_val(v))
                    where_parts = []
                    for op_name, col, val in stmt.conditions:
                        if col in ("id", "user_id") and isinstance(val, uuid.UUID):
                            where_parts.append(f"({col} = ? OR {col} = ?)")
                            params.extend([val.hex, str(val)])
                        else:
                            where_parts.append(f"{col} = ?")
                            params.append(_serialize_val(val))
                    sql = f"UPDATE {table} SET {', '.join(set_parts)}"
                    if where_parts:
                        sql += " WHERE " + " AND ".join(where_parts)
                    conn.execute(sql, params)
                    conn.commit()
                    return _QueryResult([])
                return _QueryResult([])
            finally:
                conn.close()

        async def _flush_pending(self) -> None:
            if not self._pending_adds:
                return
            conn = self._conn()
            try:
                for obj in self._pending_adds:
                    obj._save_to_conn(conn)
                conn.commit()
                self._pending_adds.clear()
            finally:
                conn.close()

        async def commit(self) -> None:
            await self._flush_pending()

        async def refresh(self, obj: Any) -> None:
            await self._flush_pending()
            table = getattr(obj, "__tablename__", "")
            pk = getattr(obj, "id", None)
            if not table or pk is None:
                return
            conn = self._conn()
            try:
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE id = ? OR id = ?",
                    (_serialize_val(pk), str(pk)),
                ).fetchone()
                if row:
                    fresh = obj.__class__._from_row(dict(row))
                    for k, v in fresh.__dict__.items():
                        setattr(obj, k, v)
            finally:
                conn.close()

        async def rollback(self) -> None:
            self._pending_adds.clear()

        async def close(self) -> None:
            self._pending_adds.clear()

    def AsyncSessionLocal() -> AsyncSession:
        return AsyncSession(_resolve_sqlite_file())


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an active async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
