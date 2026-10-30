"""Build an Apple Silicon .app + .pkg + .dmg on a Mac, without user data/models."""
import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.3.1'

def run(*args):
    subprocess.run([str(a) for a in args], check=True)

def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('Build on an Apple Silicon Mac. Intel TTS is not supported by this dependency set.')
    stage = ROOT / 'build' / 'macos-package'
    if stage.exists():
        raise SystemExit('Use a fresh build/macos-package directory (existing package is preserved).')
    app = stage / 'Книжная мастерская.app'
    contents = app / 'Contents'
    macos = contents / 'MacOS'
    resources = contents / 'Resources'
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(ROOT / 'client/native/target/release/local-books-order', macos)
    shutil.copytree(ROOT / 'build/sidecar-full/books-engine', macos / 'engine', symlinks=True)
    for f in (ROOT / 'client/native/assets').glob('*-OFL.txt'):
        shutil.copy2(f, resources)
    with (contents / 'Info.plist').open('wb') as f:
        plistlib.dump({
            'CFBundleIdentifier': 'app.localbooks.studio',
            'CFBundleName': 'Книжная мастерская',
            'CFBundleDisplayName': 'Книжная мастерская',
            'CFBundleExecutable': 'local-books-order',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': VERSION,
            'CFBundleVersion': VERSION,
            'LSMinimumSystemVersion': '14.0',
            'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': 'Запись вашего голоса для локальной озвучки книг.',
        }, f)
    # Ad-hoc signature is for local integrity only, not Apple notarization.
    run('codesign', '--force', '--deep', '--sign', '-', app)
    run('codesign', '--verify', '--deep', '--strict', app)
    run(os.sys.executable, ROOT/'scripts/verify_sidecar.py', '--executable', macos/'engine/books-engine')
    run(macos/'engine/_internal/tools/ffmpeg', '-version')
    output = ROOT/'dist/macos'
    output.mkdir(parents=True, exist_ok=True)
    pkg = output/f'LocalBooksStudio-{VERSION}-macos-arm64.pkg'
    run('pkgbuild', '--component', app, '--install-location', '/Applications',
        '--identifier', 'app.localbooks.studio', '--version', VERSION, pkg)
    disk = ROOT/'build/macos-disk'
    disk.mkdir()
    shutil.copy2(pkg, disk)
    shutil.copy2(ROOT/'packaging/macos/README.txt', disk/'README.txt')
    dmg = output/f'LocalBooksStudio-{VERSION}-macos-arm64.dmg'
    run('hdiutil', 'create', '-volname', 'Local Books Studio', '-srcfolder', disk, '-format', 'UDZO', dmg)
    with (output/'SHA256SUMS.txt').open('w') as f:
        for path in [pkg, dmg]:
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            f.write(f'{digest}  {path.name}\n')

if __name__ == '__main__':
    main()
