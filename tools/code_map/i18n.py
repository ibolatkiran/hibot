"""Locale JSON loading + reverse index (translated text -> namespace/key)."""
import json
import os
import re
import unicodedata

from common import is_secret_file, read_text

PLACEHOLDER = re.compile(r'@\w+|\{\{[^}]*\}\}|\{[^}]*\}|%[sd]|\$\{[^}]*\}|<[^>]+>')


def normalize(text, lang='en'):
    """Lowercase, trim, replace placeholders (@Name, {{x}}, {0}, %s) by '*', collapse whitespace."""
    if not isinstance(text, str):
        text = str(text)
    t = unicodedata.normalize('NFKC', text)
    t = PLACEHOLDER.sub('*', t)
    if lang == 'tr':
        t = t.replace('İ', 'i').replace('I', 'ı')
    t = t.lower().replace('i̇', 'i')
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'(\*\s*)+', '*', t) if '*' in t else t
    return t.strip(' .:!?;,')


def load_locales(root, keep=('en', 'tr')):
    loc = os.path.join(root, 'public', 'locales')
    available, data = {}, {}
    if not os.path.isdir(loc):
        return {}, {}
    for lang in sorted(os.listdir(loc)):
        d = os.path.join(loc, lang)
        if not os.path.isdir(d):
            continue
        nss = [f for f in sorted(os.listdir(d)) if f.lower().endswith('.json') and not is_secret_file(f)]
        available[lang] = [os.path.splitext(f)[0] for f in nss]
        if lang in keep:
            data[lang] = {}
            for f in nss:
                try:
                    obj = json.loads(read_text(os.path.join(d, f)))
                except Exception:
                    continue
                if isinstance(obj, dict):
                    flat = {}
                    for k, v in obj.items():
                        flat[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                    data[lang][os.path.splitext(f)[0]] = flat
    return available, data


def reverse_index(data):
    out = {}
    for lang, nss in data.items():
        idx = {}
        for ns, kv in nss.items():
            for k, v in kv.items():
                n = normalize(v, lang)
                if not n:
                    continue
                idx.setdefault(n, []).append([ns, k])
        out[lang] = idx
    return out
