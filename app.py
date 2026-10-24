from pathlib import Path

from waitress import serve

from looked.config import Settings
from looked.web import create_app


def main():
    try:
        settings = Settings.load(Path(__file__).resolve().parent)
    except ValueError as error:
        print(str(error))  # only fixed configuration names, never values
        return 1
    app = create_app(settings)
    print(f"Looked is running at http://127.0.0.1:{settings.port}", flush=True)
    print("Press Ctrl+C in this window to stop. No search runs until you click Search.", flush=True)
    if not settings.key_configured:
        print("Add SERPAPI_API_KEY to .env and restart to enable new searches.", flush=True)
    try:
        serve(app, host="127.0.0.1", port=settings.port, threads=6, clear_untrusted_proxy_headers=True,
              max_request_body_size=1_100_000, channel_timeout=180, ident="Looked")
    except OSError:
        print("Cannot open the local port. Close another copy of Looked or change PORT in .env.")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
