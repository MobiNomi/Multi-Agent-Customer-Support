from app.tools.db_tools import get_customer_details, get_order_payments

# Test fetching customer details
cus_res = get_customer_details.invoke({"customer_id": "CUS-1001"})
print("Customer Details:", cus_res)

# Test checking payments for duplicate charge
pay_res = get_order_payments.invoke({"order_id": "ORD-1034"})
print("\nOrder Payments:", pay_res)