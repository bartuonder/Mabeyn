import uvicorn

from mabeyn.core.loop import use_selector_loop_on_windows


def main() -> None:
    use_selector_loop_on_windows()
    # loop="none" keeps the event loop policy chosen above (psycopg needs the selector loop).
    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000, loop="none")


if __name__ == "__main__":
    main()
