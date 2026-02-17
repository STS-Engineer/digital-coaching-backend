# rfq_db.py
import os
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session


def _env_optional(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _env_required(name: str) -> str:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        raise RuntimeError(f"Missing env var: {name}")
    return v.strip()


def build_rfq_url() -> str:
    user = "administrationSTS"
    password = "St$@0987"
    host = "avo-adb-002.postgres.database.azure.com"
    port = "5432"
    name = "RFQ_DATA"
    sslmode = "require"

    return (
        f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}"
        f"?sslmode={sslmode}"
    )

_ENGINE = None
_SessionLocal: Optional[sessionmaker[Session]] = None

_COLUMN_CACHE: dict[str, list[str]] = {}


def init_rfq_db() -> None:
    global _ENGINE, _SessionLocal
    url = build_rfq_url()

    _ENGINE = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=30,
        max_overflow=10,
        echo=False,
    )
    _SessionLocal = sessionmaker(
        bind=_ENGINE,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def is_rfq_ready() -> bool:
    return _SessionLocal is not None


@contextmanager
def rfq_session() -> Generator[Session, None, None]:
    if not _SessionLocal:
        raise RuntimeError("RFQ DB not initialized. Call init_rfq_db() at startup.")
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_rfq_db() -> Generator[Optional[Session], None, None]:
    """
    FastAPI dependency style:
    - returns None if RFQ not initialized (so endpoints can raise 503 cleanly)
    """
    if not _SessionLocal:
        yield None
        return
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_columns(db: Session, table: str) -> list[str]:
    if table in _COLUMN_CACHE:
        return _COLUMN_CACHE[table]

    cols = db.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            ORDER BY ordinal_position
        """),
        {"t": table},
    ).scalars().all()

    _COLUMN_CACHE[table] = list(cols)
    return _COLUMN_CACHE[table]


def _pick_col(db: Session, table: str, candidates: list[str]) -> Optional[str]:
    cols = _get_columns(db, table)
    for c in candidates:
        if c in cols:
            return c
    return None


def list_product_lines(db: Session, limit: int = 100) -> list[dict]:
    name_col = _pick_col(db, "product_lines", ["product_line_name", "name"])
    if not name_col:
        return []

    sql = text(f"""
        SELECT id, {name_col} AS product_line_name
        FROM product_lines
        ORDER BY id
        LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]

def get_product_line_by_id(db: Session, product_line_id: int) -> Optional[dict]:
    cols = _get_columns(db, "product_lines")
    if not cols:
        return None

    select_cols = ", ".join([f"pl.{c}" for c in cols])

    sql = text(f"""
        SELECT {select_cols}
        FROM product_lines pl
        WHERE pl.id = :id
        LIMIT 1
    """)

    row = db.execute(sql, {"id": int(product_line_id)}).mappings().first()
    return dict(row) if row else None

def list_products(db: Session, limit: int = 100) -> list[dict]:
    name_col = _pick_col(db, "products", ["product_name", "name"])
    if not name_col:
        return []

    sql = text(f"""
        SELECT id, {name_col} AS product_name
        FROM products
        ORDER BY id
        LIMIT :limit
    """)
    rows = db.execute(sql, {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]

def list_products_grouped_by_line(db: Session, limit: int = 2000) -> list[dict]:
    sql = text("""
        SELECT
            p.product_line AS line_name,
            p.product_name AS product_name
        FROM products p
        WHERE p.product_name IS NOT NULL
        ORDER BY
            p.product_line NULLS LAST,
            p.product_name NULLS LAST
        LIMIT :limit
    """)

    rows = db.execute(sql, {"limit": limit}).mappings().all()
    if not rows:
        return []

    # Groupement
    grouped = {}
    for r in rows:
        line = r.get("line_name") or "Non classé"
        product = r.get("product_name")

        grouped.setdefault(line, [])
        if product and product not in grouped[line]:
            grouped[line].append(product)

    return [{"line_name": line, "products": products} for line, products in grouped.items()]

def search_products_by_name(db: Session, product_name: str, limit: int = 50) -> list[dict]:
    cols = _get_columns(db, "products")
    if not cols:
        return []

    select_cols = ", ".join([f"p.{c}" for c in cols])

    sql = text(f"""
        SELECT {select_cols}
        FROM products p
        WHERE p.product_name ILIKE :q
        ORDER BY p.id
        LIMIT :limit
    """)

    rows = db.execute(sql, {"q": f"%{product_name}%", "limit": limit}).mappings().all()
    return [dict(r) for r in rows]
