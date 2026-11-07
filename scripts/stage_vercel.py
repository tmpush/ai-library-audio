"""Stage only the files needed by the Vercel license service."""
import argparse
from pathlib import Path
from shutil import copy2

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("destination", type=Path)
destination = parser.parse_args().destination.resolve()
root = Path(__file__).resolve().parents[1]
for folder, names in {
    "packaging/vercel": ("vercel.json", "requirements.txt", "api/index.py"),
    "server": ("__init__.py", "api.py", "admin.html", "admin.js", "licenses.py", "database.py", "policy.json"),
    "shared": ("__init__.py", "license.py"),
}.items():
    for name in names:
        target = destination / ("" if folder == "packaging/vercel" else folder) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(root / folder / name, target)
