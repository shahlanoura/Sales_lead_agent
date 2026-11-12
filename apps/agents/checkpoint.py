from django.conf import settings

_saver = None


def get_checkpointer():
    """Postgres in prod (survives restarts, shared by workers). MemorySaver only for single-process dev."""
    global _saver
    if _saver is None:
        if settings.PG_CONNINFO:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool
            pool = ConnectionPool(settings.PG_CONNINFO, max_size=10,
                                  kwargs={"autocommit": True, "prepare_threshold": 0})
            _saver = PostgresSaver(pool)
            _saver.setup()
        else:
            from langgraph.checkpoint.memory import MemorySaver
            _saver = MemorySaver()
    return _saver
