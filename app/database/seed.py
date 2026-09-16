import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.models import Base, Customer, Order, Payment, SupportTicket

DB_PATH = "sqlite:///data/support_system.db"

def seed_database():
    os.makedirs("data", exist_ok=True)
    engine = create_engine(DB_PATH, echo=False)
    
    # Recreate tables fresh
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Insert Customers
    c1 = Customer(id="CUS-1001", name="Ali Khan", email="ali@example.com", plan="Pro")
    c2 = Customer(id="CUS-1002", name="Sara Ahmed", email="sara@example.com", plan="Enterprise")
    session.add_all([c1, c2])

    # 2. Insert Orders
    o1 = Order(id="ORD-1034", customer_id="CUS-1001", amount=149.00, status="Delivered")
    o2 = Order(id="ORD-1035", customer_id="CUS-1002", amount=2500.00, status="Processing")
    session.add_all([o1, o2])

    # 3. Insert Payments (Notice duplicate payment on ORD-1034 for testing!)
    p1 = Payment(id="PAY-5001", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00, status="Successful", transaction_id="TXN-8921")
    p2 = Payment(id="PAY-5002", customer_id="CUS-1001", order_id="ORD-1034", amount=149.00, status="Duplicate", transaction_id="TXN-8922")
    p3 = Payment(id="PAY-5003", customer_id="CUS-1002", order_id="ORD-1035", amount=2500.00, status="Successful", transaction_id="TXN-9001")
    session.add_all([p1, p2, p3])

    # 4. Insert Support Ticket
    t1 = SupportTicket(id="TCK-9001", customer_id="CUS-1001", category="Billing", priority="High", status="Open", description="Duplicate charge on ORD-1034")
    session.add(t1)

    session.commit()
    session.close()
    print("Database created and seeded successfully at data/support_system.db!")

if __name__ == "__main__":
    seed_database()