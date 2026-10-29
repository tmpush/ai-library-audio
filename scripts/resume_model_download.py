"""Resume an explicitly approved, pinned model download using verified HTTP ranges."""
import argparse
import concurrent.futures
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.engine.models import model_info

parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, required=True)
parser.add_argument('--revision', required=True)
parser.add_argument('--bytes', type=int, required=True)
parser.add_argument('--confirm', action='store_true')
args = parser.parse_args()
if not args.confirm:
    raise SystemExit('Explicit confirmation is required')
info = model_info()
if info['revision'] != args.revision or info['bytes'] != args.bytes:
    raise SystemExit('The approved model manifest has changed')
if json.loads((args.directory / 'download-manifest.json').read_text()) != info:
    raise SystemExit('Destination does not belong to this download')

def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

for item in info['files']:
    target = args.directory / item['name']
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == item['size']:
        if item['sha256'] and sha256(target) == item['sha256']:
            continue
    url = f"https://huggingface.co/{info['id']}/resolve/{info['revision']}/{item['name']}"
    partial = target.with_suffix(target.suffix + '.part')
    if not item['sha256']:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) != item['size']:
                raise ValueError('Unexpected file size')
            partial.write_bytes(response.content)
        partial.replace(target)
        continue
    prefix = partial.stat().st_size if partial.exists() else 0
    if prefix > item['size']:
        raise ValueError('Existing partial file exceeds the approved size')
    parts = target.with_suffix(target.suffix + '.ranges')
    parts.mkdir(exist_ok=True)
    step = 16 * 1024 * 1024
    starts = list(range(prefix, item['size'], step))
    progress = [prefix]
    lock = threading.Lock()
    local = threading.local()

    def download(start):
        end = min(start + step, item['size']) - 1
        path = parts / str(start)
        if not path.is_file() or path.stat().st_size != end - start + 1:
            if not hasattr(local, 'client'):
                local.client = httpx.Client(timeout=60, follow_redirects=True)
            for attempt in range(5):
                try:
                    with local.client.stream('GET', url + f'?segment={start}', headers={'Range': f'bytes={start}-{end}'}) as response:
                        expected = f"bytes {start}-{end}/{item['size']}"
                        if response.status_code != 206 or response.headers.get('content-range') != expected:
                            raise ValueError('Server did not return the requested range')
                        written = 0
                        with path.with_suffix('.part').open('wb') as output:
                            for block in response.iter_bytes(1024 * 1024):
                                written += len(block)
                                if written > end - start + 1:
                                    raise ValueError('Oversized range')
                                output.write(block)
                        if written != end - start + 1:
                            raise ValueError('Incomplete range')
                    path.with_suffix('.part').replace(path)
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 4:
                        raise
                    time.sleep(1 + attempt)
        with lock:
            progress[0] += end - start + 1
            print(json.dumps({'file': item['name'], 'downloaded': progress[0], 'total': item['size']}), flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(download, starts))
    # Preserve the original prefix until the complete assembled file passes its hash check.
    assembled = target.with_suffix(target.suffix + '.assembled')
    with assembled.open('wb') as output:
        sources = ([partial] if prefix else []) + [parts / str(start) for start in starts]
        for source in sources:
            with source.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    output.write(block)
    if assembled.stat().st_size != item['size'] or sha256(assembled) != item['sha256']:
        raise ValueError('The assembled model failed integrity verification')
    assembled.replace(target)
    print(json.dumps({'file': item['name'], 'status': 'verified', 'sha256': item['sha256']}), flush=True)
print(json.dumps({'status': 'done', 'bytes': info['bytes'], 'revision': info['revision']}), flush=True)
