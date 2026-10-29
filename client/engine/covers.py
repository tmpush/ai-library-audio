import base64
import hashlib
import io
import posixpath
import zipfile
from pathlib import Path
from urllib.parse import unquote
from defusedxml import ElementTree as ET
from PIL import Image

MAX_COVER=8*1024*1024


def store_cover(data:bytes, root:Path):
    if len(data)>MAX_COVER:raise ValueError('Обложка превышает 8 МБ')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width*source.height>20_000_000:raise ValueError('Обложка превышает 20 мегапикселей')
            image=source.convert('RGB')
            image.thumbnail((1600,1600))
            output=io.BytesIO()
            image.save(output,format='JPEG',quality=90)
    except (OSError,Image.DecompressionBombError) as exc:
        raise ValueError('Не удалось прочитать изображение обложки') from exc
    data=output.getvalue()
    identifier=hashlib.sha256(data).hexdigest()
    root.mkdir(parents=True,exist_ok=True)
    (root/(identifier+'.jpg')).write_bytes(data)
    return identifier


def extract_cover(path:Path):
    if path.suffix.lower()=='.fb2':
        root=ET.fromstring(path.read_bytes())
        ns={'f':'http://www.gribuser.ru/xml/fictionbook/2.0'}
        image=root.find('f:description/f:title-info/f:coverpage/f:image',ns)
        if image is None:return None
        href=image.get('{http://www.w3.org/1999/xlink}href','')
        if not href.startswith('#'):return None
        for binary in root.findall('f:binary',ns):
            if binary.get('id')==href[1:]:
                raw=''.join((binary.text or '').split())
                if len(raw)>MAX_COVER*4//3+4:raise ValueError('Обложка превышает 8 МБ')
                return base64.b64decode(raw,validate=True)
    if path.suffix.lower()=='.epub':
        with zipfile.ZipFile(path) as archive:
            root=ET.fromstring(archive.read('META-INF/container.xml'))
            package_path=next(n.get('full-path') for n in root.iter() if n.tag.endswith('}rootfile'))
            package=ET.fromstring(archive.read(package_path))
            cover_id=next((n.get('content') for n in package.iter() if n.tag.endswith('}meta') and n.get('name')=='cover'),None)
            item=next((n for n in package.iter() if n.tag.endswith('}item') and ('cover-image' in n.get('properties','').split() or (cover_id and n.get('id')==cover_id))),None)
            if item is None:return None
            name=posixpath.normpath(posixpath.join(posixpath.dirname(package_path),unquote(item.get('href',''))))
            if name.startswith(('../','/')) or ':' in name:raise ValueError('Недопустимый путь обложки')
            if archive.getinfo(name).file_size>MAX_COVER:raise ValueError('Обложка превышает 8 МБ')
            return archive.read(name)
    return None
