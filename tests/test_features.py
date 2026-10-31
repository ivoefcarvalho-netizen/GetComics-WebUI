import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'getcomics_web_ui'))
from getcomics_page import parse_article, tag_slug, tag_page_url, parse_listing, valid_article_url

SAMPLE = '''<html><body><article><h1>Saga Vol. 1 – 10 + Book 1 – 3 (TPB) (2012-2022)</h1>
<div class="entry-content"><h2>Free Comics Download</h2>
<p>Saga Book 1 (2014) (841 MB) : <a href="https://getcomics.org/dls/aaaa">Link 1</a> |
<a href="https://getcomics.org/dls/bbbb">Link 2</a> |
<a href="https://mega.nz/file/aaaa">Mega</a></p>
<p>Saga Book 2 (2017) (420 MB) :</p>
<p><a href="https://getcomics.org/dls/cccc">Main Server</a> |
<a href="https://www.mediafire.com/file/abcd">Mediafire</a></p>
<p>Saga Vol. 10 (2022) (167 MB) :</p>
<p><a href="https://pixeldrain.com/u/abcd1234">PIXELDRAIN</a> |
<a href="https://getcomics.org/dls/dddd">Main Server</a></p>
<h3>Screenshots :</h3><p><a href="https://getcomics.org/dls/should-not-appear">Main Server</a></p></div></article>
<div class="tags"><a href="https://getcomics.org/tag/saga/">Saga</a>
<a href="/tag/tpb/">TPB</a></div></body></html>'''
PAGE='https://getcomics.org/other-comics/saga-vol-1-7-book-1-2-tpb-2012-2017/'

class ParseTests(unittest.TestCase):
    def test_book_groups_and_alternatives(self):
        result=parse_article(SAMPLE,PAGE)
        self.assertEqual(len(result['items']),3)
        self.assertEqual([x['title'] for x in result['items']],
                         ['Saga Book 1 (2014) (841 MB)','Saga Book 2 (2017) (420 MB)',
                          'Saga Vol. 10 (2022) (167 MB)'])
        self.assertEqual([x['kind'] for x in result['items'][0]['links']],['direct','direct','manual'])
        self.assertEqual([x['kind'] for x in result['items'][1]['links']],['direct','manual'])
        self.assertEqual([x['kind'] for x in result['items'][2]['links']],['pixeldrain','direct'])
        self.assertNotIn('should-not-appear',repr(result))
        self.assertEqual({x['slug'] for x in result['tags']},{'saga','tpb'})

    def test_tag_scope_urls_and_sanitizing(self):
        self.assertEqual(tag_slug(' Saga '), 'saga')
        self.assertEqual(tag_slug('https://getcomics.org/tag/saga/'),'saga')
        self.assertEqual(tag_page_url('saga',1),'https://getcomics.org/tag/saga/')
        self.assertEqual(tag_page_url('saga',2),'https://getcomics.org/tag/saga/page/2/')
        for bad in ('https://evil.example/tag/saga/','../admin','/tag//..'):
            with self.assertRaises(ValueError): tag_slug(bad)

    def test_article_url_restrictions(self):
        self.assertEqual(valid_article_url(PAGE), PAGE)
        for bad in ('http://getcomics.org/xxx','https://evil.example/path',
                    'https://getcomics.org/tag/saga/','https://getcomics.org/'):
            with self.assertRaises(ValueError):valid_article_url(bad)

    def test_search_listing(self):
        html='''<article><h1 class="post-title"><a href="/other-comics/saga-72/">Saga #72 (2025)</a></h1></article>
                 <article><h1 class="post-title"><a href="/other-comics/saga-71/">Saga #71 (2025)</a></h1></article>'''
        self.assertEqual(len(parse_listing(html)),2)

    def test_fallback_direct_to_pixeldrain(self):
        import getcomics_web_ui.webui as webui
        with tempfile.TemporaryDirectory() as folder:
            dst=Path(folder)
            book=dst/'a.cbz'
            import zipfile
            with zipfile.ZipFile(book,'w') as z:z.writestr('01.jpg',b'JPEG'+b'0'*1100)
            row={'title':'Saga Book 1 (2014)', 'links':[
                {'url':'https://getcomics.org/dls/invalid','kind':'direct','label':'Link 1'},
                {'url':'https://pixeldrain.com/u/a','kind':'pixeldrain','label':'Pixeldrain'}]}
            with webui.lock:
                webui.state['downloads']=[{'status':'queued','title':row['title'],'message':'Em espera'}]
            with patch.object(webui,'direct_download', side_effect=ValueError('HTTP 403')) as a, \
                 patch.object(webui,'download_pixeldrain',return_value=book) as b:
                webui.download_one(row,0,dst)
            self.assertEqual(webui.state['downloads'][0]['status'],'done')
            self.assertEqual(a.call_count,1)
            self.assertEqual(b.call_count,1)

if __name__=='__main__':unittest.main()

class ServerIntegrationTests(unittest.TestCase):
    def test_article_api_returns_selectable_books(self):
        import getcomics_web_ui.webui as w
        import urllib.request
        import json
        import time
        from http.server import ThreadingHTTPServer
        from threading import Thread
        class FakeResponse:
            def __init__(self, text): self.text=text
            def raise_for_status(self): return None
        server=ThreadingHTTPServer(('127.0.0.1',0),w.Handler)
        t=Thread(target=server.serve_forever,daemon=True)
        t.start()
        address=f'http://127.0.0.1:{server.server_address[1]}'
        try:
            with patch.object(w.requests,'get',return_value=FakeResponse(SAMPLE)):
                data=json.dumps({'url':PAGE}).encode('utf-8')
                req=urllib.request.Request(address+'/api/article',data=data,method='POST',headers={
                    'X-App-Token':w.TOKEN,'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=3) as r:
                    self.assertTrue(json.load(r)['ok'])
                deadline=time.time()+3
                while time.time()<deadline:
                    with urllib.request.urlopen(address+'/api/state',timeout=3) as r:
                        state=json.load(r)
                    if state['search']['status']=='done':break
                    time.sleep(0.04)
                self.assertEqual(state['search']['status'],'done')
                self.assertEqual([x['title'].split(' (')[0] for x in state['results']],
                                 ['Saga Book 1','Saga Book 2','Saga Vol. 10'])
                self.assertEqual(len(state['tags']),2)
                with urllib.request.urlopen(address+'/',timeout=3) as r:
                    self.assertIn('Escolher pasta',r.read().decode('utf-8'))
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=3)

    def test_tag_search_never_uses_global_search(self):
        import getcomics_web_ui.webui as w
        class FakePage:
            def __init__(self, text):self.text=text
        listing='''<article><h1 class="post-title"><a href="/other-comics/saga-12/">Saga #12 (2020)</a></h1></article>
        <article><h1 class="post-title"><a href="/other-comics/different/">Different #1 (2020)</a></h1></article>'''
        requested=[]
        def fake_fetch(_, url):
            requested.append(url)
            return FakePage(listing if 'page/2/' not in url else '<article></article>')
        with patch.object(w,'fetch_with_retries',side_effect=fake_fetch), \
             patch.object(w,'CACHE_DIR',Path(tempfile.mkdtemp(prefix='gc_test_cache_'))):
            w.search_job('Saga',False,True,2020,'saga')
        with w.lock:
            found=list(w.state['results'])
        self.assertEqual(len(found),1)
        self.assertTrue(all(url.startswith('https://getcomics.org/tag/saga/') for url in requested))
        self.assertTrue(all('?s=' not in url for url in requested))
