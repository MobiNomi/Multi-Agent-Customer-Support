from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True)  # e.g. CUS-1001
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    plan = Column(String, default="Pro")    # Free, Pro, Enterprise
    created_at = Column(DateTime, default=utcnow)

    orders = relationship("Order", back_populates="customer")
    tickets = relationship("SupportTicket", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True)  # e.g. ORD-1034
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False) # Processing, Delivered, Cancelled
    created_at = Column(DateTime, default=utcnow)

    customer = relationship("Customer", back_populates="orders")
    payments = relationship("Payment", back_populates="order")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True)  # e.g. PAY-5001
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False) # Successful, Refunded, Duplicate
    transaction_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    order = relationship("Order", back_populates="payments")


class SupportTicket(Base):
    __tablename__ = "tickets"

    id = Column(String, primary_key=True)  # e.g. TCK-9001
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    category = Column(String, nullable=False)  # Billing, Technical, General
    priority = Column(String, default="Medium")  # Low, Medium, High, Urgent
    status = Column(String, default="Open")    # Open, Escalated, Closed
    description = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    customer = relationship("Customer", back_populates="tickets")
