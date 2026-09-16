from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from app.graph.agents import billing_agent_node

load_dotenv()

initial_state = {
    "messages": [HumanMessage(content="I was charged twice for order ORD-1034. Can you check?")],
    "customer_id": "CUS-1001",
    "intent": "billing",
    "assigned_agent": None,
    "confidence": 0.95,
    "tool_results": {},
    "resolution": None,
    "needs_human": False,
    "ticket_id": None
}

result_state = billing_agent_node(initial_state)
last_msg = result_state["messages"][-1]

print("Agent Response Type:", type(last_msg))
if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
    print("Agent requested tool call:", last_msg.tool_calls[0]['name'])
    print("Tool Arguments:", last_msg.tool_calls[0]['args'])
else:
    print("Agent Output:", last_msg.content)