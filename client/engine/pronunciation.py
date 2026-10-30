"""Conservative Russian speech spelling; never changes the imported text."""
import re

REVISION = 'ru-symbol-context-v1'


def normalize_symbols(text, language='ru'):
    if language != 'ru':
        return text
    # Cyrillic Х is often used as a typographical stand-in for the variable X.
    # Only explicit variable contexts are safe: X can also mean ten or a name.
    names = {'X': 'икс', 'x': 'икс', 'Х': 'икс', 'х': 'икс',
             'Y': 'игрек', 'y': 'игрек', 'Z': 'зет', 'z': 'зет'}
    cues = r'что|противоположна|противоположно|переменная|переменную|переменной|переменные|переменных|значение|значения|обозначение|буква|букву'
    return re.sub(r'(?i:\b(' + cues + r'))(\s+[«„“"\(]?)([XxХхYyZz])(?!\w|[-.]\w)',
                  lambda m: m[1] + m[2] + names[m[3]], text)
