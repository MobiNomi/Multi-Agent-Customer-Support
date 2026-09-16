import uuid
from typing import Dict, Any, List
from langchain_core.tools import tool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.models import Customer, Order, Payment, SupportTicket

DB_PATH = "sqlite:///data/support_system.db"

def get_db_session():
    engine = create_engine(DB_PATH)
    Session = sessionmaker(bind=engine)
    return Session()


@tool
def get_customer_details(customer_id: str) -> Dict[str, Any]:
    """Fetch customer profile, recent orders, and open support tickets using customer_id (e.g. CUS-1001)."""
    session = get_db_session()
    try:
        customer = session.query(Customer).filter_by(id=customer_id).first()
        if not customer:
            return {"error": f"No customer found with ID {customer_id}"}
        
        orders = [{"order_id": o.id, "amount": o.amount, "status": o.status} for o in customer.orders]
        tickets = [{"ticket_id": t.id, "category": t.category, "status": t.status} for t in customer.tickets]

        return {
            "customer_id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "plan": customer.plan,
            "orders": orders,
            "tickets": tickets
        }
    finally:
        session.close()


@tool
def get_order_payments(order_id: str) -> Dict[str, Any]:
    """Retrieve all payment ledger entries associated with a specific order_id (e.g. ORD-1034)."""
    session = get_db_session()
    try:
        order = session.query(Order).filter_by(id=order_id).first()
        if not order:
            return {"error": f"Order {order_id} not found."}

        payments = session.query(Payment).filter_by(order_id=order_id).all()
        payment_list = [
            {
                "payment_id": p.id,
                "amount": p.amount,
                "status": p.status,
                "transaction_id": p.transaction_id,
                "created_at": str(p.created_at)
            }
            for p in payments
        ]

        return {
            "order_id": order_id,
            "order_status": order.status,
            "total_order_amount": order.amount,
            "payment_records_count": len(payments),
            "payments": payment_list
        }
    finally:
        session.close()


@tool
def create_support_ticket(customer_id: str, category: str, description: str, priority: str = "High") -> Dict[str, Any]:
    """Create a new support ticket in the database for human escalation or tracking."""
    session = get_db_session()
    try:
        new_id = f"TCK-{uuid.uuid4().hex[:4].upper()}"
        ticket = SupportTicket(
            id=new_id,
            customer_id=customer_id,
            category=category,
            priority=priority,
            status="Escalated",
            description=description
        )
        session.add(ticket)
        session.commit()
        return {
            "status": "success",
            "ticket_id": new_id,
            "message": f"Support ticket {new_id} created and marked as Escalated."
        }
    except Exception as e:
        session.rollback()
        return {"error": f"Failed to create ticket: {str(e)}"}
    finally:
        session.close()