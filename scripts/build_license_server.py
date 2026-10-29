import subprocess
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--noconsole',
    '--name','books-license-server','--paths',str(root),'--collect-submodules','uvicorn',
    '--distpath',str(root/'build/license-server'),'--workpath',str(root/'build/license-pyinstaller'),
    '--specpath',str(root/'build/license-pyinstaller')]
for name in ['admin.html','admin.js','policy.json']:
    command+=['--add-data',f'{root / "server" / name}:server']
command.append(str(root/'server/local_service.py'))
subprocess.run(command,check=True,cwd=root)
