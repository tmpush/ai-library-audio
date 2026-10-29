"""Create original synthetic fixtures; no downloaded or copyrighted book content."""
from pathlib import Path
from html import escape
import zipfile
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import simpleSplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
OUT.mkdir(exist_ok=True)

stories = [
    ("Ночной смотритель", [
        ("Глава 1. Окно у реки", ["Вечером Анна открыла окно библиотеки. На другом берегу зажглись 12 фонарей. Каждый отражался в воде, и отражения медленно плыли к мосту.", "На столе лежала тетрадь смотрителя. На первой странице было написано: «Не торопись закрывать дверь. Иногда читатель приходит после дождя»."]),
        ("Глава 2. Последний читатель", ["В дверь постучал человек с мокрым зонтом. Он искал книгу о птицах, но названия не помнил.", "Анна принесла три тома. Человек открыл самый тонкий и улыбнулся. За окном перестал шуметь дождь."])]),
    ("Письма с маяка", [
        ("Глава 1. Посылка", ["На остров доставили небольшую посылку. Внутри оказались компас, карандаш и письмо без подписи.", "Смотритель записал дату: 12.10.2026. Потом проверил часы и поднялся по лестнице. До площадки оставалось 25 ступеней."]),
        ("Глава 2. Северный ветер", ["Ночью ветер переменился. Море стало темнее, а далёкие огни исчезли за туманом.", "Утром смотритель ответил на письмо. Он рассказал о погоде и о чайке, которая каждый день садилась на перила."])]),
    ("Маршрут через лес", [
        ("Глава 1. Станция", ["Поезд остановился у маленькой станции. Павел вышел на платформу и развернул карту. До озера было 8 километров.", "Дорога начиналась за старым складом. В траве блестела роса. Павел убрал карту в карман и пошёл к лесу."]),
        ("Глава 2. Озеро", ["К полудню деревья расступились. Между камнями лежала узкая полоса песка, а дальше начиналась вода.", "Павел сел на берегу. Он не спешил возвращаться: вечерний поезд приходил только после заката."])])]

title, chapters = stories[0]
xml = '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><genre>prose</genre><author><first-name>Тестовая</first-name><last-name>Редакция</last-name></author><book-title>' + title + '</book-title><lang>ru</lang></title-info></description><body>'
for heading, paragraphs in chapters:
    xml += '<section><title><p>' + escape(heading) + '</p></title>' + ''.join('<p>' + escape(p) + '</p>' for p in paragraphs) + '</section>'
xml += '</body><body name="notes"><section id="note1"><p>Это тестовое примечание, которое можно исключить из озвучки.</p></section></body></FictionBook>'
(OUT / 'night-keeper.fb2').write_text(xml, encoding='utf-8')

title, chapters = stories[1]
with zipfile.ZipFile(OUT / 'lighthouse.epub', 'w') as z:
    z.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
    z.writestr('META-INF/container.xml', '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    manifest, spine, nav = [], [], []
    for i, (heading, paragraphs) in enumerate(chapters):
        z.writestr(f'OPS/ch{i}.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" lang="ru"><head><title>' + escape(heading) + '</title></head><body><h1>' + escape(heading) + '</h1>' + ''.join('<p>' + escape(p) + '</p>' for p in paragraphs) + '</body></html>')
        manifest.append(f'<item id="c{i}" href="ch{i}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        nav.append(f'<li><a href="ch{i}.xhtml">{escape(heading)}</a></li>')
    z.writestr('OPS/nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Оглавление</title></head><body><nav epub:type="toc"><ol>' + ''.join(nav) + '</ol></nav></body></html>')
    z.writestr('OPS/book.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:cb800835-ff08-4234-8b41-0ac66f63bb22</dc:identifier><dc:title>' + title + '</dc:title><dc:creator>Тестовая редакция</dc:creator><dc:language>ru</dc:language><meta property="dcterms:modified">2026-10-28T00:00:00Z</meta></metadata><manifest>' + ''.join(manifest) + '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine>' + ''.join(spine) + '</spine></package>')

font_candidates = [Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
font = next((p for p in font_candidates if p.exists()), None)
if font is None:
    raise RuntimeError('Provide a Cyrillic TTF font to generate the PDF fixture')
pdfmetrics.registerFont(TTFont('Book', str(font)))
title, chapters = stories[2]
c = canvas.Canvas(str(OUT / 'forest-route.pdf'), pagesize=(595, 842), invariant=1)
c.setTitle(title)
c.setAuthor('Тестовая редакция')
for heading, paragraphs in chapters:
    c.setFont('Book', 20)
    c.drawString(56, 760, heading)
    y = 710
    c.setFont('Book', 12)
    for paragraph in paragraphs:
        for line in simpleSplit(paragraph, 'Book', 12, 480):
            c.drawString(56, y, line)
            y -= 20
        y -= 16
    c.showPage()
c.save()
print('Created:', ', '.join(p.name for p in OUT.iterdir()))
