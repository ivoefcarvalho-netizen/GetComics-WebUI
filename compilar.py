"""Compila GetComics para o sistema operativo actual, sem incluir credenciais."""
from pathlib import Path
import os, sys, platform, shutil, subprocess
ROOT=Path(__file__).resolve().parent
needed=['getinfo.py','download.py','menu.py','pixeldrain_support.py']
missing=[x for x in needed if not (ROOT/x).is_file()]
if missing: sys.exit('Faltam módulos personalizados: '+', '.join(missing)+' — executa preparar_fontes.sh no Mac.')
if (ROOT/'.env').exists() or (ROOT/'pixeldrain.key').exists():
    sys.exit('ERRO: credenciais encontradas na pasta de compilação. Remove-as antes de compilar.')
aria=shutil.which('aria2c')
if not aria: sys.exit('aria2c não encontrado; instala aria2 antes de compilar.')
# Os módulos do motor devem estar na raiz, ao lado do launcher.
separator=';' if os.name=='nt' else ':'
cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','GetComics',
     '--paths',str(ROOT),'--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build'),
     '--specpath',str(ROOT), '--add-data',str(ROOT/'getcomics_web_ui'/'index.html')+separator+'getcomics_web_ui',
     '--add-binary',str(aria)+separator+'.',
     '--hidden-import','getcomics_web_ui.webui','--hidden-import','getinfo','--hidden-import','download',
     '--hidden-import','menu','--hidden-import','pixeldrain_support','--collect-all','certifi','--collect-all','rich', '--collect-all','webview']
if platform.system() == 'Darwin': cmd.append('--windowed')
else: cmd.append('--console')
cmd.append(str(ROOT/'launcher.py'))
subprocess.run(cmd, check=True, cwd=ROOT)
print('Compilação concluída:',ROOT/'dist')
