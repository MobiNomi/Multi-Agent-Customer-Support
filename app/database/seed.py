"""Demo data. Run `python -m app.database.seed` to wipe and reseed the database."""
from app.database.connection import get_engine, session_scope
from app.database.models import Base, Customer, Order, Payment, SupportTicket


def _demo_rows():
    return [
        Customer(id="CUS-1001", name="Ali Khan", email="ali@example.com", plan="Pro"),
        Customer(id="CUS-1002", name="Sara Ahmed", email="sara@example.com", plan="Enterprise"),
        Order(id="ORD-1034", customer_id="CUS-1001", amount=149.00, status="Delivered"),
        Order(id="ORD-1035", customer_id="CUS-1002", amount=2500.00, status="Processing"),
        # Duplicate payment on ORD-1034 is intentional: it drives the billing demo.
        Payment(id="PAY-5001", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00,
                status="Successful", transaction_id="TXN-8921"),
        Payment(id="PAY-5002", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00,
                status="Duplicate", transaction_id="TXN-8922"),
        Payment(id="PAY-5003", customer_id="CUS-1002", order_id="ORD-1035", amount=2500.00,
                status="Successful", transaction_id="TXN-9001"),
        SupportTicket(id="TCK-9001", customer_id="CUS-1001", category="Billing", priority="High",
                      status="Open", description="Duplicate charge on ORD-1034"),
    ]


def seed_database() -> None:
    """Drop every table, recreate it and load the demo rows."""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as session:
        session.add_all(_demo_rows())  # SQLAlchemy orders the INSERTs by foreign key


def seed_if_empty() -> bool:
    """Load demo rows only when there are no customers yet. Returns True if it seeded."""
    Base.metadata.create_all(get_engine())
    with session_scope() as session:
        if session.query(Customer).first() is not None:
            return False
    seed_database()
    return True


if __name__ == "__main__":
    seed_database()
    print(f"Database seeded at {get_engine().url.render_as_string(hide_password=True)}")
