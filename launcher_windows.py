"""Entrada Windows: servidor local e janela nativa, sem janela de Terminal."""
import os
import socket
import sys
import time
import threading
import traceback
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

CFG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'GetComics Local'
CFG.mkdir(parents=True, exist_ok=True)
LOG = CFG / 'startup.log'
# O PyInstaller --windowed nao fornece stdout/stderr no Windows.
if getattr(sys, 'frozen', False):
    if sys.stdout is None: sys.stdout = LOG.open('a', encoding='utf-8')
    if sys.stderr is None: sys.stderr = LOG.open('a', encoding='utf-8')


def log(message):
    with LOG.open('a', encoding='utf-8') as out:
        out.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(message) + '\n')


def alert(message):
    log(message)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message + '\n\nRegisto: ' + str(LOG),
                                         'GetComics', 0x10)
    except Exception:
        print(message, file=sys.stderr)


def choose_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start(self_test=False):
    if getattr(sys, 'frozen', False):
        bundle=Path(sys._MEIPASS)
        if (bundle / 'aria2c.exe').exists():
            os.environ['PATH']=str(bundle)+os.pathsep+os.environ.get('PATH','')
    os.environ['GETCOMICS_WEB_PORT'] = str(choose_port())
    import getcomics_web_ui.webui as app
    port = int(os.environ['GETCOMICS_WEB_PORT'])
    address = 'http://127.0.0.1:' + str(port) + '/'
    server = app.ThreadingHTTPServer(('127.0.0.1', port), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(address + 'api/state',timeout=0.5) as res:
                    if res.status == 200: break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError('O servidor local não respondeu no arranque.')
        if self_test:
            with urllib.request.urlopen(address, timeout=3) as response:
                if b'GetComics' not in response.read():
                    raise RuntimeError('Interface HTML ausente ou invalida.')
            if getattr(sys, 'frozen', False):
                executable=Path(sys._MEIPASS) / 'aria2c.exe'
                if not executable.is_file():
                    raise RuntimeError('aria2c nao foi incluido no executavel.')
                result=subprocess.run([str(executable), '--version'], capture_output=True,
                                      timeout=12, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
                if result.returncode:
                    raise RuntimeError('aria2c nao arrancou: '+str(result.returncode))
            log('AUTO-TESTE: OK — API local, HTML e aria2c.')
            return
        try:
            import webview
            webview.create_window('GetComics', address, width=1250, height=840,
                                  min_size=(860, 580))
            webview.start(debug=False)
        except Exception:
            log('Janela nativa indisponível. Tentativa de abrir navegador: ' + traceback.format_exc())
            webbrowser.open(address)
            # Modo de recuperação sem janela nativa. Usar Gestor de Tarefas para terminar.
            while thread.is_alive():
                time.sleep(0.5)
    finally:
        app.shutdown_downloads()
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    try:
        start(self_test='--self-test' in sys.argv)
    except BaseException:
        detail='Falha ao iniciar GetComics:\n' + traceback.format_exc()[-2500:]
        if '--self-test' in sys.argv:
            log(detail)
            sys.exit(1)
        alert(detail)
        sys.exit(1)
