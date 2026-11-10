"""Small, explicit schema upgrades for databases created by older versions of the app.

`create_all` creates missing tables but never adds columns to existing ones, so a database made
before a feature existed would lack its columns. This adds them in place, keeping all data.
(For bigger schema changes, switch to Alembic.)
"""
import logging
from typing import List

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# table -> {column: DDL}. Only ever add to this; never remove or rename.
NEW_COLUMNS = {
    "customers": {                       # sign-in (v1.5)
        "password_hash": "VARCHAR",
        "role": "VARCHAR NOT NULL DEFAULT 'customer'",
        "failed_logins": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TIMESTAMP",
        "last_login_at": "TIMESTAMP",
        "token_version": "INTEGER NOT NULL DEFAULT 0",
    },
    "tickets": {                         # staff console (v1.8)
        "thread_id": "VARCHAR",
        "closed_at": "TIMESTAMP",
    },
}
CUSTOMER_COLUMNS = NEW_COLUMNS["customers"]


def ensure_schema(engine: Engine) -> List[str]:
    """Add any missing columns and indexes. Returns the columns added, as "table.column"."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    added = []
    with engine.begin() as conn:
        for table, columns in NEW_COLUMNS.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    added.append(f"{table}.{name}")
    if "customers" in tables:
        try:
            with engine.begin() as conn:  # one account per email address, ignoring upper/lower case
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_email_lower "
                                  "ON customers (lower(email))"))
        except Exception as exc:  # e.g. two customers already share an email
            logger.warning("Could not create the unique email index: %s", exc)
    if "tickets" in tables:
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tickets_thread_id ON tickets (thread_id)"))
    if added:
        logger.info("Upgraded the database: added %s", ", ".join(added))
    return added
