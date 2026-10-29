import requests
from urllib.parse import quote
from bs4 import BeautifulSoup
from rich.console import Console

BASE_URL = "https://getcomics.org"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

console = Console()

class GetComics:
    def __init__(self, query: str, results: str, verbose: bool):
        self.query = query
        # self.tag = tag
        self.num_results_desired = results
        self.verbose = verbose
        self.page = 1
        self.page_links = {}
        self.comic_links = {}

    def find_pages(self, date=None):
        url = f"{BASE_URL}/page/{self.page}?s={quote(self.query)}"

        try:
            if self.verbose:
                console.print(f"Opening page {url}")
            response = requests.get(url, headers=HEADERS, timeout=15)
        except Exception as e:
            console.print(f"Error contacting URL: {url}")
            console.print(e)
            return

        if response.status_code != 200:
            if self.verbose:
                console.print(f"[bold red]Request failed with status {response.status_code} for {url}[/bold red]")
            return

        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find_all("article")
        if len(articles) == 0:
            if self.verbose:
                console.print(f"[bold red]No <article> elements found on {url} (page layout may have changed or request was blocked).[/bold red]")
            return

        for article in articles:
            title_tag = article.find("h1", {"class": "post-title"})
            if not title_tag:
                title_tag = article.find("h1")
            if not title_tag:
                continue
            link_tag = title_tag.find("a")
            if not link_tag or not link_tag.get("href"):
                continue
            title = title_tag.text.strip()
            link = link_tag["href"]
            self.page_links[link] = title

        self.page += 1

    # PIXELDRAIN_PATCH_V3: detectar botões PIXELDRAIN, resolver /dls/ e preferir Pixeldrain.
    # GETCOMICS_SOLO_ISSUES_V1: filtra TPB/compilações e recolhe todas as páginas.
    @staticmethod
    def is_single_issue(title):
        """Heurística conservadora: só edições individuais identificáveis pelo título."""
        import re
        t = ' '.join(title.split())
        # Colectâneas, formatos de livro e packs de vários números.
        if re.search(r'(?i)\b(?:TPB|T\.P\.B\.|trade\s*paperback|omnibus|omni|compendium|'
                     r'collected\s+edition|collection|hardcover|hard\s+cover|'
                     r'graphic\s+novel|complete\s+(?:collection|series|edition)|'
                     r'boxed?\s+set|bundle|one\s+shot\s+collection|vol(?:ume)?\.?\s*\d+)\b', t):
            return False
        # Intervalos explícitos de números (incluindo '#1-6', '#001–006', '1 to 6').
        if re.search(r'(?i)(?:#\s*)?\d{1,4}\s*(?:-|–|—|to|through|&)\s*#?\s*\d{1,4}\b', t):
            return False
        # Ex.: '(Issues 1-5)' ou 'Issues 1, 2, 3'.
        if re.search(r'(?i)\b(?:issues?|nos?\.?|numbers?)\s*#?\s*\d{1,4}\s*[,/&+]', t):
            return False
        # Ex.: 'Spider-Man #12 (2026)' ou 'Batman #012'.
        if re.search(r'#\s*\d{1,4}(?:\.\d+)?(?!\d)', t):
            return True
        # Ex.: 'Absolute Green Lantern 019 (2026) (Digital)' e 'X-Men 004 (2025)'.
        # Exigir ano entre parênteses para não confundir o ano com a edição.
        if re.search(r'(?<!\d)\d{1,4}(?:\.\d+)?\s*\((?:19|20)\d{2}\)', t):
            return True
        # Outras edições inequívocas: 'Batman 019 [Digital]' / 'Batman 019 Digital'.
        if re.search(r'(?i)(?<!\d)\d{1,4}\s*(?:\[digital\]|\(digital\))', t):
            return True
        return False

    def find_all_pages(self, date=None):
        """Recolhe todas as páginas primeiro; menu de resultados aparece uma única vez.

        Protecção contra paginação infinita / repetição de resultados.
        GETCOMICS_MAX_PAGES (por omissão 100) permite ajustar limite de segurança.
        """
        import os
        try:
            max_pages = max(1, min(int(os.getenv('GETCOMICS_MAX_PAGES', '100')), 500))
        except ValueError:
            max_pages = 100
        start_page = self.page
        seen = set(self.page_links)
        total_scanned = 0
        for _ in range(max_pages):
            current_page = self.page
            console.print(f'[cyan]A pesquisar página {current_page}...[/cyan]')
            self.find_pages(date=date)
            total_scanned += 1
            fresh = set(self.page_links).difference(seen)
            if not fresh:
                # Uma resposta vazia, página repetida ou último resultado da pesquisa.
                break
            seen.update(fresh)
        else:
            console.print(f'[yellow]Limite de segurança de {max_pages} páginas atingido. '
                          'Se necessário, aumenta GETCOMICS_MAX_PAGES.[/yellow]')
        before = len(self.page_links)
        self.page_links = {url: title for url, title in self.page_links.items()
                           if self.is_single_issue(title)}
        console.print(f'[green]Páginas analisadas: {total_scanned}. '
                      f'Edições individuais: {len(self.page_links)} '
                      f'(excluídos: {before - len(self.page_links)}).[/green]')
        # O menu original permite carregar em "n", mas a pesquisa já percorreu
        # todas as páginas: a segunda chamada a find_all_pages será inócua.
        self._all_pages_loaded = True

    def get_download_links(self):
        import re
        from urllib.parse import urljoin, urlparse

        getcomics_hosts = {'getcomics.org', 'www.getcomics.org'}
        pixel_hosts = {'pixeldrain.com', 'www.pixeldrain.com', 'pixeldrain.net', 'www.pixeldrain.net'}
        self.fallback_links = {}

        for page_url, title in self.page_links.items():
            try:
                response = requests.get(page_url, headers=HEADERS, timeout=15)
                response.raise_for_status()
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    raise RuntimeError('GetComics limitou temporariamente as consultas (HTTP 429). Interrompe as tentativas e volta mais tarde.') from exc
                if self.verbose:
                    console.print(f'[yellow]Falha HTTP ao abrir {page_url}: {exc}[/yellow]')
                continue
            except requests.RequestException as exc:
                if self.verbose:
                    console.print(f'[yellow]Falha ao abrir {page_url}: {exc}[/yellow]')
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            pixeldrain, direct, mediafire = [], [], []
            for a in soup.find_all('a', href=True):
                href = urljoin(page_url, a['href'].strip())
                parsed = urlparse(href)
                host = (parsed.hostname or '').lower()
                label = (a.get_text(' ', strip=True) + ' ' + a.get('title', '')).upper()
                if parsed.scheme not in ('http', 'https'):
                    continue

                # O botão PIXELDRAIN pode apontar para getcomics.org/dls/...,
                # que redirecciona para pixeldrain.com/u/ID.
                if 'PIXELDRAIN' in label:
                    if host in pixel_hosts and re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?', parsed.path):
                        pixeldrain.append(href)
                    elif host in getcomics_hosts and parsed.path.startswith(('/dls/', '/download', '/dlds/')):
                        try:
                            with requests.get(href, headers=HEADERS, timeout=(10, 20),
                                              allow_redirects=True, stream=True) as redirect_response:
                                destination = redirect_response.url
                                final = urlparse(destination)
                                if (redirect_response.status_code < 400 and
                                    (final.hostname or '').lower() in pixel_hosts and
                                    re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?', final.path)):
                                    pixeldrain.append(destination)
                                    if self.verbose:
                                        console.print('[cyan]Pixeldrain encontrado por redireccionamento.[/cyan]')
                                elif self.verbose:
                                    console.print('[yellow]Botão Pixeldrain não resolveu para um ficheiro suportado.[/yellow]')
                        except requests.RequestException as exc:
                            if self.verbose:
                                console.print(f'[yellow]Falha ao resolver botão Pixeldrain: {exc}[/yellow]')
                    elif self.verbose:
                        console.print('[yellow]Formato do botão Pixeldrain não suportado.[/yellow]')
                    continue  # Nunca interpretar o botão Pixeldrain como download directo.

                if host in pixel_hosts and re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?', parsed.path):
                    pixeldrain.append(href)
                elif (host in getcomics_hosts and parsed.path.startswith(('/download', '/dlds/', '/dls/'))
                      and ('DOWNLOAD NOW' in label or 'MAIN SERVER' in label)):
                    direct.append(href)
                elif host in ('mediafire.com', 'www.mediafire.com') and 'MEDIAFIRE' in label:
                    mediafire.append(href)

            if pixeldrain:
                primary = pixeldrain[0]
                self.comic_links[primary] = title
                if direct:
                    self.fallback_links[primary] = direct[0]
                console.print(f'[green]Servidor seleccionado: PIXELDRAIN — {title}[/green]')
            elif direct:
                self.comic_links[direct[0]] = title
                if self.verbose:
                    console.print(f'[yellow]Servidor seleccionado: GetComics Direct — {title}[/yellow]')
            elif mediafire:
                self.comic_links['_MEDIAFIRE_' + mediafire[0]] = title
                if self.verbose:
                    console.print(f'[yellow]Servidor seleccionado: MediaFire — {title}[/yellow]')
            elif self.verbose:
                console.print(f'[yellow]Sem links reconhecidos: {page_url}[/yellow]')
