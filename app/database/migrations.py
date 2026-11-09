"""Small, explicit schema upgrades for databases created by older versions of the app.

`create_all` creates missing tables but never adds columns to existing ones, so a database made
before login existed would lack the login columns. This adds them in place, keeping all data.
(For bigger schema changes, switch to Alembic.)
"""
import logging
from typing import List

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

CUSTOMER_COLUMNS = {
    "password_hash": "VARCHAR",
    "role": "VARCHAR NOT NULL DEFAULT 'customer'",
    "failed_logins": "INTEGER NOT NULL DEFAULT 0",
    "locked_until": "TIMESTAMP",
    "last_login_at": "TIMESTAMP",
    "token_version": "INTEGER NOT NULL DEFAULT 0",
}


def ensure_schema(engine: Engine) -> List[str]:
    """Add any missing login columns and the unique email index. Returns the columns added."""
    insp = inspect(engine)
    if "customers" not in insp.get_table_names():
        return []
    existing = {c["name"] for c in insp.get_columns("customers")}
    added = []
    with engine.begin() as conn:
        for name, ddl in CUSTOMER_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE customers ADD COLUMN {name} {ddl}"))
                added.append(name)
    try:
        with engine.begin() as conn:  # one account per email address, ignoring upper/lower case
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_customers_email_lower ON customers (lower(email))"))
    except Exception as exc:  # e.g. two customers already share an email
        logger.warning("Could not create the unique email index: %s", exc)
    if added:
        logger.info("Upgraded the customers table: added %s", ", ".join(added))
    return added
