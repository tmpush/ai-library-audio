"""Explicit CLI entry point; expected revision and bytes must be supplied by the caller."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from client.engine.models import ModelDownload

parser=argparse.ArgumentParser()
parser.add_argument('--revision',required=True)
parser.add_argument('--bytes',required=True,type=int)
parser.add_argument('--confirm',action='store_true')
parser.add_argument('--directory',type=Path,default=Path('models'))
args=parser.parse_args()
download=ModelDownload()
print(json.dumps(download.start(args.directory,args.revision,args.bytes,args.confirm)),flush=True)
while download.thread.is_alive():
    download.thread.join(30)
    print(json.dumps(download.snapshot()),flush=True)
result=download.snapshot()
if result['status']!='done':raise SystemExit(result.get('error','Download did not complete'))
