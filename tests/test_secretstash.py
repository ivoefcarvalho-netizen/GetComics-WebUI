"""Testes da importação Secret Stash (sem pedidos externos)."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from secretstash import parse_wishlist, matches_issue, best_match
import getcomics_web_ui.webui as w

CSV=('\ufeffSérie;Ano;Editora;Número;Tipo;Data de venda;ComicVine\n'
     'Amazing Spider-Man;2026;Marvel;1;em falta;;https://example.org/x\n'
     'Amazing Spider-Man;2026;Marvel;11;em falta;;https://example.org/y\n'
     'Street Fighter II;2005;UDON;7;em falta;;\n'
     'Homunculus (Omnibus);2023;Seven Seas;2;em falta;;\n')

class ParseAndMatch(unittest.TestCase):
    def test_actual_secretstash_csv(self):
        path=ROOT.parent.parent/'SecretStash-desejos-2026-10-31.csv'
        if path.exists():
            rows=parse_wishlist(path.read_text(encoding='utf-8-sig'))
            self.assertEqual(len(rows),66)
            self.assertEqual(len({row['series'] for row in rows}),12)
        else:
            self.skipTest('Ficheiro de exemplo apenas presente no ambiente de desenvolvimento')

    def test_parse_bom_semicolon_and_dedup(self):
        rows=parse_wishlist(CSV+CSV.splitlines()[1]+'\n')
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[0]['series'],'Amazing Spider-Man')
        self.assertEqual(rows[0]['number'],1)
        self.assertEqual(rows[0]['year'],2026)
        self.assertNotIn('ComicVine',rows[0])

    def test_reject_malformed_csv(self):
        for text in ('', 'title,year,issue\na,2026,1', 'Série;Ano;Número\nX;broken;9'):
            with self.assertRaises(ValueError):parse_wishlist(text)

    def test_exact_series_issue_year_and_ranges(self):
        i=parse_wishlist(CSV)
        self.assertTrue(matches_issue(i[0],'Amazing Spider-Man #001 (2026)'))
        self.assertFalse(matches_issue(i[0],'Amazing Spider-Man #11 (2026)'))
        self.assertFalse(matches_issue(i[0],'Amazing Spider-Man #1 (2025)'))
        self.assertFalse(matches_issue(i[0],'Amazing Spider-Man #1-6 (2026)'))
        self.assertFalse(matches_issue(i[0],'Amazing Spider-Man #1 (2026) TPB #1-6'))
        self.assertTrue(matches_issue(i[1],'Amazing Spider-Man 011 (2026)'))
        self.assertTrue(matches_issue(i[2],'Street Fighter II #7 (2005)'))
        self.assertFalse(matches_issue({'series':'Street Fighter','year':2005,'number':7},'Street Fighter II #7 (2005)'))
        self.assertTrue(matches_issue(i[3],'Homunculus Omnibus Vol 2 (2023)'))

    def test_best_match_requires_getcomics_page(self):
        i=parse_wishlist(CSV)[0]
        items=[{'title':'Amazing Spider-Man #1 (2026)','page_url':'https://invalid.example/p'},
               {'title':'Amazing Spider-Man #1 (2026)','page_url':'https://getcomics.org/test/'}]
        match=best_match(i,items)
        self.assertEqual(match['page_url'],'https://getcomics.org/test/')


class StashAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.stash_path=patch.object(w,'STASH_FILE',Path(self.tmp.name)/'wishlist.json')
        self.queue_path=patch.object(w,'QUEUE_FILE',Path(self.tmp.name)/'downloads.json')
        self.stash_path.start();self.queue_path.start()
        self.previous=w.state['stash'];self.downloads=w.state['downloads']
        w.state['downloads']=[]
        w.state['stash']={'status':'idle','message':'','items':[],'processed':0,'total':0}
        self.submit=patch.object(w.pool,'submit')
        self.mock_submit=self.submit.start()
        self.download_submit=patch.object(w.download_pool,'submit')
        self.download_submit.start()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),w.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_address[1]}'

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=2)
        w.state['stash']=self.previous;w.state['downloads']=self.downloads
        w.scheduled_indices.clear()
        self.submit.stop();self.download_submit.stop()
        self.stash_path.stop();self.queue_path.stop();self.tmp.cleanup()

    def post(self,path,payload):
        req=Request(self.base+path,method='POST',data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json','X-App-Token':w.TOKEN})
        with urlopen(req,timeout=2) as resp:return json.load(resp)

    def test_import_persist_and_manual_download(self):
        response=self.post('/api/stash/import',{'csv':CSV})
        self.assertEqual(response['count'],4)
        saved=json.loads(w.STASH_FILE.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['items']),4)
        self.assertNotIn('PIXELDRAIN_API_KEY',w.STASH_FILE.read_text())
        w.update_stash_item(0,status='matched',match={'title':'Amazing Spider-Man #1 (2026)',
                             'page_url':'https://getcomics.org/test/', 'links':[], 'source':'Por verificar'})
        self.assertEqual(self.post('/api/stash/download',{'ids':[0]})['count'],1)
        self.assertEqual(len(w.state['downloads']),1)
        self.assertEqual(w.state['downloads'][0]['title'],'Amazing Spider-Man #1 (2026)')
        self.assertEqual(self.post('/api/stash/download',{'ids':[0]})['count'],0)

    def test_search_runs_in_background(self):
        self.post('/api/stash/import',{'csv':CSV})
        self.assertTrue(self.post('/api/stash/search',{})['ok'])
        self.assertEqual(w.state['stash']['status'],'running')
        self.assertEqual(self.mock_submit.call_count,1)

    def test_rate_limit_keeps_unprocessed_items(self):
        self.post('/api/stash/import',{'csv':CSV})
        from requests import HTTPError, Response
        blocked=Response();blocked.status_code=429
        with patch.object(w,'stash_fetch_series',side_effect=HTTPError('429 Too Many Requests',response=blocked)), \
             patch('time.sleep',return_value=None):
            w.stash_search_job(refresh=True)
        self.assertEqual(w.state['stash']['status'],'limited')
        self.assertTrue(all(x['status']=='pending' for x in w.state['stash']['items']))
        self.assertEqual(json.loads(w.STASH_FILE.read_text())['items'][0]['status'],'pending')

    def test_one_series_fetch_multiple_matches(self):
        items=parse_wishlist(CSV)[:2]
        class Response:
            def __init__(self,html):self.text=html
        class Session: pass
        urls=[]
        def fetch(session,url):
            urls.append(url)
            if '/page/1?' in url:
                return Response('<article><h1 class="post-title"><a href="https://getcomics.org/s1">Amazing Spider-Man #1 (2026)</a></h1></article>'
                                '<article><h1 class="post-title"><a href="https://getcomics.org/s11">Amazing Spider-Man #11 (2026)</a></h1></article>')
            return Response('')
        with patch.object(w,'fetch_with_retries',side_effect=fetch), patch.object(w,'CACHE_DIR',Path(self.tmp.name)):
            rows,complete,pages=w.stash_fetch_series(Session(),'Amazing Spider-Man',items,refresh=True)
        self.assertTrue(complete)
        self.assertEqual(pages,1)
        self.assertEqual(len(urls),1)
        self.assertTrue(best_match(items[0],rows))
        self.assertTrue(best_match(items[1],rows))

if __name__=='__main__':unittest.main()
