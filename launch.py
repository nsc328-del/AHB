"""Open an existing local instance, or run the workbench on loopback."""
import json
import threading
import urllib.request
import webbrowser

URL = "http://127.0.0.1:8787"


def open_when_ready():
    import time
    for _ in range(40):
        try:
            with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
                if json.load(response).get("service") == "video-workbench":
                    webbrowser.open(URL)
                    return
        except Exception:
            time.sleep(.25)


if __name__ == "__main__":
    running = False
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=1) as response:
            running = json.load(response).get("service") == "video-workbench"
    except Exception:
        pass
    if running:
        webbrowser.open(URL)
        print("剪辑工作台已打开：" + URL)
    else:
        import uvicorn
        threading.Thread(target=open_when_ready, daemon=True).start()
        uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)
