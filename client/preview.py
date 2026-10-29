"""Local browser preview. Uses the real engine and never enables an unlicensed renderer."""
import argparse
import json
import secrets
import socket
import webbrowser
from pathlib import Path
from urllib.parse import quote,unquote,urlparse
import httpx
import uvicorn
from .engine.api import create_app


def main():
    parser=argparse.ArgumentParser(description='Open the locally built book preparation UI')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--data-dir',type=Path,help='Separate local library for development/testing')
    parser.add_argument('--with-samples',action='store_true',help='Add the three original example books once')
    parser.add_argument('--stop',action='store_true',help='Stop this local preview instance')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    data=args.data_dir or root/'data/preview'
    session_file=data/'preview-session.json'
    if session_file.is_file():
        try:
            existing=json.loads(session_file.read_text(encoding='utf-8'))['url']
            parsed=urlparse(existing)
            session=json.loads(unquote(parsed.fragment.removeprefix('session=')))
            origin=f'{parsed.scheme}://{parsed.netloc}'
            if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or session['endpoint']!=origin:
                raise ValueError('Invalid preview address')
            headers={'Authorization':'Bearer '+session['token']}
            with httpx.Client(timeout=3) as connection:
                response=connection.get(origin+'/projects',headers=headers)
                response.raise_for_status()
                if args.stop:
                    connection.post(origin+'/preview-session/stop',headers=headers).raise_for_status()
                    return
            if args.no_browser:print(json.dumps({'url':existing}),flush=True)
            else:webbrowser.open(existing)
            return
        except (OSError,ValueError,KeyError,httpx.HTTPError):
            pass
    if args.stop:return
    frontend=root/'client/ui/dist'
    if not (frontend/'index.html').is_file():
        raise SystemExit('Build the UI first: cd client/ui, npm ci, npm run build')
    token=secrets.token_urlsafe(48)
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    sock.bind(('127.0.0.1',0))
    endpoint=f'http://127.0.0.1:{sock.getsockname()[1]}'
    session=quote(json.dumps({'endpoint':endpoint,'token':token}),safe='')
    url=endpoint+'/preview/#session='+session
    class PreviewServer(uvicorn.Server):
        async def startup(self,sockets=None):
            await super().startup(sockets)
            if self.started:
                session_file.write_text(json.dumps({'url':url}),encoding='utf-8')
                if args.no_browser:print(json.dumps({'url':url}),flush=True)
                else:
                    print('Книжная мастерская открывается в браузере. Закройте это окно, чтобы остановить обработчик.',flush=True)
                    webbrowser.open(url)
    app=create_app(data,token,frontend)
    if args.with_samples:
        from .engine.books import import_book
        library=app.state.studio.library
        hashes={library.load(project['id']).source_hash for project in library.list()}
        for filename in ['night-keeper.fb2','lighthouse.epub','forest-route.pdf']:
            book=import_book(root/'samples'/filename)
            if book.source_hash not in hashes:library.save(book)
    server=PreviewServer(uvicorn.Config(app,host='127.0.0.1',port=0,log_level='warning',access_log=False))
    @app.post('/preview-session/stop')
    def stop():
        server.should_exit=True
        return {'ok':True}
    try:server.run(sockets=[sock])
    finally:
        if session_file.is_file() and json.loads(session_file.read_text(encoding='utf-8')).get('url')==url:
            session_file.unlink()


if __name__=='__main__':main()
