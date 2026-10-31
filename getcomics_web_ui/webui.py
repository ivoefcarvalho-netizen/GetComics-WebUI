#!/usr/bin/env python3
"""Interface web local para o GetComics Desktop (macOS/Windows).
Escuta apenas em 127.0.0.1; não expõe a chave Pixeldrain à interface.
"""
import importlib, json, os, re, secrets, sys, threading, time, traceback, zipfile, shutil, tempfile, hashlib, subprocess
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote, urljoin, parse_qs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = ((Path(sys._MEIPASS) / 'getcomics_web_ui') if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent)
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import requests
from bs4 import BeautifulSoup
from getinfo import GetComics
from pixeldrain_support import is_pixeldrain_url, download_pixeldrain
from download_controls import PAUSE, DownloadStopped
from queue_store import save_queue, load_queue
from secretstash import parse_wishlist, best_match, canonical
from getcomics_page import (parse_article, parse_listing, extract_tags, tag_slug,
                             tag_page_url, valid_article_url)

WEB_PORT = int(os.getenv('GETCOMICS_WEB_PORT', '8765'))
MAX_PAGES = max(1, min(int(os.getenv('GETCOMICS_WEB_MAX_PAGES', '40')), 100))
# Configuração persistente e individual, fora da pasta da aplicação.
CONFIG_DIR = ((Path(os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming'))) / 'GetComics Local')
              if os.name == 'nt' else Path.home() / 'Library' / 'Application Support' / 'GetComics Local')
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / 'settings.json'
KEY_FILE = CONFIG_DIR / ('pixeldrain.dat' if os.name == 'nt' else 'pixeldrain.key')
KEY_MIGRATED = CONFIG_DIR / '.api_key_migrated'
QUEUE_FILE = CONFIG_DIR / 'download_queue.json'
STASH_FILE = CONFIG_DIR / 'secretstash_wishlist.json'

def _save_api_key(key):
    """Credenciais fora da pasta distribuída, com permissões apenas do utilizador."""
    if not isinstance(key, str) or len(key) > 400 or '\n' in key or '\r' in key:
        raise ValueError('Chave API inválida.')
    key = key.strip()
    if not key:
        raise ValueError('Introduz uma chave API.')
    tmp = KEY_FILE.with_suffix('.tmp')
    if os.name == 'nt':
        from windows_secret import protect
        tmp.write_bytes(protect(key.encode('utf-8')))
    else:
        with tmp.open('w', encoding='utf-8') as out:
            os.chmod(tmp, 0o600)
            out.write(key)
    tmp.replace(KEY_FILE)
    if os.name != 'nt': os.chmod(KEY_FILE, 0o600)
    os.environ['PIXELDRAIN_API_KEY'] = key
    KEY_MIGRATED.touch(exist_ok=True)

def _load_api_key():
    # Importação única da instalação antiga; após apagar não volta a surgir.
    os.environ['PIXELDRAIN_CREDENTIALS_MANAGED'] = '1'
    if KEY_FILE.is_file():
        if os.name == 'nt':
            from windows_secret import unprotect
            key = unprotect(KEY_FILE.read_bytes()).decode('utf-8').strip()
        else:
            key = KEY_FILE.read_text(encoding='utf-8').strip()
        if key:
            os.environ['PIXELDRAIN_API_KEY'] = key
        return
    if KEY_MIGRATED.exists():
        os.environ.pop('PIXELDRAIN_API_KEY', None)
        return
    old_key = os.environ.get('PIXELDRAIN_API_KEY', '').strip()
    legacy_env = ROOT / '.env'
    if not old_key and legacy_env.exists():
        for line in legacy_env.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('PIXELDRAIN_API_KEY='):
                old_key = line.partition('=')[2].strip().strip('"\'')
                break
    if old_key:
        _save_api_key(old_key)

_load_api_key()

def _remove_api_key():
    KEY_FILE.unlink(missing_ok=True)
    os.environ.pop('PIXELDRAIN_API_KEY', None)
    KEY_MIGRATED.touch(exist_ok=True)

DEFAULT_SETTINGS = {'download_dir': str(Path.home() / 'Downloads' / 'Comics'),
                    'server_priority': 'direct', 'max_pages': 40, 'connections': 8}
try:
    config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    if not isinstance(config, dict): raise ValueError('Configuração inválida')
except (OSError, ValueError):
    config = {}
settings = {**DEFAULT_SETTINGS, **{k:v for k,v in config.items() if k in DEFAULT_SETTINGS}}
if os.getenv('GETCOMICS_WEB_DOWNLOAD_DIR') and not CONFIG_FILE.exists():
    settings['download_dir'] = os.environ['GETCOMICS_WEB_DOWNLOAD_DIR']
DEST = Path(settings['download_dir']).expanduser().resolve()
DEST.mkdir(parents=True, exist_ok=True)
MAX_PAGES = int(settings['max_pages'])
os.environ['PIXELDRAIN_CONNECTIONS'] = str(settings['connections'])

TOKEN = secrets.token_urlsafe(24)
lock = threading.RLock()
state = {'search': {'status':'idle','message':'Pronto para pesquisar','pages':0,'count':0}, 'results':[], 'downloads':[], 'download_dir':str(DEST), 'tags': [], 'article_url': '',
         'stash': {'status':'idle', 'message':'Importa um CSV do Secret Stash para começar.',
                   'items':[], 'processed':0, 'total':0}}
pool = ThreadPoolExecutor(max_workers=2)
download_pool = ThreadPoolExecutor(max_workers=1)  # uma fila de downloads de cada vez
queued_lock = threading.Lock()
scheduled_indices = set()

# Carregamento antes de abrir o servidor; não iniciar transferências automaticamente.
try:
    state['downloads'] = load_queue(QUEUE_FILE)
except (OSError, ValueError, TypeError) as exc:
    # Não substituir um ficheiro eventualmente recuperável com dados incompletos.
    print('Aviso: fila anterior indisponível:', exc, flush=True)
    if QUEUE_FILE.exists():
        try:
            # Nunca substituir silenciosamente uma fila anterior corrompida.
            backup = CONFIG_DIR / ('download_queue.corrupt.' + str(int(time.time())) + '.json')
            QUEUE_FILE.replace(backup)
            print('Cópia de recuperação da fila:', backup, flush=True)
        except OSError as backup_error:
            print('Não foi possível preservar a fila danificada:', backup_error, flush=True)
    state['downloads'] = []



# A lista de desejos é persistente e independente da fila de downloads.
# As pesquisas nunca são retomadas automaticamente após reiniciar o programa.
try:
    _stash_data = json.loads(STASH_FILE.read_text(encoding='utf-8'))
    _saved_items = _stash_data.get('items', []) if _stash_data.get('version') == 1 else []
    if not isinstance(_saved_items, list): raise ValueError('Lista guardada inválida')
    _saved_items = [item for item in _saved_items[:1000] if isinstance(item, dict)
                    and isinstance(item.get('series'), str) and isinstance(item.get('number'), int)
                    and isinstance(item.get('year'), int)]
    state['stash'] = {'status':'idle', 'message':f'{len(_saved_items)} desejos recuperados. Podes continuar a pesquisa.',
                      'items':_saved_items, 'processed':sum(x.get('status') != 'pending' for x in _saved_items),
                      'total':len(_saved_items)}
except FileNotFoundError:
    pass
except (ValueError, TypeError, OSError, json.JSONDecodeError) as _exc:
    print('Aviso: lista Secret Stash anterior não pôde ser lida:', _exc, flush=True)


def persist_stash():
    with lock:
        payload = {'version':1, 'items':state['stash']['items']}
        tmp = STASH_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        if os.name != 'nt': os.chmod(tmp, 0o600)
        tmp.replace(STASH_FILE)
        if os.name != 'nt': os.chmod(STASH_FILE, 0o600)


def update_stash_item(index, **changes):
    with lock:
        state['stash']['items'][index].update(changes)
        state['stash']['processed'] = sum(x.get('status') != 'pending' for x in state['stash']['items'])
        persist_stash()


def stash_fetch_series(session, series, wanted, refresh=False):
    """Uma pesquisa por SÉRIE (não por edição), com cache e ritmo moderado."""
    key = hashlib.sha256(canonical(series).encode('utf-8')).hexdigest()
    cache = CACHE_DIR / ('secretstash_' + key + '.json')
    if not refresh:
        try:
            prev = json.loads(cache.read_text(encoding='utf-8'))
            if isinstance(prev.get('rows'), list) and (prev.get('complete') or
                all(best_match(item, prev['rows']) for item in wanted)):
                return prev['rows'], True, int(prev.get('pages', 0))
        except (OSError, ValueError, TypeError):
            pass
    found, seen_pages = {}, set()
    completed = False
    exhaustive = False
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        if page > 1: time.sleep(0.7)  # Respeitar a fonte e evitar rajadas.
        url = f'https://getcomics.org/page/{page}?s={quote(series)}'
        response = fetch_with_retries(session, url)
        listing = parse_listing(response.text)
        if not listing:
            completed = True
            exhaustive = True
            break
        fingerprint = tuple(sorted(x['page_url'] for x in listing))
        if fingerprint in seen_pages:
            completed = True
            exhaustive = True
            break
        seen_pages.add(fingerprint)
        pages = page
        for row in listing:
            found[row['page_url']] = row
        # Deixar de percorrer páginas quando já encontrámos todas as edições.
        if all(best_match(item, list(found.values())) for item in wanted):
            completed = True
            break
    rows = list(found.values())
    # Guardar apenas pesquisas completas. Em caso de 429, nunca confundir
    # uma pesquisa parcial com 'não encontrado'.
    if completed:
        tmp=cache.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({'complete':exhaustive, 'pages':pages, 'rows':rows}, ensure_ascii=False),encoding='utf-8')
        tmp.replace(cache)
    return rows, completed, pages


def stash_search_job(refresh=False):
    with lock:
        state['stash'].update(status='running',message='A preparar pesquisas por série…')
        groups = {}
        for index, item in enumerate(state['stash']['items']):
            if refresh or item.get('status') != 'matched':
                groups.setdefault(canonical(item['series']), []).append(index)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(pool_connections=2, pool_maxsize=2))
    done_groups = 0
    try:
        for _, indices in groups.items():
            with lock:
                series=state['stash']['items'][indices[0]]['series']
                wanted=[dict(state['stash']['items'][i]) for i in indices]
                state['stash']['message'] = f'A pesquisar {series} ({done_groups+1}/{len(groups)} séries)…'
            try:
                candidates, complete, pages = stash_fetch_series(session, series, wanted, refresh)
            except requests.HTTPError as exc:
                code=exc.response.status_code if exc.response is not None else 0
                if code == 429:
                    with lock:
                        state['stash'].update(status='limited', message='HTTP 429: o site limitou os pedidos. Pesquisa interrompida; tenta novamente mais tarde.')
                    return
                for i in indices:
                    update_stash_item(i,status='error',message=f'Erro HTTP {code or "desconhecido"}; podes repetir a pesquisa.')
                done_groups += 1
                continue
            except (requests.RequestException, ValueError, OSError) as exc:
                for i in indices:
                    update_stash_item(i,status='error',message=f'Falha de pesquisa: {str(exc)[:120]}')
                done_groups += 1
                continue
            for i in indices:
                with lock:item=dict(state['stash']['items'][i])
                match=best_match(item,candidates)
                if match:
                    update_stash_item(i,status='matched',match=match,message='Correspondência exacta de série, número e ano.')
                elif complete:
                    update_stash_item(i,status='not_found',match=None,message=f'Não identificado nas {pages} páginas analisadas.')
                else:
                    update_stash_item(i,status='pending',match=None,message=f'Pesquisa parcial ({pages} páginas). Podes voltar a tentar.')
            done_groups += 1
            with lock:
                state['stash']['message'] = f'{done_groups}/{len(groups)} séries pesquisadas.'
            time.sleep(0.7)
        with lock:
            matched=sum(x['status']=='matched' for x in state['stash']['items'])
            state['stash'].update(status='done',message=f'Pesquisa concluída: {matched} correspondências de {len(state["stash"]["items"])} desejos.')
    except Exception as exc:
        with lock:state['stash'].update(status='error',message='Pesquisa interrompida: '+str(exc)[:200])
    finally:
        session.close()


def persist_queue():
    # Todas as alterações à fila são serializadas pelo mesmo RLock.
    with lock:
        save_queue(QUEUE_FILE, state['downloads'])


def update_queue(index, **changes):
    with lock:
        state['downloads'][index].update(changes)
        persist_queue()


def public_downloads():
    # Os resultados podem conter URL assinados; não os expor à interface.
    fields = ('title', 'source', 'status', 'message', 'filename', 'attempts')
    with lock:
        return [{**{'id': i}, **{k: entry[k] for k in fields if k in entry}}
                for i, entry in enumerate(state['downloads'])]

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


def cache_path(query, only_issues, tag=''):
    key = hashlib.sha256((query.casefold().strip() + '|' + str(only_issues) + '|tag=' + tag).encode()).hexdigest()
    return CACHE_DIR / (key + '.json')


def fetch_with_retries(session, url):
    for attempt in range(3):
        try:
            response = session.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=(12,25))
            response.raise_for_status()
            if not response.text.strip():
                raise ValueError('Resposta vazia')
            return response
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (403, 429):
                raise
            if attempt == 2: raise
            time.sleep(0.6*(attempt+1))


def issue_key(title):
    # Retira diferenças triviais de pontuação/capitalização sem confundir edições.
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9#]+', ' ', title.casefold())).strip()


def filter_min_year(rows, min_year):
    """Filtra pelo ano da edição no título, não pela data de publicação do artigo."""
    if not min_year:
        return rows
    filtered = []
    for row in rows:
        years = re.findall(r'\(((?:19|20)\d{2})\)', row.get('title', ''))
        if years and int(years[-1]) >= min_year:
            filtered.append(row)
    return filtered


def show_rows(rows, min_year):
    return [dict(row, id=i) for i, row in enumerate(filter_min_year(rows, min_year))]


def search_job(query, only_issues, refresh=False, min_year=0, tag=''):
    event('search',status='running',message='A iniciar pesquisa...',pages=0,count=0)
    cachefile = cache_path(query, only_issues, tag)
    try:
        previous = json.loads(cachefile.read_text()) if cachefile.exists() else {}
    except (ValueError, OSError):
        previous = {}
    old_rows = previous.get('rows', [])
    if not refresh and old_rows:
        with lock:
            state['results'] = show_rows(old_rows, min_year)
        event('search',status='done',count=len(filter_min_year(old_rows, min_year)),pages=previous.get('pages',0),message=f'{len(filter_min_year(old_rows, min_year))} resultados (ano mínimo: {min_year or "sem limite"}) • cache disponível')
        return
    # Mantém visíveis os resultados anteriores enquanto actualiza.
    with lock: state['results']=show_rows(old_rows, min_year)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=4))
    found = {}
    incomplete = False
    scanned = 0
    seen_page_urls = set()
    repeated_pages = 0
    try:
        for page in range(1, MAX_PAGES+1):
            event('search',pages=page,message=f'A pesquisar página {page}… ({len(found)} edições)')
            url = (tag_page_url(tag, page) if tag else
                   f'https://getcomics.org/page/{page}?s={quote(query)}')
            try:
                response = fetch_with_retries(session,url)
                listing = parse_listing(response.text)
                if not listing and page == 1:
                    raise ValueError('Página inicial sem resultados; a fonte pode ter mudado ou estar bloqueada')
                if not listing: break
            except Exception as exc:
                incomplete=True
                event('search',message=f'Página {page} falhou: {exc}; a preservar resultados anteriores')
                break
            scanned += 1
            fresh=0
            # Ao pesquisar dentro de uma tag, NUNCA consultar a pesquisa global.
            # Filtragem textual no cliente garante que todos os artigos são da tag.
            for item in listing:
                title = item['title']
                if query and query.casefold() not in title.casefold(): continue
                if only_issues and not single_issue(title):continue
                key=issue_key(title)
                if key not in found:
                    found[key]={'title':title,'page_url':item['page_url']}
                    fresh+=1
            # Uma página repetida pode indicar que o servidor está a devolver o mesmo conteúdo.
            fingerprint = tuple(sorted(row['page_url'] for row in listing))
            if fingerprint and fingerprint in seen_page_urls:
                repeated_pages += 1
            else:
                repeated_pages = 0
            seen_page_urls.add(fingerprint)
            if repeated_pages >= 2: break
        # Cache de links já resolvidos: não consultar cada URL se não for preciso.
        old_by_key={issue_key(r['title']):r for r in old_rows}
        rows=[]
        for key, item in found.items():
            old=old_by_key.get(key)
            if old:
                row={k:v for k,v in old.items() if k!='id'}
                row['page_url']=item['page_url']
            else:
                row={'title':item['title'],'page_url':item['page_url'],'links':[],'source':'Por verificar'}
            rows.append(row)
        # Se falhar a pesquisa, nunca apagar silenciosamente resultados conhecidos.
        # A cache é cumulativa, nunca desaparecem edições antigas numa actualização.
        keys={issue_key(r['title']) for r in rows}
        rows += [{k:v for k,v in old.items() if k!='id'} for old in old_rows if issue_key(old['title']) not in keys]
        if not rows and old_rows: rows=[{k:v for k,v in r.items() if k!='id'} for r in old_rows]
        rows.sort(key=lambda r: (int(re.search(r'#\s*(\d+)',r['title']).group(1)) if re.search(r'#\s*(\d+)',r['title']) else 10**8, r['title'].casefold()))
        with lock:state['results']=show_rows(rows, min_year)
        temp=cachefile.with_suffix('.tmp')
        temp.write_text(json.dumps({'pages':scanned,'rows':rows,'incomplete':incomplete},ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(cachefile)
        event('search',status='done',pages=scanned,count=len(filter_min_year(rows, min_year)),message=f'{len(filter_min_year(rows, min_year))} edições filtradas, {scanned} páginas' + (' • pesquisa parcial, resultados guardados preservados' if incomplete else ' • resultados guardados; actualizar para procurar novidades'))
    except Exception as exc:
        if old_rows:
            with lock:state['results']=show_rows(old_rows, min_year)
            event('search',status='done',count=len(filter_min_year(old_rows, min_year)),message=f'Falhou a pesquisa ({exc}); a mostrar resultados guardados')
        else:event('search',status='error',message=str(exc))
    finally:session.close()


def import_article_job(url):
    event('search', status='running', message='A ler os livros da página…', pages=0, count=0)
    try:
        response = requests.get(url, timeout=(12, 30), headers={'User-Agent':'Mozilla/5.0'})
        response.raise_for_status()
        page = parse_article(response.text, url)
        if not page['items']:
            raise ValueError('Não identifiquei ficheiros individuais com fontes nesta página.')
        with lock:
            state['results'] = [dict(r, id=i) for i, r in enumerate(page['items'])]
            state['tags'] = page['tags']
            state['article_url'] = url
        event('search', status='done', pages=1, count=len(page['items']),
              message=f"{len(page['items'])} livros encontrados • selecciona os que pretendes")
    except Exception as exc:
        event('search', status='error', message=f'Falha ao ler artigo: {exc}')


def resolve_links(row):
    if row.get('links'):
        return row['links']
    url = valid_article_url(row['page_url'])
    response = requests.get(url, timeout=(12, 30), headers={'User-Agent':'Mozilla/5.0'})
    response.raise_for_status()
    parsed = parse_article(response.text, url)
    if len(parsed['items']) > 1:
        raise ValueError('Este artigo contém vários livros. Usa «Ver livros» para os escolher individualmente.')
    if not parsed['items']:
        raise ValueError('Não encontrei ligações de download reconhecidas neste artigo.')
    return parsed['items'][0]['links']


def safe_name(value):
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(str(value)).name).strip(' .')
    return name if name not in ('','.','..') else 'ficheiro.cbz'


def target_for(name, folder):
    name=safe_name(name)
    base=folder/name
    n=1
    while base.exists() or base.with_name(base.name+'.part').exists():
        base=folder/(f'{Path(name).stem} ({n}){Path(name).suffix}')
        n+=1
    return base


def direct_download(url, folder, title):
    """Transferência HTTP normal, sem contornar 403/429 ou páginas intermediárias."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Endereço de download inválido')
    with requests.get(url, stream=True, timeout=(15, 120),
                      headers={'User-Agent':'Mozilla/5.0'}) as response:
        # Botões /dls/ podem redireccionar para Pixeldrain: nesse caso
        # usar a autenticação Premium, mesmo que o GET anónimo tenha dado 403.
        if is_pixeldrain_url(response.url):
            return Path(download_pixeldrain(response.url, folder))
        response.raise_for_status()
        ctype = response.headers.get('Content-Type','').lower()
        if any(x in ctype for x in ('text/html','application/json','text/plain')):
            raise ValueError('Servidor devolveu HTML/JSON em vez de um ficheiro')
        final = urlparse(response.url)
        if final.scheme not in ('https', 'http') or not final.hostname:
            raise ValueError('Redireccionamento para endereço inesperado')
        # Processar Pixeldrain redireccionado através do fornecedor autenticado.
        if is_pixeldrain_url(response.url):
            return Path(download_pixeldrain(response.url, folder))
        disposition = response.headers.get('Content-Disposition','')
        m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)', disposition, re.I)
        filename = requests.utils.unquote(m.group(1)) if m else requests.utils.unquote(final.path.rstrip('/').split('/')[-1])
        if not filename.lower().endswith(('.cbz','.cbr','.zip','.rar')):
            filename = safe_name(title) + '.cbz'
        target = target_for(filename, folder)
        temp = target.with_name(target.name + '.part')
        try:
            with temp.open('wb') as out:
                for chunk in response.iter_content(256 * 1024):
                    PAUSE.wait()
                    if chunk: out.write(chunk)
            with temp.open('rb') as fh:
                sig = fh.read(8)
            if sig.startswith(b'Rar!') and target.suffix.lower() not in ('.rar','.cbr'):
                target = target.with_suffix('.cbr')
                target = target_for(target.name, folder)
            elif zipfile.is_zipfile(temp) and target.suffix.lower() not in ('.zip','.cbz'):
                target = target.with_suffix('.cbz')
                target = target_for(target.name, folder)
            check = target.with_name(target.name+'.test')
            temp.replace(check)
            try:
                good,reason = valid_archive_content(check, target.suffix)
                if not good: raise ValueError(reason)
                check.replace(target)
            finally:
                check.unlink(missing_ok=True)
            return target
        finally:
            temp.unlink(missing_ok=True)


def valid_archive_content(path, suffix):
    # Validação independente da extensão do ficheiro temporário.
    if not path.exists() or path.stat().st_size < 1024:
        return False, 'Ficheiro inexistente ou demasiado pequeno'
    if suffix.lower() in ('.cbz','.zip'):
        if not zipfile.is_zipfile(path): return False, 'Resposta não é um ZIP/CBZ válido'
        try:
            with zipfile.ZipFile(path) as z:
                if z.testzip(): return False, 'Erro CRC no arquivo'
                if not any(n.lower().endswith(('.jpg','.jpeg','.png','.webp','.gif','.avif')) for n in z.namelist()):
                    return False, 'Arquivo sem imagens reconhecidas'
        except Exception as exc: return False, 'ZIP inválido: '+str(exc)
    elif suffix.lower() in ('.cbr','.rar'):
        with path.open('rb') as fh:
            if not fh.read(8).startswith(b'Rar!\x1a\x07'):
                return False, 'Resposta não é um RAR/CBR válido'
    else:
        return False, 'Extensão não suportada: '+suffix
    return True, 'OK'


def source_order(links):
    direct_first = settings['server_priority'] == 'direct'
    order = {'direct':0 if direct_first else 1,
             'pixeldrain':1 if direct_first else 0, 'manual':2}
    # Python sort preserva a ordem original de Link 1, Link 2, etc.
    return sorted(links, key=lambda link: order.get(link['kind'],3))


def download_one(row, idx, folder):
    try:
        PAUSE.wait()
        update_queue(idx, status='running', message='A verificar fontes…',
                     attempts=int(state['downloads'][idx].get('attempts', 0)) + 1)
        PAUSE.wait()
        links = resolve_links(row)
        candidates = source_order(links)
        errors = []
        manual = [l for l in candidates if l['kind'] == 'manual']
        automatic = [l for l in candidates if l['kind'] in ('direct','pixeldrain')]
        if not automatic:
            raise ValueError('Sem fontes automáticas disponíveis. Consulta as ligações alternativas na lista.')
        for n, link in enumerate(automatic, 1):
            PAUSE.wait()
            try:
                update_queue(idx, message=f"Fonte {n}/{len(automatic)}: {link['label']}",
                             source=link['label'])
                if link['kind'] == 'pixeldrain':
                    path = Path(download_pixeldrain(link['url'], folder))
                    good, reason = valid_archive(path)
                    if not good:
                        path.rename(path.with_name(path.name+'.invalido'))
                        raise ValueError(reason)
                else:
                    path = direct_download(link['url'], folder, row['title'])
                good, reason = valid_archive(path)
                if not good:
                    path.rename(path.with_name(path.name+'.invalido'))
                    raise ValueError(reason)
                update_queue(idx, status='done', filename=path.name,
                             message=f"Validado via {link['label']}: {path.name}")
                return
            except DownloadStopped:
                raise
            except (requests.RequestException, ValueError, RuntimeError, OSError) as exc:
                code = exc.response.status_code if isinstance(exc,requests.HTTPError) and exc.response is not None else None
                errors.append(link['label'] + ': ' + str(exc)[:180])
                if code == 429:
                    raise ValueError('Limite HTTP 429: aguarda antes de tentar novamente. ' + str(exc)) from exc
                continue
        extra = ' • Outras fontes exigem download manual.' if manual else ''
        raise ValueError('Falharam as fontes automáticas: ' + ' | '.join(errors) + extra)
    except DownloadStopped:
        update_queue(idx, status='interrupted', message='Interrompido ao fechar. Carrega em Retomar pendentes.')
    except Exception as exc:
        update_queue(idx, status='error', message=str(exc))


def schedule_queue(indices, allowed=('queued','interrupted')):
    """Agenda entradas existentes sem duplicar trabalhos ou perder a pasta original."""
    with lock:
        indices = [i for i in dict.fromkeys(indices)
                   if type(i) is int and 0 <= i < len(state['downloads'])
                   and state['downloads'][i]['status'] in allowed
                   and i not in scheduled_indices]
        if PAUSE.stopping:
            return 0
        for i in indices:
            scheduled_indices.add(i)
            state['downloads'][i].update(status='queued', message='Em espera na fila…')
        if indices:
            persist_queue()
    if indices:
        try:
            download_pool.submit(download_job_run, indices)
        except RuntimeError:
            with lock:
                scheduled_indices.difference_update(indices)
            raise
    return len(indices)


def download_job_prepare_rows(rows):
    with lock:
        busy = {issue_key(d['title']) for d in state['downloads']
                if d['status'] in ('queued','running','interrupted','done')}
        unique = {}
        for row in rows:
            key = issue_key(row['title'])
            if key not in busy and key not in unique:
                unique[key] = row
        new_indices = []
        for row in unique.values():
            new_indices.append(len(state['downloads']))
            state['downloads'].append({
                'title': row['title'], 'source': row.get('source','Por verificar'),
                'status': 'queued', 'message': 'Em espera', 'row': row,
                'folder': str(DEST), 'attempts': 0,
            })
        if new_indices:
            persist_queue()
    return schedule_queue(new_indices)


def download_job_prepare(ids):
    with lock:
        rows = [state['results'][i].copy() for i in ids]
    return download_job_prepare_rows(rows)


def download_job_run(indices):
    try:
        for position, index in enumerate(indices):
            if PAUSE.stopping:
                return
            with lock:
                entry = state['downloads'][index]
                row = entry['row'].copy()
                folder = Path(entry['folder'])
            download_one(row, index, folder)
            with lock:
                msg = state['downloads'][index].get('message','')
            if 'HTTP 429' in msg or 'Limite HTTP 429' in msg:
                for remaining in indices[position+1:]:
                    update_queue(remaining, status='interrupted',
                                 message='Pendente: servidor limitou pedidos (HTTP 429). Retoma mais tarde.')
                return
    finally:
        with lock:
            scheduled_indices.difference_update(indices)


def shutdown_downloads():
    """Guardar pendentes e interromper os downloads activos ao fechar a janela."""
    PAUSE.stop()
    with lock:
        for entry in state['downloads']:
            if entry['status'] == 'running':
                entry.update(status='interrupted', message='Interrompido ao fechar. Retoma na próxima sessão.')
        persist_queue()
        persist_stash()
    download_pool.shutdown(wait=False, cancel_futures=True)
    pool.shutdown(wait=False, cancel_futures=True)


def save_download_folder(path):
    global DEST
    candidate=Path(str(path)).expanduser().resolve()
    if not candidate.is_absolute() or len(str(candidate)) > 600:
        raise ValueError('Caminho de destino inválido.')
    candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir(): raise ValueError('O destino indicado não é uma pasta.')
    with lock:
        DEST = candidate
        settings['download_dir'] = str(candidate)
        state['download_dir'] = str(candidate)
        _persist_settings()
    return str(candidate)


def _persist_settings():
    tmp=CONFIG_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False),encoding='utf-8')
    if os.name != 'nt': os.chmod(tmp,0o600)
    tmp.replace(CONFIG_FILE)


def choose_folder():
    """Selector de pastas nativo: tkinter no Windows, AppleScript no Mac."""
    if os.name == 'nt':
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        try:
            root.withdraw()
            root.attributes('-topmost', True)
            return filedialog.askdirectory(parent=root, title='Escolher pasta de destino', initialdir=str(DEST)) or ''
        finally:
            root.destroy()
    script = 'POSIX path of (choose folder with prompt "Escolhe a pasta de destino dos livros")'
    result = subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=120)
    if result.returncode:
        if '-128' in result.stderr: return ''
        raise RuntimeError('Selector de pasta indisponível: '+result.stderr.strip()[:200])
    return result.stdout.strip()


def suggest_tags(query):
    """WordPress REST API quando pública, com tags recolhidas de artigos em alternativa."""
    with lock:
        known=list(state['tags'])
    if query:
        known=[t for t in known if query.casefold() in t['name'].casefold() or query.casefold() in t['slug']]
    try:
        r=requests.get('https://getcomics.org/wp-json/wp/v2/tags',
                       params={'search':query[:60], 'per_page':40}, timeout=(7,9),
                       headers={'User-Agent':'Mozilla/5.0'})
        r.raise_for_status()
        data=r.json()
        if isinstance(data,list):
            suggestions=[{'name':x.get('name',''), 'slug':tag_slug(x.get('slug',''))}
                         for x in data if isinstance(x,dict) and x.get('slug')]
            return sorted({t['slug']:t for t in known+suggestions}.values(),key=lambda t:t['name'].casefold())
    except (requests.RequestException,ValueError,TypeError,KeyError):
        pass
    # Quando a API WordPress não responde, confirmar se uma tag exacta existe.
    if query:
        try:
            slug=tag_slug(query)
            r=requests.get(tag_page_url(slug,1), timeout=(7,9),
                           headers={'User-Agent':'Mozilla/5.0'})
            if r.status_code==200 and parse_listing(r.text):
                known.append({'name':query.strip(), 'slug':slug})
        except (requests.RequestException,ValueError):
            pass
    return sorted({t['slug']:t for t in known}.values(),key=lambda t:t['name'].casefold())

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args): pass
    def respond(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/':
            html=(HERE/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(html)));self.end_headers();self.wfile.write(html)
        elif self.path=='/api/state':
            with lock:self.respond({**state, 'downloads': public_downloads(), 'token':TOKEN, 'settings':dict(settings),
                                    'paused':PAUSE.paused,
                                    'has_api_key':bool(os.environ.get('PIXELDRAIN_API_KEY','').strip())})
        elif self.path.startswith('/api/tags?') or self.path=='/api/tags':
            q=parse_qs(urlparse(self.path).query).get('q',[''])[0][:60]
            self.respond({'tags':suggest_tags(q)})
        else:self.respond({'error':'Não encontrado'},404)
    def do_POST(self):
        try:
            if self.headers.get('X-App-Token')!=TOKEN:return self.respond({'error':'Acesso recusado'},403)
            size=int(self.headers.get('Content-Length',0))
            if size < 0 or size > (850000 if self.path == '/api/stash/import' else 100000):
                return self.respond({'error':'Pedido demasiado grande'},413)
            obj=json.loads(self.rfile.read(size) or b'{}')
            if self.path=='/api/stash/import':
                with lock:
                    if state['stash']['status']=='running':
                        return self.respond({'error':'Pesquisa da lista em curso'},409)
                items=parse_wishlist(obj.get('csv',''))
                with lock:
                    state['stash']={'status':'idle','message':f'{len(items)} desejos importados. Carrega em Pesquisar lista.',
                                    'items':items, 'processed':0, 'total':len(items)}
                    persist_stash()
                return self.respond({'ok':True,'count':len(items)})
            if self.path=='/api/stash/search':
                with lock:
                    if state['search']['status']=='running':
                        return self.respond({'error':'A pesquisa por título está em curso. Aguarda que termine.'},409)
                    if state['stash']['status']=='running':
                        return self.respond({'error':'Pesquisa da lista em curso'},409)
                    if not state['stash']['items']:
                        return self.respond({'error':'Importa primeiro uma lista Secret Stash.'},400)
                    state['stash']['status']='running'
                pool.submit(stash_search_job,bool(obj.get('refresh',False)))
                return self.respond({'ok':True})
            if self.path=='/api/stash/download':
                ids=obj.get('ids',[])
                with lock:
                    items=state['stash']['items']
                    if not isinstance(ids,list) or len(ids)>500 or not all(type(i) is int and 0<=i<len(items) for i in ids):
                        return self.respond({'error':'Selecção inválida'},400)
                    if not ids:return self.respond({'error':'Selecciona pelo menos uma edição.'},400)
                    if any(items[i].get('status')!='matched' or not items[i].get('match') for i in ids):
                        return self.respond({'error':'Só podes descarregar correspondências encontradas.'},400)
                    rows=[dict(items[i]['match']) for i in dict.fromkeys(ids)]
                with queued_lock:
                    count=download_job_prepare_rows(rows)
                return self.respond({'ok':True,'count':count})
            if self.path=='/api/pause':
                PAUSE.pause()
                return self.respond({'ok':True,'paused':True})
            if self.path=='/api/resume':
                PAUSE.resume()
                return self.respond({'ok':True,'paused':False})
            if self.path=='/api/retry-failed':
                requested = obj.get('id', None)
                with lock:
                    if requested is not None and (type(requested) is not int or not 0 <= requested < len(state['downloads'])):
                        return self.respond({'error':'Identificador inválido'},400)
                    indices = ([requested] if requested is not None else
                               [i for i,d in enumerate(state['downloads']) if d['status']=='error'])
                count = schedule_queue(indices, allowed=('error',))
                return self.respond({'ok':True,'count':count})
            if self.path=='/api/resume-pending':
                PAUSE.resume()
                with lock:
                    indices = [i for i,d in enumerate(state['downloads'])
                               if d['status'] in ('queued','interrupted')]
                count = schedule_queue(indices)
                return self.respond({'ok':True,'count':count})
            if self.path=='/api/pixeldrain-key':
                if obj.get('remove') is True:
                    _remove_api_key()
                    return self.respond({'ok':True,'has_api_key':False})
                _save_api_key(obj.get('api_key',''))
                return self.respond({'ok':True,'has_api_key':True})
            if self.path=='/api/article':
                url=valid_article_url(str(obj.get('url','')))
                with lock:
                    if state['stash']['status']=='running':return self.respond({'error':'Pesquisa Secret Stash em curso'},409)
                    if state['search']['status']=='running':return self.respond({'error':'Pesquisa em curso'},409)
                    state['search']={'status':'running','message':'A abrir artigo…','pages':0,'count':0}
                pool.submit(import_article_job,url)
                return self.respond({'ok':True})
            if self.path=='/api/choose-folder':
                folder=choose_folder()
                return self.respond({'path':save_download_folder(folder) if folder else ''})
            if self.path=='/api/settings':
                if 'download_dir' in obj:
                    save_download_folder(obj['download_dir'])
                if 'server_priority' in obj:
                    priority=str(obj['server_priority'])
                    if priority not in ('direct','pixeldrain'):
                        return self.respond({'error':'Prioridade inválida'},400)
                    with lock: settings['server_priority']=priority
                if 'max_pages' in obj:
                    count=int(obj['max_pages'])
                    if not 1 <= count <= 100:
                        return self.respond({'error':'O limite deve estar entre 1 e 100'},400)
                    global MAX_PAGES
                    with lock:
                        MAX_PAGES=count
                        settings['max_pages']=count
                if 'connections' in obj:
                    value = int(obj['connections'])
                    if not 1 <= value <= 16:
                        return self.respond({'error':'O número de ligações deve estar entre 1 e 16'},400)
                    with lock: settings['connections']=value
                    os.environ['PIXELDRAIN_CONNECTIONS']=str(value)
                _persist_settings()
                return self.respond({'ok':True, 'settings':dict(settings)})
            if self.path=='/api/search':
                with lock:
                    if state['stash']['status']=='running':return self.respond({'error':'Pesquisa Secret Stash em curso'},409)
                query=str(obj.get('query','')).strip()[:150]
                try: tag=tag_slug(str(obj.get('tag','')))
                except ValueError as exc: return self.respond({'error':str(exc)},400)
                if not query and not tag:return self.respond({'error':'Indica um termo de pesquisa ou uma tag'},400)
                min_year_raw = str(obj.get('min_year', '')).strip()
                if min_year_raw and (not min_year_raw.isdigit() or not 1900 <= int(min_year_raw) <= 2100):
                    return self.respond({'error':'O ano mínimo deve estar entre 1900 e 2100'},400)
                with lock:
                    if state['search']['status']=='running':return self.respond({'error':'Pesquisa em curso'},409)
                    state['search']={'status':'running','message':'A iniciar…','pages':0,'count':0}
                with lock: state['article_url']=''
                pool.submit(search_job,query,bool(obj.get('only_issues',True)),bool(obj.get('refresh',False)),int(min_year_raw) if min_year_raw else 0,tag)
                return self.respond({'ok':True})
            if self.path=='/api/download':
                ids=obj.get('ids',[])
                with lock:total=len(state['results'])
                if not isinstance(ids,list) or len(ids)>500 or not all(type(i) is int and 0<=i<total for i in ids):return self.respond({'error':'Selecção inválida'},400)
                if not ids:return self.respond({'error':'Sem selecção'},400)
                with queued_lock:
                    download_job_prepare(list(dict.fromkeys(ids)))
                return self.respond({'ok':True})
            self.respond({'error':'Não encontrado'},404)
        except Exception as exc:self.respond({'error':str(exc)},500)

if __name__=='__main__':
    print('GetComics Web UI — servidor local')
    print(f'Abre http://127.0.0.1:{WEB_PORT} no navegador')
    print('Destino: '+str(DEST))
    print('Para parar: Ctrl+C')
    try:
        ThreadingHTTPServer(('127.0.0.1',WEB_PORT),Handler).serve_forever()
    finally:
        shutdown_downloads()
