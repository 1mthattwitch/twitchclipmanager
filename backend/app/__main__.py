"""python -m app  ->  start the server and open the browser."""
import os
import threading
import webbrowser

import uvicorn

PORT = int(os.environ.get("TCM_PORT", "8765"))

if __name__ == "__main__":
    if os.environ.get("TCM_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    print(f"Clip Manager running at http://localhost:{PORT}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="warning")
