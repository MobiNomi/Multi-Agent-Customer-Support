"""Chat with the live graph in the terminal: `python -m scripts.chat_cli --customer CUS-1001`.

Shows which node handled each step and any tool calls, so you can watch the routing.
"""
import argparse
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.database.connection import init_db
from app.database.seed import seed_if_empty
from app.graph.workflow import build_support_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", default="CUS-1001")
    args = parser.parse_args()

    init_db()
    seed_if_empty()
    graph = build_support_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"{args.customer}:{uuid.uuid4().hex}"}, "recursion_limit": 12}
    print(f"Chatting as {args.customer}. Ctrl+C or 'exit' to quit.\n")

    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit"}:
            break
        if not text:
            continue
        turn = {"messages": [HumanMessage(content=text)], "customer_id": args.customer,
                "ticket_id": None, "needs_human": False}
        for update in graph.stream(turn, config, stream_mode="updates"):
            for node, delta in update.items():
                if node == "supervisor":
                    print(f"  [router] intent={delta['intent']} confidence={delta['confidence']:.2f}")
                for msg in (delta or {}).get("messages", []):
                    if isinstance(msg, AIMessage) and msg.tool_calls:
                        for c in msg.tool_calls:
                            print(f"  [{node}] calls {c['name']}({c['args']})")
                    elif isinstance(msg, ToolMessage):
                        print(f"  [tool] {msg.name} -> {msg.content[:160]}")
                    elif isinstance(msg, AIMessage) and msg.content:
                        print(f"bot ({node}) > {msg.content}\n")


if __name__ == "__main__":
    main()
