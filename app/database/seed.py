"""Demo data. Run `python -m app.database.seed` to wipe and reseed the database."""
from datetime import timedelta
from functools import lru_cache

from app.auth.passwords import hash_password
from app.database.connection import get_engine, init_db, session_scope
from app.database.models import Base, Customer, Order, Payment, SupportTicket, utcnow

# Demo sign-ins. Shown on the sign-in pages only when DEMO_MODE=true.
DEMO_PASSWORD = "demo1234"
DEMO_ACCOUNTS = ("CUS-1001", "CUS-1002")
DEMO_STAFF = {"id": "STF-1001", "name": "Hina Raza", "email": "staff@acmecloud.example"}


@lru_cache(maxsize=1)
def _demo_password_hash() -> str:
    # Hashing is deliberately slow; hash once per process and reuse it for the demo accounts.
    return hash_password(DEMO_PASSWORD)


def _demo_rows():
    pw = _demo_password_hash()
    return [
        Customer(id="CUS-1001", name="Ali Khan", email="ali@example.com", plan="Pro", password_hash=pw),
        Customer(id="CUS-1002", name="Sara Ahmed", email="sara@example.com", plan="Enterprise", password_hash=pw),
        # A support staff member. Staff use the console at /staff, not the customer chat.
        Customer(id=DEMO_STAFF["id"], name=DEMO_STAFF["name"], email=DEMO_STAFF["email"], plan="Staff",
                 role="staff", password_hash=pw),
        Order(id="ORD-1034", customer_id="CUS-1001", amount=149.00, status="Delivered"),
        Order(id="ORD-1035", customer_id="CUS-1002", amount=2500.00, status="Processing"),
        # Duplicate payment on ORD-1034 is intentional: it drives the billing demo.
        Payment(id="PAY-5001", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00,
                status="Successful", transaction_id="TXN-8921"),
        Payment(id="PAY-5002", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00,
                status="Duplicate", transaction_id="TXN-8922"),
        Payment(id="PAY-5003", customer_id="CUS-1002", order_id="ORD-1035", amount=2500.00,
                status="Successful", transaction_id="TXN-9001"),
        # Sara was charged twice too, so the refund demo works on the Enterprise account as well.
        Payment(id="PAY-5004", customer_id="CUS-1002", order_id="ORD-1035", amount=2500.00,
                status="Duplicate", transaction_id="TXN-9002"),
        SupportTicket(id="TCK-9001", customer_id="CUS-1001", category="Billing", priority="High",
                      status="Open", description="Duplicate charge on ORD-1034",
                      created_at=utcnow() - timedelta(days=3)),   # older than the ticket guard's 1-hour window
    ]


def seed_database() -> None:
    """Drop every table, recreate it and load the demo rows."""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    init_db()
    with session_scope() as session:
        session.add_all(_demo_rows())  # SQLAlchemy orders the INSERTs by foreign key


def seed_if_empty() -> bool:
    """Load demo rows only when there are no customers yet. Returns True if it seeded."""
    init_db()
    with session_scope() as session:
        if session.query(Customer).first() is not None:
            return False
    seed_database()
    return True


def ensure_demo_passwords() -> int:
    """Give the demo accounts their demo password if they have none (databases made before login)."""
    with session_scope() as session:
        missing = [c for c in session.query(Customer).filter(Customer.id.in_(DEMO_ACCOUNTS)).all()
                   if not c.password_hash]
        for c in missing:
            c.password_hash = _demo_password_hash()
        return len(missing)


def ensure_demo_staff() -> bool:
    """Add the demo staff account to databases made before the staff console. Returns True if added."""
    from sqlalchemy import func
    with session_scope() as session:
        if session.get(Customer, DEMO_STAFF["id"]) is not None:
            return False
        if session.query(Customer).filter(func.lower(Customer.email) == DEMO_STAFF["email"]).first():
            return False                      # someone already uses that email; leave it alone
        session.add(Customer(id=DEMO_STAFF["id"], name=DEMO_STAFF["name"], email=DEMO_STAFF["email"],
                             plan="Staff", role="staff", password_hash=_demo_password_hash()))
        return True


# Demo payments added after the first release. Older demo databases get them on startup (demo mode only).
LATER_DEMO_PAYMENTS = (
    dict(id="PAY-5004", customer_id="CUS-1002", order_id="ORD-1035", amount=2500.00,
         status="Duplicate", transaction_id="TXN-9002"),
)


def ensure_demo_payments() -> int:
    """Add demo payments missing from an older demo database. Returns how many were added.

    Only touches the demo customer's own demo order, and never re-adds a payment that exists
    (so a duplicate that was already refunded stays refunded).
    """
    added = 0
    with session_scope() as session:
        for row in LATER_DEMO_PAYMENTS:
            order = session.get(Order, row["order_id"])
            if order is None or order.customer_id != row["customer_id"]:
                continue
            if session.get(Payment, row["id"]) is not None:
                continue
            if session.query(Payment).filter_by(transaction_id=row["transaction_id"]).first() is not None:
                continue
            session.add(Payment(**row))
            added += 1
    return added


if __name__ == "__main__":
    seed_database()
    print(f"Database seeded at {get_engine().url.render_as_string(hide_password=True)}")
