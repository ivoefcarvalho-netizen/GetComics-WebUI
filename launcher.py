"""Entrada da aplicação empacotada. Não inclui credenciais."""
import os
import sys
import runpy
import threading
import webbrowser
from pathlib import Path

# As importações explícitas permitem ao PyInstaller descobrir estes módulos.
import getinfo  # noqa
import download  # noqa
import menu  # noqa
import pixeldrain_support  # noqa

if getattr(sys, 'frozen', False):
    bundle = Path(sys._MEIPASS)
    exe_dir = Path(sys.executable).resolve().parent
    aria_name = 'aria2c.exe' if os.name == 'nt' else 'aria2c'
    aria_bin = bundle / aria_name
    if aria_bin.is_file():
        os.environ['PATH'] = str(bundle) + os.pathsep + os.environ.get('PATH', '')

# Carrega a chave apenas de configuração externa, sem a guardar no executável.
if not os.environ.get('PIXELDRAIN_API_KEY'):
    options = [Path.home() / '.config' / 'getcomics' / '.env',
               Path.home() / 'getcomics-downloader' / '.env']
    for f in options:
        if f.is_file():
            for line in f.read_text(encoding='utf-8').splitlines():
                if line.strip().startswith('PIXELDRAIN_API_KEY='):
                    os.environ['PIXELDRAIN_API_KEY'] = line.partition('=')[2].strip().strip('"\'')
                    break
            if os.environ.get('PIXELDRAIN_API_KEY'):
                break

# Abre a interface automaticamente. O servidor só aceita ligações locais.
port = int(os.environ.get('GETCOMICS_WEB_PORT', '8765'))
threading.Timer(1.7, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()
runpy.run_module('getcomics_web_ui.webui', run_name='__main__')
