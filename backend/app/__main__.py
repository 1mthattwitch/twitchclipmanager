"""python -m app  ->  start the server and open the browser."""
import os
import threading
import webbrowser

import uvicorn

PORT = int(os.environ.get("TCM_PORT", "8765"))


def open_tabs(opener=webbrowser.open) -> list[str]:
    """The help chat (if turned on), then the app. TCM_NO_BROWSER=1 opens nothing."""
    if os.environ.get("TCM_NO_BROWSER") == "1":
        return []
    urls = []
    try:
        from . import config
        s = config.get_settings()
        if s.open_chat_on_start and s.help_chat_url.startswith("https://"):
            urls.append(s.help_chat_url)
    except Exception:
        pass  # a broken settings file must never stop the app opening
    urls.append(f"http://localhost:{PORT}")
    for url in urls:
        opener(url)
    return urls


if __name__ == "__main__":
    threading.Timer(1.5, open_tabs).start()
    print(f"Clip Manager running at http://localhost:{PORT}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")
