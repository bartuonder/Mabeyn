import asyncio
import sys


def use_selector_loop_on_windows() -> None:
    """psycopg's async mode cannot run on the default Windows proactor event loop."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
