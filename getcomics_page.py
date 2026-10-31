"""Leitor HTML de páginas e tags do GetComics.

Apenas produz metadados e ligações anunciadas no artigo. Não contorna bloqueios,
autenticação, limites de pedidos, ou plataformas que exijam navegação manual.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup, NavigableString, Tag

SITE_HOSTS = {'getcomics.org', 'www.getcomics.org'}
PIXEL_HOSTS = {'pixeldrain.com', 'www.pixeldrain.com', 'pixeldrain.net', 'www.pixeldrain.net'}
MANUAL_HOSTS = {'mega.nz', 'mega.co.nz', 'mediafire.com', 'www.mediafire.com',
                'getcomics.ufile.io', 'ufile.io', '1024terabox.com', 'terabox.com',
                'www100.zippyshare.com'}
ITEM_RE = re.compile(r'(?i)(?:\b(?:book|vol(?:ume)?\.?|issue|part|tome)\s*#?\s*\d+\b|#\s*\d+\b)')
YEARMATCH = re.compile(r'\(((?:19|20)\d{2})\)')
DOWNLOAD_LABELS = ('LINK ', 'MAIN SERVER', 'DIRECT', 'PIXELDRAIN', 'MEDIAFIRE',
                   'MEGA', 'UFILE', 'ZIPPY', 'TERABOX', 'DOWNLOAD', 'MIRROR')


def valid_article_url(value):
    """Limita a importação de páginas aos artigos públicos do domínio indicado."""
    p = urlparse(value.strip())
    if p.scheme != 'https' or p.hostname not in SITE_HOSTS or p.username or p.password:
        raise ValueError('Cola um endereço https://getcomics.org/ de um artigo.')
    if not p.path or p.path == '/' or p.path.startswith(('/tag/', '/wp-admin/', '/wp-json/')):
        raise ValueError('É necessário um endereço de artigo, não de pesquisa ou tag.')
    if p.port not in (None, 443):
        raise ValueError('Porta não permitida')
    return 'https://getcomics.org' + p.path


def tag_slug(value):
    value = value.strip()
    if not value:
        return ''
    if '://' in value:
        p = urlparse(value)
        if p.scheme != 'https' or p.hostname not in SITE_HOSTS or not p.path.startswith('/tag/'):
            raise ValueError('A tag tem de pertencer ao GetComics (endereço /tag/...).')
        value = p.path.split('/tag/', 1)[1].strip('/').split('/')[0]
    value = unicodedata.normalize('NFKD', unquote(value))
    value = ''.join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r'[\s_]+', '-', value)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,100}', value):
        raise ValueError('Tag inválida. Utiliza o nome/slug ou o endereço da tag.')
    return value


def tag_page_url(slug, page):
    if page < 1:
        raise ValueError('Página inválida')
    base = f'https://getcomics.org/tag/{tag_slug(slug)}/'
    return base if page == 1 else base + f'page/{page}/'


def extract_tags(html):
    soup = BeautifulSoup(html, 'html.parser')
    tags = {}
    for a in soup.select('a[href]'):
        href = urlparse(urljoin('https://getcomics.org/', a['href']))
        if href.hostname in SITE_HOSTS and href.path.startswith('/tag/'):
            slug = href.path.split('/tag/', 1)[1].strip('/').split('/')[0]
            try:
                slug = tag_slug(slug)
            except ValueError:
                continue
            name = a.get_text(' ', strip=True) or slug
            if slug:
                tags[slug] = {'name': name, 'slug': slug}
    return sorted(tags.values(), key=lambda item: item['name'].casefold())


def _lines(content):
    """Lineariza nós de texto/âncoras em linhas sem duplicar texto do DOM."""
    lines = []
    words = []
    links = []

    def flush():
        nonlocal words, links
        name = re.sub(r'\s+', ' ', ' '.join(words)).strip()
        if name or links:
            lines.append((name, links))
        words, links = [], []

    def walk(node):
        if isinstance(node, NavigableString):
            t = str(node).strip()
            if t:
                words.append(t)
            return
        if not isinstance(node, Tag) or node.name in ('script', 'style', 'noscript'):
            return
        if node.name == 'br':
            flush()
            return
        if node.name == 'a' and node.get('href'):
            label = node.get_text(' ', strip=True)
            links.append({'label': label, 'url': node['href']})
        for child in node.children:
            walk(child)
        if node.name in ('p', 'li', 'h1', 'h2', 'h3', 'h4', 'tr'):
            flush()

    walk(content)
    flush()
    return lines


def classify_link(link, page_url):
    href = urljoin(page_url, link['url'].strip())
    p = urlparse(href)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        return None
    host = p.hostname.lower()
    label = link['label'].strip() or 'Ligação'
    upper = label.upper()
    if host in PIXEL_HOSTS:
        kind = 'pixeldrain'
    elif host in MANUAL_HOSTS or any(n in host for n in ('mega.', 'mediafire.', 'ufile.', 'terabox.', 'zippyshare.')):
        kind = 'manual'
    elif host in SITE_HOSTS and p.path.startswith(('/dls/', '/download', '/dlds/')):
        kind = 'direct'
    elif host in SITE_HOSTS and p.path.startswith('/tag/'):
        return None
    elif any(s in upper for s in DOWNLOAD_LABELS):
        # O destino poderá ser uma página intermediária. Só é descarregado
        # se a resposta for realmente um ficheiro (nunca HTML).
        kind = 'direct'
    elif host in ('getcomics.info', 'www.getcomics.info') and 'POST' in upper:
        kind = 'manual'
    else:
        return None
    return {'url': href, 'label': label, 'kind': kind}


def parse_article(html, page_url):
    """Lista livros/volumes e links por grupo, sem confundir servidores entre títulos."""
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.select_one('article h1, h1.post-title, h1.entry-title, h1')
    page_title = title_tag.get_text(' ', strip=True) if title_tag else page_url.rstrip('/').rsplit('/', 1)[-1]
    main = soup.select_one('.entry-content, .post-content, .td-post-content, .post-body, article')
    if main is None:
        raise ValueError('Não encontrei o conteúdo principal do artigo.')

    raw_lines = _lines(main)
    # Não confundir links do artigo com publicidade, navegação ou notas.
    began = False
    download_lines = []
    has_marker = any('FREE COMICS DOWNLOAD' in text.upper() for text, _ in raw_lines)
    for text, links in raw_lines:
        upper = text.upper()
        if 'FREE COMICS DOWNLOAD' in upper:
            began = True
            continue
        if began and (upper.startswith('SCREENSHOTS') or upper.startswith('NOTES :') or upper == 'NOTES'):
            break
        if began or not has_marker:
            download_lines.append((text, links))

    groups = []
    current = None
    for text, anchors in download_lines:
        title_candidate = (ITEM_RE.search(text) is not None and
                           (text.rstrip().endswith(':') or re.search(r'\((?:19|20)\d{2}\)', text)))
        # Não aceitar os sumários de título tipo Vol. 1-10 que não têm ligações.
        if title_candidate and not re.search(r'\b(?:vol|book)\. ?\d+\s*[-–]\s*\d+', text, re.I):
            if current and current['links']:
                groups.append(current)
            heading = text.strip()
            if anchors:
                first_label = anchors[0].get('label', '')
                # Quando o título e os botões estão no mesmo parágrafo,
                # separar o texto da lista de servidores.
                if first_label:
                    cut = re.search(r'\s*:?\s*' + re.escape(first_label), heading, re.I)
                    if cut:
                        heading = heading[:cut.start()].rstrip(' :')
            current = {'title': heading.rstrip(' :'), 'page_url': page_url, 'links': []}
        elif current is None:
            # Artigos que só têm um download, sem divisão em capítulos/livros.
            current = {'title': page_title, 'page_url': page_url, 'links': []}
        for anchor in anchors:
            link = classify_link(anchor, page_url)
            if link and not any(item['url'] == link['url'] for item in current['links']):
                current['links'].append(link)
    if current and current['links']:
        groups.append(current)

    # Se a página continha uma única ligação dentro de um título genérico,
    # usamos o título do artigo; grupos distintos conservam os seus títulos.
    for idx, group in enumerate(groups):
        group['id'] = idx
        group['source'] = ', '.join(dict.fromkeys(link['label'] for link in group['links']))
    return {'title': page_title, 'items': groups, 'tags': extract_tags(html), 'page_url': page_url}


def parse_listing(html):
    soup = BeautifulSoup(html, 'html.parser')
    found = []
    for article in soup.select('article'):
        heading = article.select_one('h1.post-title, h1, h2.post-title, h2')
        anchor = heading.find('a', href=True) if heading else None
        if not anchor:
            continue
        title = heading.get_text(' ', strip=True)
        href = urljoin('https://getcomics.org/', anchor['href'])
        if urlparse(href).hostname not in SITE_HOSTS:
            continue
        found.append({'title': title, 'page_url': href, 'links': [], 'source': 'Por verificar'})
    return found
