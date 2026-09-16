from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from app.graph.router import supervisor_router_node

load_dotenv()

sample_state = {
    "messages": [HumanMessage(content="I was charged twice on my credit card for order ORD-1034")]
}

res = supervisor_router_node(sample_state)
print("Router Output:", res)