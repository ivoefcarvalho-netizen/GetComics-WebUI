"""Importação de listas de desejos Secret Stash e correspondência conservadora de edições.

O módulo não faz downloads e não guarda credenciais. Pesquisas HTTP ficam no backend.
"""
import csv
import io
import re
import unicodedata
from urllib.parse import urlparse

MAX_ROWS = 1000


def canonical(text):
    value = unicodedata.normalize('NFKD', str(text or '').casefold())
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value)).strip()


def parse_wishlist(csv_text):
    if not isinstance(csv_text, str) or len(csv_text.encode('utf-8')) > 750_000:
        raise ValueError('Ficheiro demasiado grande (máximo 750 KB).')
    csv_text = csv_text.lstrip('\ufeff')
    if not csv_text.strip():
        raise ValueError('O ficheiro CSV está vazio.')
    reader = csv.DictReader(io.StringIO(csv_text, newline=''), delimiter=';')
    required = {'Série', 'Ano', 'Número'}
    if not reader.fieldnames or not required.issubset({x.strip().lstrip('\ufeff') for x in reader.fieldnames}):
        raise ValueError('CSV Secret Stash inválido: são necessárias as colunas Série;Ano;Número.')
    rows = []
    seen = set()
    for line, record in enumerate(reader, 2):
        if not isinstance(record, dict) or None in record:
            raise ValueError(f'Linha {line}: CSV mal formado.')
        series = (record.get('Série') or '').strip()
        year = (record.get('Ano') or '').strip()
        number = (record.get('Número') or '').strip()
        if not series and not number and not year:
            continue
        if not series or len(series) > 200 or not year.isdigit() or not 1900 <= int(year) <= 2100:
            raise ValueError(f'Linha {line}: série ou ano inválidos.')
        if not re.fullmatch(r'\d{1,5}', number) or int(number) > 9999:
            raise ValueError(f'Linha {line}: número de edição inválido ({number!r}).')
        key = (canonical(series), int(year), int(number))
        if key in seen:
            continue
        seen.add(key)
        rows.append({'series':series, 'year':int(year), 'number':int(number),
                     'publisher':(record.get('Editora') or '')[:120],
                     'kind':(record.get('Tipo') or '')[:80],
                     'status':'pending', 'message':'Por pesquisar', 'match':None})
        if len(rows) > MAX_ROWS:
            raise ValueError(f'Lista demasiado grande (máximo {MAX_ROWS} títulos).')
    if not rows:
        raise ValueError('Não foram encontradas entradas válidas na lista.')
    return rows


def _number_in_remainder(remainder, number):
    """Exige número inequívoco no sufixo da série; não confunde #1 com #11."""
    # Não tratar pacotes e intervalos como edições individuais (ex.: #1–6).
    if re.search(r'(?i)(?:#|\b(?:issue|no\.?|vol(?:ume)?\.?|book)\s*#?\s*)?\d{1,4}\s*(?:-|–|—|to|through)\s*#?\d{1,4}\b', remainder):
        return False
    # Na pesquisa de 'Street Fighter' não aceitar 'Street Fighter II #7'.
    if re.match(r'(?i)^\s*[-–:]?\s*(?:II|III|IV|V|VI|VII|VIII|IX|X)\b', remainder):
        return False
    markers = re.findall(r'(?i)(?:#|\b(?:issue|no\.?|number|vol(?:ume)?\.?|book|part)\s*#?\s*)(\d{1,4})\b', remainder)
    if markers:
        return number in [int(x) for x in markers]
    # Publicações sem #, ex.: 'Absolute Green Lantern 019 (2025)'.
    plain = re.match(r'(?i)^\s*[-–:]?\s*0*(\d{1,4})(?=\s|\(|\[|$)', remainder)
    if plain:
        return int(plain.group(1)) == number
    # 'Homunculus (Omnibus) (2023) Volume 2'.
    trailing = re.search(r'(?i)\b(?:vol(?:ume)?\.?|book)\s*#?\s*(\d{1,4})\b', remainder)
    return bool(trailing and int(trailing.group(1)) == number)


def matches_issue(item, title):
    """Só selecciona correspondências com série, número e ano explícito."""
    normalized_title = canonical(title)
    normalized_series = canonical(item['series'])
    # Não associar uma colectânea a um número individual só por partilharem
    # uma referência numérica; uma série Omnibus continua a ser permitida.
    if 'omnibus' not in normalized_series and re.search(r'(?i)\b(?:TPB|collected|collection|compendium|bundle|omnibus)\b', title):
        return False
    if not normalized_title.startswith(normalized_series + ' '):
        # Alguns títulos representam Omnibus como formato em vez de parte do nome.
        if normalized_series.endswith(' omnibus') and 'omnibus' in normalized_title.split():
            shortened = normalized_series.removesuffix(' omnibus')
            if not normalized_title.startswith(shortened + ' '):
                return False
            normalized_series = shortened
        else:
            return False
    suffix = normalized_title[len(normalized_series):].strip()
    # Exigir que o ano seja explícito, sem assumir que data da página = ano da série.
    years = {int(x) for x in re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', title)}
    if item['year'] not in years:
        return False
    raw = str(title)
    # Extrair o sufixo preservando símbolos como '#' e a grafia 'Vol.'.
    series_tokens = normalized_series.split()
    token_pattern = r'[^a-zA-Z0-9]*'.join(re.escape(t) for t in series_tokens)
    # Comparação sem perda de acentos já foi feita acima; para obter número,
    # os marcadores são procurados apenas depois da parte correspondente à série.
    normalized_raw = unicodedata.normalize('NFKD', raw.casefold())
    normalized_raw = ''.join(c for c in normalized_raw if not unicodedata.combining(c))
    match = re.match(r'^\s*' + token_pattern, normalized_raw, re.I)
    if not match:
        return False
    remainder = normalized_raw[match.end():]
    return _number_in_remainder(remainder, item['number'])


def best_match(item, candidates):
    """Prefere uma correspondência directa a nomes genéricos / pacotes."""
    valid = [c for c in candidates if matches_issue(item, c.get('title', ''))
             and _is_getcomics_url(c.get('page_url', ''))]
    if not valid:
        return None
    valid.sort(key=lambda r: (bool(re.search(r'(?i)\b(?:TPB|collection|bundle|complete|pack)\b', r['title'])),
                              len(r['title']), r['page_url']))
    winner = valid[0]
    return {'title': winner['title'], 'page_url': winner['page_url'],
            'links':winner.get('links', []), 'source':winner.get('source', 'Por verificar')}


def _is_getcomics_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme == 'https' and parsed.hostname in ('getcomics.org', 'www.getcomics.org')
    except ValueError:
        return False
