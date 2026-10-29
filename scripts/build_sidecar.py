import platform
import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description='Build the native local engine without downloading models')
parser.add_argument('--with-tts',action='store_true',help='Experimental: include already installed TTS dependencies')
parser.add_argument('--onedir',action='store_true',help='Keep large CUDA libraries beside the executable instead of extracting them on every launch')
parser.add_argument('--ffmpeg-dir',type=Path,help='Include an existing FFmpeg directory, including its licenses and DLLs')
args=parser.parse_args()
system,arch=platform.system(),platform.machine().lower()
targets={('Windows','amd64'):'x86_64-pc-windows-msvc',('Darwin','arm64'):'aarch64-apple-darwin',('Darwin','x86_64'):'x86_64-apple-darwin'}
target=targets.get((system,arch))
if not target:raise SystemExit('Build on Windows x64 or macOS Intel/Apple Silicon')
command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir' if args.onedir else '--onefile','--name','books-engine',
    '--paths',str(root),'--collect-submodules','uvicorn','--collect-submodules','keyring.backends',
    '--collect-data','langdetect',
    '--collect-submodules','num2words','--distpath',str(root/('build/sidecar-full' if args.onedir else 'build/sidecar')),
    '--workpath',str(root/'build/pyinstaller'),'--specpath',str(root/'build')]
if args.with_tts:
    packages=['qwen_tts','torch','torchaudio','transformers','accelerate','tokenizers',
              'huggingface_hub','soundfile','librosa','onnxruntime','einops','sox','faster_qwen3_tts','ruaccent']
    missing=[package for package in packages if importlib.util.find_spec(package) is None]
    if missing:raise SystemExit('Install approved TTS dependencies into this build environment first: '+', '.join(missing))
    for package in packages:command+=['--collect-all',package]
    for distribution in ['qwen-tts','faster-qwen3-tts','ruaccent-predictor']:
        command+=['--copy-metadata',distribution]
if args.ffmpeg_dir:
    folder=args.ffmpeg_dir.expanduser().resolve()
    if not (folder/('ffmpeg.exe' if system=='Windows' else 'ffmpeg')).is_file():
        raise SystemExit('The supplied directory must contain ffmpeg (and its licenses/runtime dependencies)')
    command+=['--add-data',str(folder)+':tools']
command.append(str(root/'client/sidecar.py'))
subprocess.run(command,check=True,cwd=root)
if args.onedir:
    print(root/'build/sidecar-full/books-engine')
    raise SystemExit(0)
extension='.exe' if system=='Windows' else ''
destination=root/'client/ui/src-tauri/binaries'/f'books-engine-{target}{extension}'
destination.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(root/'build/sidecar'/f'books-engine{extension}',destination)
print(destination)
