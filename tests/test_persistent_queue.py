"""Testes sem Internet: fila persistente, retoma e repetição de falhas."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from queue_store import load_queue, save_queue
import getcomics_web_ui.webui as w


def sample(status='queued', title='Teste #01 (2026)'):
    return {'title':title, 'status':status, 'source':'Pixeldrain',
            'message':'Teste', 'row':{'title':title,'page_url':'https://getcomics.org/example',
                                   'links':[{'kind':'direct','label':'Servidor',
                                             'url':'https://example.com/file.cbz'}]},
            'folder':'C:\\Downloads\\Teste', 'attempts':1}


class QueueFileTests(unittest.TestCase):
    def test_reload_states_and_keep_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            save_queue(path,[sample('running'),sample('queued','Teste #02'),
                             sample('done','Teste #03'),sample('error','Teste #04')])
            restored=load_queue(path)
            self.assertEqual([x['status'] for x in restored],
                             ['interrupted','queued','done','error'])
            self.assertEqual(restored[0]['folder'],'C:\\Downloads\\Teste')
            self.assertEqual(restored[0]['row']['links'][0]['kind'],'direct')
            self.assertNotIn('PIXELDRAIN_API_KEY',path.read_text())
            if os.name!='nt': self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_nonexistent_and_corrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            self.assertEqual(load_queue(path),[])
            path.write_text('{broken',encoding='utf-8')
            with self.assertRaises(ValueError): load_queue(path)


class QueueApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.queue_file=patch.object(w,'QUEUE_FILE',Path(self.tmp.name)/'queue.json')
        self.queue_file.start()
        self.old_downloads=w.state['downloads']
        w.state['downloads']=[sample('error'),sample('interrupted','Teste #02'),
                              sample('done','Teste #03')]
        w.scheduled_indices.clear()
        self.calls=[]
        self.submit=patch.object(w.download_pool,'submit',side_effect=self.capture)
        self.submit.start()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),w.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_address[1]}'

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=2)
        self.submit.stop()
        w.state['downloads']=self.old_downloads
        w.scheduled_indices.clear()
        self.queue_file.stop();self.tmp.cleanup()

    def capture(self,fn,*args):
        self.calls.append((fn,args))

    def post(self,path,data):
        req=Request(self.base+path,data=json.dumps(data).encode(),method='POST',
                    headers={'X-App-Token':w.TOKEN,'Content-Type':'application/json'})
        with urlopen(req,timeout=2) as res:return json.load(res)

    def test_retry_and_resume_without_repeating_completed(self):
        self.assertEqual(self.post('/api/retry-failed',{})['count'],1)
        self.assertEqual(w.state['downloads'][0]['status'],'queued')
        self.assertEqual(self.post('/api/retry-failed',{})['count'],0)
        self.assertEqual(self.post('/api/resume-pending',{})['count'],1)
        self.assertEqual(self.post('/api/resume-pending',{})['count'],0)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len(load_queue(w.QUEUE_FILE)),3)
        self.assertEqual(w.state['downloads'][2]['status'],'done')

    def test_api_hides_download_urls_and_shows_retry_controls(self):
        with urlopen(self.base+'/api/state',timeout=2) as res:data=json.load(res)
        self.assertEqual(data['downloads'][0]['status'],'error')
        self.assertNotIn('row',data['downloads'][0])
        self.assertNotIn('folder',data['downloads'][0])
        with urlopen(self.base+'/',timeout=2) as res:html=res.read().decode()
        for phrase in ('Retomar pendentes','Repetir falhados','retry-single'):
            self.assertIn(phrase,html)
        self.assertEqual(self.post('/api/retry-failed',{'id':2})['count'],0)
        self.assertEqual(self.post('/api/retry-failed',{'id':0})['count'],1)

    def test_closing_preserves_remaining_work(self):
        w.state['downloads'] = [sample('running'), sample('queued','Teste #02')]
        with patch.object(w.PAUSE, 'stop') as stop, \
             patch.object(w.download_pool,'shutdown') as shutdown_dl, \
             patch.object(w.pool,'shutdown') as shutdown_search:
            w.shutdown_downloads()
        self.assertTrue(stop.called)
        self.assertTrue(shutdown_dl.called)
        self.assertTrue(shutdown_search.called)
        persisted = load_queue(w.QUEUE_FILE)
        self.assertEqual([x['status'] for x in persisted], ['interrupted','queued'])

    def test_item_failure_is_saved_for_next_session(self):
        w.state['downloads'] = [sample('queued')]
        with patch.object(w, 'resolve_links', return_value=[]):
            w.download_one(w.state['downloads'][0]['row'],0,Path(self.tmp.name))
        self.assertEqual(load_queue(w.QUEUE_FILE)[0]['status'],'error')

    def test_items_persist_automatically_on_enqueue(self):
        w.state['downloads']=[]
        w.state['results']=[{'title':'Demo #10','page_url':'https://getcomics.org/page/',
                             'links':[{'kind':'direct','url':'https://example.com/a.cbz','label':'Link'}]}]
        self.assertEqual(w.download_job_prepare([0]),1)
        restored=load_queue(w.QUEUE_FILE)
        self.assertEqual(restored[0]['title'],'Demo #10')
        self.assertEqual(restored[0]['status'],'queued')
        self.assertEqual(restored[0]['folder'],str(w.DEST))


if __name__=='__main__': unittest.main()
