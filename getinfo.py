"""Motor local de pesquisa e resolução de fontes, para a interface V3."""
import re
from urllib.parse import urljoin, urlparse, quote
import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/120 Safari/537.36'}

class GetComics:
    def __init__(self, query, results, verbose):
        self.query=query
        self.num_results_desired=results
        self.verbose=verbose
        self.page_links={}
        self.comic_links={}
        self.fallback_links={}

    @staticmethod
    def is_single_issue(title):
        if re.search(r'(?i)\b(?:TPB|trade paperback|omnibus|compendium|collection|collected edition|hardcover|graphic novel|bundle|complete series)\b',title):
            return False
        if re.search(r'(?i)(?:#\s*)?\d{1,4}\s*(?:-|–|—|to|through|&)\s*#?\s*\d{1,4}\b', title):
            return False
        return bool(re.search(r'#\s*\d{1,4}\b|(?<!\d)\d{1,4}\s*\((?:19|20)\d\d\)',title))

    def get_download_links(self):
        for page_url, title in self.page_links.items():
            response=requests.get(page_url,headers=HEADERS,timeout=(10,20))
            response.raise_for_status()
            soup=BeautifulSoup(response.text,'html.parser')
            pixel=[]; direct=[]; media=[]
            for a in soup.find_all('a',href=True):
                href=urljoin(page_url,a['href'].strip())
                p=urlparse(href)
                host=(p.hostname or '').lower()
                label=(a.get_text(' ',strip=True)+' '+str(a.get('title',''))).upper()
                if p.scheme not in ('http','https'): continue
                if host in ('pixeldrain.com','www.pixeldrain.com','pixeldrain.net','www.pixeldrain.net') and re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?',p.path):
                    pixel.append(href)
                elif 'PIXELDRAIN' in label and host in ('getcomics.org','www.getcomics.org') and p.path.startswith(('/dls/','/download','/dlds/')):
                    try:
                        with requests.get(href,headers=HEADERS,stream=True,allow_redirects=True,timeout=(10,20)) as r:
                            rp=urlparse(r.url)
                            if r.status_code<400 and (rp.hostname or '').lower() in ('pixeldrain.com','www.pixeldrain.com','pixeldrain.net','www.pixeldrain.net') and re.fullmatch(r'/(?:u|api/file)/[A-Za-z0-9_-]+/?',rp.path):
                                pixel.append(r.url)
                    except requests.RequestException:
                        pass
                elif host in ('getcomics.org','www.getcomics.org') and p.path.startswith(('/dls/','/download','/dlds/')) and ('DOWNLOAD NOW' in label or 'MAIN SERVER' in label):
                    direct.append(href)
                elif host in ('mediafire.com','www.mediafire.com') and 'MEDIAFIRE' in label:
                    media.append(href)
            if pixel:
                self.comic_links[pixel[0]]=title
                if direct:self.fallback_links[pixel[0]]=direct[0]
            elif direct:
                self.comic_links[direct[0]]=title
            elif media:
                self.comic_links['_MEDIAFIRE_'+media[0]]=title
