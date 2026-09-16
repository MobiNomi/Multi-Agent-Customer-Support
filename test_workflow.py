from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from app.graph.workflow import build_support_graph

load_dotenv()

graph = build_support_graph()

initial_input = {
    "messages": [HumanMessage(content="I was charged twice on my card for order ORD-1034. Can you refund the duplicate charge?")],
    "customer_id": "CUS-1001"
}

print("Running Multi-Agent Workflow...\n")
output = graph.invoke(initial_input)

print("--- FINAL GRAPH RESPONSE ---")
for msg in output["messages"]:
    print(f"[{msg.__class__.__name__}]: {msg.content}\n")