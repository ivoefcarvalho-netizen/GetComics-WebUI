"""Pausa/retoma cooperativa. aria2c suspenso em macOS e Windows."""
import os
import signal
import threading

class DownloadStopped(Exception):
    """Terminar rapidamente sem marcar downloads interrompidos como falhados."""


class PauseController:
    def __init__(self):
        self._gate = threading.Event()
        self._gate.set()
        self._lock = threading.Lock()
        self._proc = None
        self._stopping = False

    @property
    def paused(self):
        return not self._gate.is_set()

    @property
    def stopping(self):
        return self._stopping

    def wait(self):
        self._gate.wait()
        if self._stopping:
            raise DownloadStopped()

    def stop(self):
        with self._lock:
            self._stopping = True
            self._set_process_paused(False)
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except (OSError, ProcessLookupError):
                    pass
            self._gate.set()

    def pause(self):
        with self._lock:
            self._gate.clear()
            self._set_process_paused(True)

    def resume(self):
        with self._lock:
            self._set_process_paused(False)
            self._gate.set()

    def _set_process_paused(self, paused):
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            if os.name == 'nt':
                import psutil
                process = psutil.Process(self._proc.pid)
                process.suspend() if paused else process.resume()
            else:
                self._proc.send_signal(signal.SIGSTOP if paused else signal.SIGCONT)
        except (OSError, ProcessLookupError, AttributeError):
            pass

    def register_process(self, proc):
        with self._lock:
            self._proc = proc
            if not self._gate.is_set():
                self._set_process_paused(True)

    def unregister_process(self, proc):
        with self._lock:
            if self._proc is proc:
                self._proc = None

PAUSE = PauseController()
