"""Frozen local engine entry point. Session token arrives only through parent environment."""
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn
from client.engine.api import create_app


class ReadyServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets)
        if self.started:
            port = sockets[0].getsockname()[1]
            print(json.dumps({'type':'ready','port':port}),flush=True)


def main():
    token=os.environ.get('BOOKS_LOCAL_TOKEN','')
    root=os.environ.get('BOOKS_DATA','')
    if len(token)<32 or not root:
        raise SystemExit('The desktop parent must supply BOOKS_LOCAL_TOKEN and BOOKS_DATA')
    # Frozen scientific extension initialization can hang in a Windows worker
    # thread (observed in NumPy and scipy.special). Initialize the TTS import
    # graph on the main thread before starting the server or render worker.
    # This loads libraries only: no checkpoint, CUDA allocation or download.
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import importlib.util
        if importlib.util.find_spec('numpy') is not None:
            import numpy  # noqa: F401
        if importlib.util.find_spec('qwen_tts') is not None:
            import torch  # noqa: F401
            import scipy.special, scipy.signal, scipy.linalg, scipy.fft  # noqa: F401
            import sklearn  # noqa: F401
            from librosa import load as load_audio  # noqa: F401
            import qwen_tts  # noqa: F401
            import faster_qwen3_tts  # noqa: F401
    app=create_app(Path(root),token)
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.bind(('127.0.0.1',0))
    server=ReadyServer(uvicorn.Config(app,host='127.0.0.1',port=0,log_level='warning',access_log=False))
    def parent_watch():
        # Closing the parent's stdin pipe requests a graceful sidecar shutdown.
        try:
            for line in sys.stdin:
                if line.strip()=='shutdown':break
        finally:
            server.should_exit=True
    threading.Thread(target=parent_watch,daemon=True).start()
    server.run(sockets=[sock])


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
