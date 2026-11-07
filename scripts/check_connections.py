"""Check every external connection: `python -m scripts.check_connections`."""
import sys

from app.config import get_settings
from app.database.connection import check_db, get_engine, init_db, session_scope
from app.database.models import Customer
from app.llm import check_llm
from app.memory import count_conversations, open_checkpointer


def main() -> int:
    settings = get_settings()
    ok = True

    url = get_engine().url.render_as_string(hide_password=True)
    if check_db():
        init_db()
        with session_scope() as s:
            customers = s.query(Customer).count()
        print(f"[ok]   database  {url}  ({customers} customers)")
        if customers == 0:
            print("       empty: run `python -m app.database.seed` or start the API with SEED_DEMO_DATA=true")
    else:
        ok = False
        print(f"[FAIL] database  {url}  unreachable")

    try:
        with open_checkpointer() as saver:
            n = count_conversations(saver)
        where = settings.memory_db_path if settings.memory_backend == "sqlite" else "RAM (lost on restart)"
        print(f"[ok]   memory    {settings.memory_backend}  {where}  ({n} saved conversations)")
    except Exception as exc:
        ok = False
        print(f"[FAIL] memory    {settings.memory_backend}  {type(exc).__name__}: {exc}")

    try:
        reply = check_llm()
        print(f"[ok]   groq      model={settings.llm_model}  reply={reply!r}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] groq      model={settings.llm_model}  {type(exc).__name__}: {exc}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
