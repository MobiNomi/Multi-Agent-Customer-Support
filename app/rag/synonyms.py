"""Query expansion: customers use different words than the documents.

Keyword search only matches words that literally appear in a chunk, so "yearly" never finds "annual".
When a query contains a word on the left, the words on the right are added to the search.
Keep this list small and specific to your documents; add a line whenever the eval shows a miss.
"""
QUERY_SYNONYMS = {
    "yearly": "annual",
    "year": "annual",
    "cost": "price",
    "costs": "price",
    "pricing": "price",
    "money": "refund",
    "reimburse": "refund",
    "reimbursement": "refund",
    "billed": "charged",
    "twice": "duplicate",
    "double": "duplicate",
    "reply": "response",
    "replies": "response",
    "respond": "response",
    "pay": "payment",
    "paying": "payment",
    "pkr": "rupees",
    "login": "log",
    "signin": "log",
    "2fa": "two-factor",
    "europe": "eu",
    "european": "eu",
    "unsubscribe": "cancel",
}
