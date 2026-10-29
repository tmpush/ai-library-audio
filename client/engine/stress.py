"""Russian stress hints. Automatic predictions never replace explicit user marks.

Qwen may ignore these hints; retain normal orthography in the stored book.
"""
import re
import threading

REVISION = 'ru-explicit-stress-v1'
VOWELS = 'аеёиоуыэюяАЕЁИОУЫЭЮЯ'


def speech_stress(text, language='ru'):
    if language != 'ru':
        return text
    # Canonical editor mark U+0301 and common +vowel input become the same hint.
    text = re.sub(r'\+([' + VOWELS + r'])', lambda m: m[1] + '\u0301', text)
    return re.sub(r'([' + VOWELS + r'])\u0301+', lambda m: m[1] + '\u02ca', text)


# This package includes its weights: inference never downloads anything.
AUTO_REVISION = 'ruaccent-predictor-1.2.0-v2'
_WORD = re.compile(r"[+а-яА-ЯёЁ\u0301\u02ca]+")
_LOCK = threading.RLock()
_MODEL = None


def _predict(text):
    global _MODEL
    import contextlib
    import sys
    with _LOCK:
        if _MODEL is None:
            try:
                from ruaccent import load_accentor
                # Keep stdout free for the desktop protocol; reserve CUDA for TTS.
                with contextlib.redirect_stdout(sys.stderr):
                    _MODEL = load_accentor(device='cpu')
            except (ImportError, FileNotFoundError) as exc:
                raise ValueError('Модель ударений не установлена. Отключите «Расставлять ударения» или установите полную версию программы.') from exc
        return _MODEL(text, format='synthesis')


def _plain(word):
    return word.replace('+', '').replace('\u0301', '').replace('\u02ca', '')


def automatic_stress(text, language='ru'):
    """Transfer only verified accent positions, never generated spelling/punctuation.

    Short, bounded context windows avoid the model's 256-character output limit.
    Explicit marks and ё win. Unknown/misaligned model output is left unchanged.
    """
    if language != 'ru':
        return text
    # Do not send URLs or application pause directives to the predictor.
    pieces = re.split(r'(https?://\S+|\[\[pause:\d+\]\])', text)
    for i in range(0, len(pieces), 2):
        original = pieces[i]
        words = list(_WORD.finditer(original))
        replacements = []
        start = 0
        while start < len(words):
            end = start + 1
            while end < len(words) and words[end].end() - words[start].start() <= 140:
                end += 1
            group = words[start:end]
            source = original[group[0].start():group[-1].end()]
            clean = re.sub(r'\s+', ' ', _plain(source))
            if len(clean) <= 140 and any(not any(c in w[0] for c in '+\u0301\u02caёЁ') and sum(c in VOWELS for c in w[0]) > 1 for w in group):
                predicted = list(_WORD.finditer(_predict(clean)))
                if len(predicted) == len(group) and all(_plain(a[0]) == _plain(b[0]) for a, b in zip(group, predicted)):
                    for old, new in zip(group, predicted):
                        if any(c in old[0] for c in '+\u0301\u02caёЁ'):
                            continue
                        candidate = re.sub(r'\+([' + VOWELS + r'])', lambda m: m[1] + '\u0301', new[0])
                        if candidate.count('\u0301') == 1 and _plain(candidate) == old[0]:
                            replacements.append((old.start(), old.end(), candidate))
            start = end
        for a, b, replacement in reversed(replacements):
            original = original[:a] + replacement + original[b:]
        pieces[i] = original
    return ''.join(pieces)
