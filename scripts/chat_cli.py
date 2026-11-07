"""Chat with the live graph in the terminal.

    python -m scripts.chat_cli --customer CUS-1001                # new conversation
    python -m scripts.chat_cli --customer CUS-1001 --session abc  # resume a saved one

Shows which node handled each step and any tool calls, so you can watch the routing.
"""
import argparse
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.config import get_settings
from app.database.connection import init_db
from app.database.seed import seed_if_empty
from app.graph.workflow import build_support_graph
from app.memory import open_checkpointer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", default="CUS-1001")
    parser.add_argument("--session", default=None, help="session ID to resume (default: start a new one)")
    args = parser.parse_args()

    init_db()
    seed_if_empty()
    session = args.session or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": f"{args.customer}:{session}"},
              "recursion_limit": get_settings().max_graph_steps}

    with open_checkpointer() as checkpointer:
        graph = build_support_graph(checkpointer=checkpointer)
        previous = graph.get_state(config).values.get("messages", [])
        status = f"resuming ({len(previous)} saved messages)" if previous else "new conversation"
        print(f"Chatting as {args.customer}, session {session}: {status}. Type 'exit' to quit.\n")

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

    print(f"\nResume later with: python -m scripts.chat_cli --customer {args.customer} --session {session}")


if __name__ == "__main__":
    main()
