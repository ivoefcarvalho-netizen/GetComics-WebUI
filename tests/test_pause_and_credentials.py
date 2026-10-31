import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from download_controls import PauseController, PAUSE
import getcomics_web_ui.webui as w


class PauseTests(unittest.TestCase):
    def test_gate_pauses_wait_and_resumes(self):
        controller=PauseController()
        controller.pause()
        done=threading.Event()
        worker=threading.Thread(target=lambda: (controller.wait(), done.set()), daemon=True)
        worker.start()
        self.assertFalse(done.wait(0.05))
        controller.resume()
        self.assertTrue(done.wait(1))
        worker.join(timeout=1)

    def test_sigstop_sigcont_for_mac_process(self):
        controller=PauseController()
        class FakeProc:
            def __init__(self):self.calls=[]
            def poll(self):return None
            def send_signal(self,sig):self.calls.append(sig)
        proc=FakeProc()
        controller.register_process(proc)
        controller.pause()
        controller.resume()
        controller.unregister_process(proc)
        if os.name=='posix':
            self.assertEqual(proc.calls,[signal.SIGSTOP,signal.SIGCONT])
        else:
            # A implementacao Windows usa psutil; validar num teste Windows real.
            self.assertEqual(proc.calls,[])


class APIKeyTests(unittest.TestCase):
    def test_save_and_remove_key_in_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'pixeldrain.key'
            marker=Path(folder)/'.migrated'
            with patch.object(w,'KEY_FILE',key),patch.object(w,'KEY_MIGRATED',marker),patch.dict(os.environ,{},clear=False):
                w._save_api_key('test-key-123')
                if os.name == 'nt':
                    from windows_secret import unprotect
                    self.assertEqual(unprotect(key.read_bytes()),b'test-key-123')
                    self.assertNotIn(b'test-key-123',key.read_bytes())
                else:
                    self.assertEqual(key.read_text(),'test-key-123')
                    self.assertEqual(key.stat().st_mode & 0o777,0o600)
                self.assertEqual(os.environ['PIXELDRAIN_API_KEY'],'test-key-123')
                w._remove_api_key()
                self.assertFalse(key.exists())
                self.assertNotIn('PIXELDRAIN_API_KEY',os.environ)
                self.assertTrue(marker.exists())

    def test_http_pause_resume_and_key_no_exposure(self):
        http=ThreadingHTTPServer(('127.0.0.1',0),w.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_address[1]}'
        try:
            for endpoint,expect in [('/api/pause',True),('/api/resume',False)]:
                req=Request(base+endpoint,data=b'{}',method='POST',headers={'X-App-Token':w.TOKEN,'Content-Type':'application/json'})
                with urlopen(req,timeout=2) as res:result=json.load(res)
                self.assertEqual(result['paused'],expect)
                with urlopen(base+'/api/state',timeout=2) as res:state=json.load(res)
                self.assertEqual(state['paused'],expect)
                self.assertIn('has_api_key',state)
                self.assertNotIn('api_key',state)
            with urlopen(base+'/',timeout=2) as res:html=res.read().decode()
            self.assertIn('Pausar downloads',html)
            self.assertIn('Chave API Pixeldrain',html)
        finally:
            PAUSE.resume()
            http.shutdown();http.server_close();thread.join(timeout=2)

if __name__=='__main__': unittest.main()
