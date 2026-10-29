"""Arranque autónomo com janela nativa e diagnóstico local."""
import os
import sys
import time
import socket
import runpy
import threading
import traceback
import urllib.request
import webbrowser
from pathlib import Path

import getinfo  # noqa
import download  # noqa
import menu  # noqa
import pixeldrain_support  # noqa

def config_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GetComics"
    if os.name == "nt":
        return Path(os.getenv("APPDATA", str(Path.home()))) / "GetComics"
    return Path.home() / ".config" / "getcomics"

DIR = config_dir()
DIR.mkdir(parents=True, exist_ok=True)
LOG = DIR / "startup.log"

def note(message):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(message) + "\n")

def fail(message):
    note(message)
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("GetComics", message + "\n\nDiagnóstico: " + str(LOG))
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)

def main():
    note("A iniciar GetComics")
    if getattr(sys, "frozen", False):
        bundle = Path(sys._MEIPASS)
        binary = "aria2c.exe" if os.name == "nt" else "aria2c"
        if (bundle / binary).is_file():
            os.environ["PATH"] = str(bundle) + os.pathsep + os.environ.get("PATH", "")
    # Credenciais continuam fora da aplicação; nunca são incluídas no executável.
    if not os.environ.get("PIXELDRAIN_API_KEY"):
        candidates = [DIR / "pixeldrain.key", Path.home() / ".config" / "getcomics" / ".env",
                      Path.home() / "getcomics-downloader" / ".env"]
        for f in candidates:
            if f.is_file():
                if f.name == "pixeldrain.key":
                    key = f.read_text(encoding="utf-8").strip()
                else:
                    key = next((s.partition("=")[2].strip().strip("\\\"'") for s in f.read_text(encoding="utf-8").splitlines()
                                if s.strip().startswith("PIXELDRAIN_API_KEY=")), "")
                if key:
                    os.environ["PIXELDRAIN_API_KEY"] = key
                    break

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    os.environ["GETCOMICS_WEB_PORT"] = str(port)
    url = "http://127.0.0.1:" + str(port) + "/"
    errors = []

    def serve():
        try:
            runpy.run_module("getcomics_web_ui.webui", run_name="__main__")
        except BaseException:
            detail = traceback.format_exc()
            errors.append(detail)
            note(detail)

    server = threading.Thread(target=serve, name="GetComicsHTTP", daemon=True)
    server.start()
    for _ in range(120):
        if errors:
            fail("O servidor interno não conseguiu arrancar.")
            return
        try:
            with urllib.request.urlopen(url + "api/state", timeout=0.35) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.15)
    else:
        fail("O servidor interno não respondeu. Verifica o ficheiro de diagnóstico.")
        return

    try:
        import webview
        webview.create_window("GetComics", url, width=1280, height=830, min_size=(850, 580))
        webview.start(debug=False)
    except Exception:
        note("A janela nativa falhou; a abrir no navegador:\n" + traceback.format_exc())
        webbrowser.open(url)
        # Mantém o servidor vivo enquanto estiver em modo navegador.
        try:
            while server.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    try:
        main()
    except BaseException:
        fail("Erro ao iniciar GetComics:\n" + traceback.format_exc())
