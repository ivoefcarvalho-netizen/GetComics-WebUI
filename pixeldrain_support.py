"""PIXELDRAIN_PATCH_V2 — autenticação Premium e transferências via aria2c."""
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import requests

HOSTS = {'pixeldrain.com', 'www.pixeldrain.com', 'pixeldrain.net', 'www.pixeldrain.net'}


def is_pixeldrain_url(url):
    p = urlparse(url)
    return p.scheme == 'https' and (p.hostname or '').lower() in HOSTS and bool(
        re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?', p.path)
    )


def _key():
    key = os.getenv('PIXELDRAIN_API_KEY', '').strip()
    if key:
        return key
    env_file = Path(__file__).resolve().parent / '.env'
    if env_file.is_file():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('PIXELDRAIN_API_KEY='):
                return line.strip().partition('=')[2].strip().strip('"').strip("'")
    return ''


def _base_file(url):
    if not is_pixeldrain_url(url):
        raise ValueError('Link Pixeldrain inválido')
    identifier = urlparse(url).path.rstrip('/').split('/')[-1]
    return 'https://pixeldrain.com/api/file/' + identifier, identifier


def _file_info(base, auth):
    res = requests.get(base + '/info', auth=auth, timeout=(15, 30))
    res.raise_for_status()
    info = res.json()
    if not info.get('success', True):
        raise RuntimeError('Pixeldrain recusou o pedido: ' + str(info.get('message', 'erro desconhecido')))
    return info


def _target(download_dir, name):
    folder = Path(download_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(str(name)).name)
    if safe in ('', '.', '..'):
        raise RuntimeError('Nome de ficheiro inválido')
    candidate = folder / safe
    index = 1
    while candidate.exists():
        candidate = folder / f'{Path(safe).stem} ({index}){Path(safe).suffix}'
        index += 1
    return candidate


def _aria2_download(base, target, expected_size, key):
    # A chave nunca vai na linha de comandos (visível via ps).
    # Um ficheiro de configuração temporário, com permissões 0600, transporta o header.
    connections = int(os.getenv('PIXELDRAIN_CONNECTIONS', '8'))
    if not 1 <= connections <= 16:
        raise ValueError('PIXELDRAIN_CONNECTIONS deve estar entre 1 e 16')
    config_path = None
    partial_name = target.name + '.part'
    partial = target.parent / partial_name
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='pixeldrain_aria2_', suffix='.conf', delete=False) as config:
            config_path = Path(config.name)
            os.chmod(config_path, 0o600)
            config.write('split=' + str(connections) + '\n')
            config.write('max-connection-per-server=' + str(connections) + '\n')
            config.write('min-split-size=1M\n')
            config.write('file-allocation=none\n')
            if key:
                encoded = base64.b64encode((':' + key).encode('utf-8')).decode('ascii')
                config.write('header=Authorization: Basic ' + encoded + '\n')
        args = [
            'aria2c', '--conf-path=' + str(config_path),
            '--console-log-level=warn', '--summary-interval=1',
            '--continue=true', '--auto-file-renaming=false', '--allow-overwrite=false',
            '--dir=' + str(target.parent), '--out=' + partial_name,
            base + '?download',
        ]
        print(f'[Pixeldrain] aria2c ({connections} ligações no máximo): {target.name}', flush=True)
        result = subprocess.run(args, check=False, capture_output=True, text=True, errors='replace')
        if result.returncode != 0:
            details = (result.stderr or result.stdout or '').strip()
            safe_details = '\n'.join(details.splitlines()[-6:])[:1000]
            # Evitar colocar a chave de autenticação nos logs.
            if key:
                safe_details = safe_details.replace(key, '[OCULTA]')
            raise RuntimeError(f'aria2c terminou com erro {result.returncode}: {safe_details or "sem detalhes"}')
        if not partial.is_file():
            raise RuntimeError('aria2c não produziu um ficheiro de download')
        length = partial.stat().st_size
        if expected_size is not None and length != int(expected_size):
            raise RuntimeError(f'Tamanho incorrecto: {length}/{expected_size} bytes')
        if length == 0:
            raise RuntimeError('Ficheiro vazio')
        partial.rename(target)
        # aria2 remove normalmente os seus metadados após conclusão.
        return target
    finally:
        if config_path:
            config_path.unlink(missing_ok=True)


def _requests_download(base, target, expected_size, auth):
    partial = target.with_name(target.name + '.part')
    try:
        with requests.get(base + '?download', auth=auth, stream=True, timeout=(20, 120)) as res:
            res.raise_for_status()
            ctype = res.headers.get('content-type', '').lower()
            if 'application/json' in ctype or 'text/html' in ctype:
                raise RuntimeError('O servidor devolveu uma página de erro')
            with partial.open('wb') as stream:
                for chunk in res.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        size = partial.stat().st_size
        if expected_size is not None and size != int(expected_size):
            raise RuntimeError(f'Tamanho incorrecto: {size}/{expected_size} bytes')
        if not size:
            raise RuntimeError('Ficheiro vazio')
        partial.rename(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def download_pixeldrain(url, download_dir):
    base, identifier = _base_file(url)
    key = _key()
    if not key:
        print('[Pixeldrain] ATENÇÃO: sem chave API; limites da modalidade gratuita poderão aplicar-se.')
    auth = ('', key) if key else None
    info = _file_info(base, auth)
    target = _target(download_dir, info.get('name') or identifier + '.cbz')
    size = info.get('size')
    if shutil.which('aria2c'):
        try:
            return _aria2_download(base, target, size, key)
        except (RuntimeError, OSError) as exc:
            # O aria2c empacotado pode falhar mesmo estando presente. Dar diagnóstico
            # e usar o método HTTP autenticado como alternativa.
            print('[Pixeldrain] Falha aria2c: ' + str(exc), flush=True)
            print('[Pixeldrain] A tentar download alternativo através de Python.', flush=True)
            partial = target.with_name(target.name + '.part')
            if partial.exists():
                partial.rename(target.with_name(target.name + '.aria2-falhou'))
    else:
        print('[Pixeldrain] aria2c indisponível; a usar Python (uma ligação).', flush=True)
    return _requests_download(base, target, size, auth)
