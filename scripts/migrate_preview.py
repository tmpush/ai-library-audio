"""One-time local migration; the original library is retained. Run with preview closed."""
import json
import os
import shutil
import sqlite3
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=root/'data/preview'
target=Path(os.environ['APPDATA'])/'app.localbooks.studio'
if target.exists() and any(target.iterdir()):
    raise SystemExit('Desktop data already exists; refusing to overwrite it')
target.mkdir(parents=True,exist_ok=True)
for file in source.rglob('*'):
    relative=file.relative_to(source)
    if not file.is_file() or file.name in {'preview-session.json','worker.lock'} or file.name.endswith(('-wal','-shm')):
        continue
    destination=target/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    if file.suffix=='.db':
        with sqlite3.connect(file) as old,sqlite3.connect(destination) as new:
            old.backup(new)
    else:
        shutil.copy2(file,destination)
report={'source':str(source),'destination':str(target),'original_preserved':True}
(root/'artifacts/desktop-migration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
