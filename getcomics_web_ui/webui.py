#!/usr/bin/env python3
"""Interface web local para a instalação existente do GetComics Downloader.
Escuta apenas em 127.0.0.1; não expõe a chave Pixeldrain à interface.
"""
import importlib, json, os, re, secrets, sys, threading, time, traceback, zipfile, shutil, tempfile, hashlib, platform
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = (Path(sys._MEIPASS) / 'getcomics_web_ui') if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import requests
from bs4 import BeautifulSoup
from getinfo import GetComics
from pixeldrain_support import is_pixeldrain_url, download_pixeldrain

WEB_PORT = int(os.getenv('GETCOMICS_WEB_PORT', '8765'))
MAX_PAGES = max(1, min(int(os.getenv('GETCOMICS_WEB_MAX_PAGES', '40')), 100))
CONFIG_DIR = (Path(os.getenv('APPDATA', str(Path.home() / 'AppData' / 'Roaming'))) / 'GetComics') if os.name == 'nt' else (Path.home() / 'Library' / 'Application Support' / 'GetComics' if sys.platform == 'darwin' else Path.home() / '.config' / 'getcomics')
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / 'settings.json'
DEFAULTS = {'download_dir':str(Path.home() / 'Downloads' / 'Comics'), 'server_priority':'pixeldrain', 'connections':8, 'max_pages':40}
def load_settings():
    try:
        stored=json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        return {**DEFAULTS, **{k:v for k,v in stored.items() if k in DEFAULTS}}
    except (OSError,ValueError,TypeError):
        return DEFAULTS.copy()
settings = load_settings()
DEST = Path(settings['download_dir']).expanduser().resolve()
DEST.mkdir(parents=True,exist_ok=True)
KEY_FILE=CONFIG_DIR / 'pixeldrain.key'
def load_key():
    if KEY_FILE.is_file():
        os.environ['PIXELDRAIN_API_KEY']=KEY_FILE.read_text(encoding='utf-8').strip()
load_key()
os.environ['PIXELDRAIN_CONNECTIONS']=str(settings['connections'])
def public_settings():
    with lock:
        return {**settings, 'has_api_key':bool(os.environ.get('PIXELDRAIN_API_KEY','').strip())}
def save_settings(obj):
    global DEST, MAX_PAGES
    new=dict(settings)
    path=str(obj.get('download_dir',settings['download_dir'])).strip()
    folder=Path(path).expanduser().resolve()
    if not folder.is_absolute() or len(str(folder))>500:raise ValueError('Pasta inválida')
    folder.mkdir(parents=True,exist_ok=True)
    if not folder.is_dir():raise ValueError('O destino não é uma pasta')
    priority=str(obj.get('server_priority',settings['server_priority']))
    if priority not in ('pixeldrain','direct'):raise ValueError('Prioridade inválida')
    connections=int(obj.get('connections',settings['connections']))
    if not 1<=connections<=16:raise ValueError('As ligações devem estar entre 1 e 16')
    pages=int(obj.get('max_pages',settings['max_pages']))
    if not 1<=pages<=100:raise ValueError('O máximo de páginas deve estar entre 1 e 100')
    new.update(download_dir=str(folder),server_priority=priority,connections=connections,max_pages=pages)
    tmp=CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding='utf-8')
    if os.name!='nt':os.chmod(tmp,0o600)
    tmp.replace(CONFIG_FILE)
    key=obj.get('api_key',None)
    if key is not None:
        if not isinstance(key,str) or len(key)>400:raise ValueError('Chave inválida')
        if key.strip():
            KEY_FILE.write_text(key.strip(),encoding='utf-8')
            if os.name!='nt':os.chmod(KEY_FILE,0o600)
            os.environ['PIXELDRAIN_API_KEY']=key.strip()
    if obj.get('remove_api_key') is True:
        KEY_FILE.unlink(missing_ok=True)
        os.environ.pop('PIXELDRAIN_API_KEY',None)
    with lock:
        settings.update(new)
        DEST=folder
        MAX_PAGES=pages
        state['download_dir']=str(folder)
    os.environ['PIXELDRAIN_CONNECTIONS']=str(connections)
    return public_settings()

TOKEN = secrets.token_urlsafe(24)
lock = threading.RLock()
MAX_PAGES=int(settings['max_pages'])
state = {'search': {'status':'idle','message':'Pronto para pesquisar','pages':0,'count':0}, 'results':[], 'downloads':[], 'download_dir':str(DEST)}
pool = ThreadPoolExecutor(max_workers=3)

def event(kind, **data):
    with lock:
        state[kind].update(data)

def valid_archive(path):
    if not path.exists() or path.stat().st_size < 1024:
        return False, 'Ficheiro inexistente ou demasiado pequeno'
    with path.open('rb') as fh: sig = fh.read(8)
    if path.suffix.lower() in ('.cbz','.zip'):
        if not zipfile.is_zipfile(path): return False, 'O ficheiro não é um arquivo ZIP/CBZ'
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
                if bad: return False, 'Erro CRC no ficheiro: '+bad
                if not any(n.lower().endswith(('.jpg','.jpeg','.png','.webp','.gif','.avif')) for n in z.namelist()):
                    return False, 'CBZ sem imagens reconhecíveis'
        except Exception as exc: return False, 'Arquivo inválido: '+str(exc)
    elif path.suffix.lower() in ('.cbr','.rar'):
        if not sig.startswith(b'Rar!\x1a\x07'): return False, 'O ficheiro não é um arquivo RAR/CBR'
    else: return False, 'Extensão de ficheiro não suportada: '+path.suffix
    return True, 'OK'

def single_issue(title):
    # Utiliza a heurística já instalada quando disponível.
    if hasattr(GetComics, 'is_single_issue'):
        return GetComics.is_single_issue(title)
    if re.search(r'(?i)\b(TPB|omnibus|compendium|collected|collection|hardcover|graphic novel|bundle)\b',title):return False
    return bool(re.search(r'#\s*\d+|\b\d{1,4}\s*\(20\d\d\)',title))

CACHE_DIR = CONFIG_DIR / 'cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def cache_path(query, only_issues):
    key = hashlib.sha256((query.casefold().strip() + '|' + str(only_issues)).encode()).hexdigest()
    return CACHE_DIR / (key + '.json')


def fetch_with_retries(session, url):
    for attempt in range(3):
        try:
            response = session.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=(12,25))
            response.raise_for_status()
            if not response.text.strip():
                raise ValueError('Resposta vazia')
            return response
        except (requests.RequestException, ValueError):
            if attempt == 2: raise
            time.sleep(0.6*(attempt+1))


def issue_key(title):
    # Retira diferenças triviais de pontuação/capitalização sem confundir edições.
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9#]+', ' ', title.casefold())).strip()


def search_job(query, only_issues, refresh=False):
    event('search',status='running',message='A iniciar pesquisa...',pages=0,count=0)
    cachefile = cache_path(query, only_issues)
    try:
        previous = json.loads(cachefile.read_text()) if cachefile.exists() else {}
    except (ValueError, OSError):
        previous = {}
    old_rows = previous.get('rows', [])
    if not refresh and old_rows:
        with lock:
            state['results'] = [dict(r,id=i) for i,r in enumerate(old_rows)]
        event('search',status='done',count=len(old_rows),pages=previous.get('pages',0),message=f'{len(old_rows)} resultados guardados • usa Atualizar para nova pesquisa')
        return
    with lock: state['results']=[]
    session = requests.Session()
    session.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=4))
    found = {}
    incomplete = False
    scanned = 0
    try:
        for page in range(1, MAX_PAGES+1):
            event('search',pages=page,message=f'A pesquisar página {page}… ({len(found)} edições)')
            url = f'https://getcomics.org/page/{page}?s={quote(query)}'
            try:
                response = fetch_with_retries(session,url)
                soup = BeautifulSoup(response.text, 'html.parser')
                articles = soup.find_all('article')
                if not articles and page == 1: raise ValueError('Página inicial sem artigos; o site poderá estar a bloquear a pesquisa')
                if not articles: break
            except Exception as exc:
                incomplete=True
                event('search',message=f'Página {page} falhou: {exc}; a preservar resultados anteriores')
                break
            scanned += 1
            fresh=0
            for article in articles:
                heading=article.find('h1',class_='post-title') or article.find('h1')
                anchor=heading.find('a',href=True) if heading else None
                if not anchor: continue
                title=heading.get_text(' ',strip=True)
                if only_issues and not single_issue(title):continue
                pageurl=anchor['href']
                key=issue_key(title)
                if key not in found:
                    found[key]={'title':title,'page_url':pageurl}
                    fresh+=1
            if not fresh: break
        # Cache de links já resolvidos: não consultar cada URL se não for preciso.
        old_by_key={issue_key(r['title']):r for r in old_rows}
        rows=[]
        for key, item in found.items():
            old=old_by_key.get(key)
            if old:
                row={k:v for k,v in old.items() if k!='id'}
                row['page_url']=item['page_url']
            else:
                row={'title':item['title'],'page_url':item['page_url'],'url':None,'fallback':None,'source':'Por verificar'}
            rows.append(row)
        # Se falhar a pesquisa, nunca apagar silenciosamente resultados conhecidos.
        if incomplete:
            keys={issue_key(r['title']) for r in rows}
            rows += [{k:v for k,v in old.items() if k!='id'} for old in old_rows if issue_key(old['title']) not in keys]
        if not rows and old_rows: rows=[{k:v for k,v in r.items() if k!='id'} for r in old_rows]
        rows.sort(key=lambda r: (int(re.search(r'#\s*(\d+)',r['title']).group(1)) if re.search(r'#\s*(\d+)',r['title']) else 10**8, r['title'].casefold()))
        with lock:state['results']=[dict(r,id=i) for i,r in enumerate(rows)]
        temp=cachefile.with_suffix('.tmp')
        temp.write_text(json.dumps({'pages':scanned,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(cachefile)
        event('search',status='done',pages=scanned,count=len(rows),message=f'{len(rows)} edições, {scanned} páginas' + (' • pesquisa parcial; resultados anteriores preservados' if incomplete else ' • resultados guardados'))
    except Exception as exc:
        if old_rows:
            with lock:state['results']=[dict(r,id=i) for i,r in enumerate(old_rows)]
            event('search',status='done',count=len(old_rows),message=f'Falhou a pesquisa ({exc}); a mostrar resultados guardados')
        else:event('search',status='error',message=str(exc))
    finally:session.close()


def resolve_link(row):
    if row.get('url') and row.get('source') not in ('Por verificar',None):
        if settings['server_priority']=='direct' and is_pixeldrain_url(row['url']) and row.get('fallback'):
            row={**row,'url':row['fallback'],'source':'Directo'}
        return row
    comic=GetComics('',1,False)
    comic.page_links={row['page_url']:row['title']}
    comic.get_download_links()
    if not comic.comic_links:
        raise ValueError('Nenhum link de transferência disponível nesta edição')
    url=next(iter(comic.comic_links))
    alt=getattr(comic,'fallback_links',{}).get(url)
    if settings['server_priority']=='direct' and alt and is_pixeldrain_url(url):
        url,alt=alt,url
    row.update(url=url,source=('Pixeldrain' if is_pixeldrain_url(url) else 'MediaFire' if url.startswith('_MEDIAFIRE_') else 'Directo'),fallback=alt)
    # Actualizar a lista em memória, de forma a não resolver duas vezes.
    with lock:
        for entry in state['results']:
            if entry.get('page_url')==row['page_url']:
                entry.update(url=row['url'],source=row['source'],fallback=row['fallback'])
    return row

def safe_name(value):
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_', Path(value).name).strip()
    return name if name not in ('','.','..') else 'ficheiro.cbz'

def target_for(name,download_dir):
    base=download_dir/safe_name(name)
    n=1
    while base.exists() or base.with_name(base.name+'.part').exists():
        base=download_dir / (f'{Path(name).stem} ({n}){Path(name).suffix}')
        n+=1
    return base

def direct_download(url,download_dir):
    # Seguir apenas ligações HTTP(S), sem aceitar file:// ou outros protocolos.
    if urlparse(url).scheme not in ('http','https'):raise ValueError('Ligação inválida')
    with requests.get(url,stream=True,timeout=(15,120),headers={'User-Agent':'Mozilla/5.0'}) as response:
        response.raise_for_status()  # Impede a criação de CBZ a partir de erros HTTP 403.
        ctype=response.headers.get('Content-Type','').lower()
        if any(x in ctype for x in ('text/html','application/json','text/plain')):
            raise ValueError('Servidor devolveu uma página de erro, não um CBZ')
        filename=requests.utils.unquote(response.url.rstrip('/').split('/')[-1].split('?')[0]); target=target_for(filename,download_dir)
        temp=target.with_name(target.name+'.part')
        try:
            with temp.open('wb') as out:
                for chunk in response.iter_content(1024*256):
                    if chunk:out.write(chunk)
            # A extensão .part não altera o conteúdo; verificar com extensão final temporária.
            probe=target.with_name(target.stem+'.verificar'+target.suffix)
            temp.replace(probe)
            good,reason=valid_archive(probe)
            if not good:
                probe.unlink(missing_ok=True)
                raise ValueError(reason)
            probe.replace(target)
            return target
        finally:
            temp.unlink(missing_ok=True)

def download_one(row,idx,download_dir):
    with lock:state['downloads'][idx].update(status='running',message='A verificar servidor…')
    try:
        row=resolve_link(row)
        with lock:state['downloads'][idx].update(source=row['source'],message='A descarregar de '+row['source'])
        url=row['url']
        if is_pixeldrain_url(url):
            path=Path(download_pixeldrain(url,download_dir))
            good,reason=valid_archive(path)
            if not good:
                quarantine=path.with_name(path.name+'.invalido')
                path.replace(quarantine)
                raise ValueError(f'{reason}. Ficheiro isolado: {quarantine.name}')
        elif url.startswith('_MEDIAFIRE_'):
            raise ValueError('MediaFire requer download manual: '+url[len('_MEDIAFIRE_'):])
        else:
            # Um 403 é uma recusa do servidor. Não guardar página de erro como CBZ.
            try: path=direct_download(url,download_dir)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code==403:
                    raise ValueError('Servidor directo recusou o pedido (HTTP 403). Não foi criado nenhum CBZ. Abre a página do artigo para verificar outro servidor.') from exc
                raise
        with lock:state['downloads'][idx].update(status='done',message='Ficheiro validado',filename=path.name)
    except Exception as exc:
        with lock:state['downloads'][idx].update(status='error',message=str(exc))

def download_job(ids):
    with lock:
        rows=[state['results'][i].copy() for i in ids]
        busy={issue_key(d['title']) for d in state['downloads'] if d['status'] in ('queued','running','done')}
        rows=[r for r in rows if issue_key(r['title']) not in busy]
        offset=len(state['downloads'])
        state['downloads'].extend([{'title':r['title'],'source':r['source'],'status':'queued','message':'Em espera'} for r in rows])
    download_dir=DEST
    for j,row in enumerate(rows):download_one(row,offset+j,download_dir)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def respond(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/':
            html=(HERE/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
        elif self.path=='/api/settings':
            self.respond(public_settings())
        elif self.path=='/api/state':
            with lock:self.respond({**state,'token':TOKEN})
        else:self.respond({'error':'Não encontrado'},404)
    def do_POST(self):
        try:
            if self.headers.get('X-App-Token')!=TOKEN:return self.respond({'error':'Acesso recusado'},403)
            size=int(self.headers.get('Content-Length',0))
            if size>100000:return self.respond({'error':'Pedido demasiado grande'},413)
            obj=json.loads(self.rfile.read(size) or b'{}')
            if self.path=='/api/settings':
                return self.respond(save_settings(obj))
            if self.path=='/api/choose-folder':
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    picked=[]
                    def choose():
                        root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
                        picked.append(filedialog.askdirectory(initialdir=str(DEST),title='Escolher pasta de downloads'))
                        root.destroy()
                    thread=threading.Thread(target=choose);thread.start();thread.join(timeout=60)
                    return self.respond({'path':picked[0] if picked else ''})
                except Exception as e:
                    return self.respond({'error':'Selecção gráfica indisponível; escreve o caminho manualmente: '+str(e)},503)
            if self.path=='/api/search':
                query=str(obj.get('query','')).strip()[:150]
                if not query:return self.respond({'error':'Pesquisa vazia'},400)
                with lock:
                    if state['search']['status']=='running':return self.respond({'error':'Pesquisa em curso'},409)
                    state['search']={'status':'running','message':'A iniciar…','pages':0,'count':0}
                pool.submit(search_job,query,bool(obj.get('only_issues',True)),bool(obj.get('refresh',False)))
                return self.respond({'ok':True})
            if self.path=='/api/download':
                ids=obj.get('ids',[])
                with lock:total=len(state['results'])
                if not isinstance(ids,list) or len(ids)>500 or not all(type(i) is int and 0<=i<total for i in ids):return self.respond({'error':'Selecção inválida'},400)
                if not ids:return self.respond({'error':'Sem selecção'},400)
                pool.submit(download_job,list(dict.fromkeys(ids)))
                return self.respond({'ok':True})
            self.respond({'error':'Não encontrado'},404)
        except Exception as exc:self.respond({'error':str(exc)},500)

if __name__=='__main__':
    print('GetComics Web UI — apenas neste Mac')
    print(f'Abre http://127.0.0.1:{WEB_PORT} no navegador')
    print('Destino: '+str(DEST))
    print('Para parar: Ctrl+C')
    ThreadingHTTPServer(('127.0.0.1',WEB_PORT),Handler).serve_forever()
