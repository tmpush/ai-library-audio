"""Deterministic speech spelling. Never modifies the imported book.

Russian prose conventions, not a general morphological parser. Unrecognised
identifiers/URLs are preserved for the pronunciation dictionary/editor.
"""
import datetime
import re
from decimal import Decimal
from num2words import num2words

REVISION = 'ru-prose-v1'
MONTHS = 'января февраля марта апреля мая июня июля августа сентября октября ноября декабря'.split()
NUMBER = r'\d{1,12}(?:[.,]\d{1,6})?'


def words(value, **kw):
    return num2words(value, lang='ru', **kw)


def noun(n, forms):
    n = abs(int(n))
    return forms[2] if 11 <= n % 100 <= 14 else forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def cardinal(token):
    token = token.replace('−', '-').replace(',', '.')
    sign = 'плюс ' if token.startswith('+') else ''
    token = token.lstrip('+')
    # Codes with leading zeroes are spoken digit by digit, without losing zeroes.
    if '.' not in token and len(token) > 1 and token.startswith('0'):
        return sign + ' '.join(words(int(d)) for d in token)
    return sign + words(Decimal(token) if '.' in token else int(token))


def normalize_numbers(text, language='ru'):
    if language not in {'ru', 'en', 'de', 'fr', 'es', 'it', 'pt'}:
        if re.search(r'\d', text):
            raise ValueError('Числа прописью для этого языка пока не поддерживаются. Отключите преобразование или исправьте числа вручную')
        return text
    if language != 'ru':
        return re.sub(r'(?<![\w/+:−-])(?<!\d[.,])\d{1,12}(?!\w|[.,/+:−-]\d)',
                      lambda m: num2words(int(m[0]), lang=language), text)

    saved = []
    def hold(value):
        saved.append(value)
        return '\ue000' + chr(0xE100 + len(saved) - 1) + '\ue001'
    # Protect control commands, URLs, version strings and alphanumeric IDs.
    text = re.sub(r'\[\[pause:\d+\]\]|https?://\S+|\b\w*[A-Za-z]\w*\d\w*(?:[.-]\w+)*', lambda m: hold(m[0]), text)

    def date(m):
        d, mo, y = map(int, m.groups())
        try: datetime.date(y, mo, d)
        except ValueError: return hold(m[0])
        return hold(f'{words(d, to="ordinal", gender="n")} {MONTHS[mo-1]} {words(y, to="ordinal", case="g")} года')
    text = re.sub(r'(?<![\w.])([0-3]?\d)\.([01]?\d)\.(\d{4})(?!\d)', date, text)

    def clock(m):
        h, minute = map(int, m.groups())
        if h > 23 or minute > 59: return hold(m[0])
        return hold(f'{words(h)} {noun(h, ("час", "часа", "часов"))} {words(minute, gender="f")} {noun(minute, ("минута", "минуты", "минут"))}')
    text = re.sub(r'(?<![\w:])(\d{1,2}):(\d{2})(?![\d:])', clock, text)
    text = re.sub(r'\b(в|во)\s+(\d{4})\s+(году|г\.)', lambda m: hold(f'{m[1]} {words(int(m[2]), to="ordinal", case="p")} году'), text, flags=re.I)
    text = re.sub(r'\b(\d{4})\s+года\b', lambda m: hold(words(int(m[1]), to='ordinal', case='g') + ' года'), text)
    text = re.sub(r'\b(\d{4})\s+год\b', lambda m: hold(words(int(m[1]), to='ordinal') + ' год'), text)
    text = re.sub(r'\b(глава|часть|книга)\s+(\d{1,3})(?!\w|[.,]\d)', lambda m: hold(m[1] + ' ' + words(int(m[2]), to='ordinal', gender='f')), text, flags=re.I)
    # Explicit ordinal suffixes carry the necessary grammatical information.
    suffixes = {'й': ('m','n'), 'ый': ('m','n'), 'ой': ('m','n'), 'я': ('f','n'), 'ая': ('f','n'),
                'е': ('n','n'), 'ое': ('n','n'), 'го': ('m','g'), 'му': ('m','d'), 'м': ('m','p'), 'ю': ('f','a')}
    text = re.sub(r'\b(\d{1,6})-(ый|ой|ая|ое|го|му|й|я|е|м|ю)\b',
                  lambda m: hold(words(int(m[1]), to='ordinal', gender=suffixes[m[2]][0], case=suffixes[m[2]][1])), text)
    # Russian thousands grouping, before units/decimals.
    text = re.sub(r'(?<!\w)\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)', lambda m: re.sub(r'\s', '', m[0]), text)

    def money(m):
        value = Decimal(m[1].replace(',', '.'))
        currency = {'₽':'RUB', 'руб.':'RUB', 'руб':'RUB', '€':'EUR', '$':'USD'}[m[2]]
        return hold(words(value, to='currency', currency=currency))
    text = re.sub(r'(?<![\w.,])(' + NUMBER + r')\s*(₽|€|\$|руб\.?)(?!\w)', money, text)

    def fraction(m):
        a, b = map(int, m.groups())
        if b <= 1: return hold(m[0])
        return hold(words(a, gender='f') + ' ' + words(b, to='ordinal', gender='f', case='n' if a == 1 else 'g', plural=a != 1))
    text = re.sub(r'(?<![\w/])(\d{1,6})/(\d{1,6})(?![\w/])', fraction, text)
    text = re.sub(r'(?<!\w)(' + NUMBER + r')\s*%', lambda m: hold(cardinal(m[1]) + ' ' + ('процента' if re.search(r'[.,]', m[1]) else noun(int(m[1]), ('процент','процента','процентов')))), text)
    units = {'км': ('километр','километра','километров'), 'кг': ('килограмм','килограмма','килограммов'),
             'см': ('сантиметр','сантиметра','сантиметров'), 'мм': ('миллиметр','миллиметра','миллиметров')}
    text = re.sub(r'(?<!\w)(' + NUMBER + r')\s*(км|кг|см|мм)\b', lambda m: hold(cardinal(m[1]) + ' ' + (units[m[2]][1] if re.search(r'[.,]',m[1]) else noun(int(m[1]),units[m[2]]))), text)
    text = re.sub(r'(?<![\w.,])(' + NUMBER + r')[–-](' + NUMBER + r')(?![\w.,]\d)', lambda m: hold(cardinal(m[1]) + ' — ' + cardinal(m[2])), text)
    # Do not partially corrupt unknown dotted sequences or identifiers.
    text = re.sub(r'(?<!\w)\d+(?:[.:/]\d+){2,}(?!\w)', lambda m: hold(m[0]), text)
    text = re.sub(r'(?<![\w/.:])(?<!\d,)[+−-]?' + NUMBER + r'(?!\w|[.,/:]\d)', lambda m: cardinal(m[0]), text)
    return re.sub('\ue000(.)\ue001', lambda m: saved[ord(m[1])-0xE100], text)
