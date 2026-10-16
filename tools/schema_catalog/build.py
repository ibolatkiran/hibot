#!/usr/bin/env python3
"""HiBot DB schema catalog generator.

Usage (from d:\\repos\\HiBot):
    set PYTHONIOENCODING=utf-8
    python tools/schema_catalog/build.py [--eu PATH] [--apac PATH] [--out PATH]

Reads two SSDT SQL projects (read-only) and writes knowledge/schema/*.
Python 3 standard library only.  Deterministic: same inputs -> same outputs.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sqlcat as S  # noqa: E402
import classify as C  # noqa: E402
import reports as R  # noqa: E402

DEF_EU = r'd:\repos\sirius-eu\gfsm-europe'
DEF_APAC = r'd:\repos\gfsm-apac\ahsiriusdbsrv.database.windows.net'
DEF_OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'knowledge', 'schema')

FOLDER_TYPES = {
    'Tables': 'table', 'StoredProcedures': 'procedure', 'Views': 'view', 'Functions': 'function',
    'UserDefinedTypes': 'udt', 'Synonyms': 'synonym', 'Sequences': 'sequence',
}
SKIP_FOLDERS = {'Storage'}


def fwd(p):
    return p.replace('\\', '/')


def scan(root):
    """Return (files, principals, skipped) ; files = {key: (type, path)}"""
    files, principals, skipped = {}, [], Counter()
    for schema in sorted(os.listdir(root)):
        sdir = os.path.join(root, schema)
        if not os.path.isdir(sdir) or schema in ('bin', 'obj', 'docs', '.git', '.vs'):
            continue
        if schema == 'Security':  # project-level Security folder: names only, never contents
            for f in sorted(os.listdir(sdir)):
                if f.lower().endswith('.sql'):
                    principals.append((schema, os.path.splitext(f)[0]))
            continue
        for folder in sorted(os.listdir(sdir)):
            fdir = os.path.join(sdir, folder)
            if not os.path.isdir(fdir):
                continue
            if folder == 'Security':
                for f in sorted(os.listdir(fdir)):
                    if f.lower().endswith('.sql'):
                        principals.append((schema, os.path.splitext(f)[0]))
                continue
            if folder in SKIP_FOLDERS:
                continue
            typ = FOLDER_TYPES.get(folder)
            if not typ:
                skipped[schema + '/' + folder] += 1
                continue
            for f in sorted(os.listdir(fdir)):
                if f.lower().endswith('.sql'):
                    key = '%s.%s' % (schema, os.path.splitext(f)[0])
                    files[key] = (typ, os.path.join(fdir, f))
    return files, principals, skipped


def parse_table(batches, key, idx):
    rec = {'columns': [], 'primary_key': [], 'indexes': [], 'foreign_keys': [], 'triggers': []}
    col_desc, table_desc = {}, None
    defaults = {}
    disabled = set()
    full_clean = '\n'.join(c for c, _ in batches)
    for m in re.finditer(r'\bDISABLE\s+TRIGGER\s+((?:%s\s*\.\s*)?%s)' % (S.IDENT, S.IDENT), full_clean, re.I):
        disabled.add(S._name_parts(m.group(1))[-1])
    for clean, nc in batches:
        head = clean.lstrip()[:40].upper()
        if re.match(r'CREATE\s+TABLE\b', head):
            t = S.parse_create_table(clean, key)
            if t:
                rec['columns'] = t['columns']
                rec['primary_key'] = t['primary_key']
                rec['indexes'].extend(t['indexes'])
                rec['foreign_keys'].extend(t['foreign_keys'])
        elif re.match(r'ALTER\s+TABLE\b', head):
            m = re.search(r'ALTER\s+TABLE\s+((?:%s\s*\.\s*)?%s)\s+(?:WITH\s+\w+\s+)?ADD\s+(.*)$' % (S.IDENT, S.IDENT), clean, re.I | re.S)
            if m:
                for item in S.split_top(m.group(2)):
                    c = S.parse_constraint(item)
                    if not c:
                        continue
                    if c['kind'] == 'pk':
                        rec['primary_key'] = c['columns']
                    elif c['kind'] == 'unique':
                        rec['indexes'].append({'name': c['name'], 'columns': c['columns'], 'unique': True})
                    elif c['kind'] == 'fk':
                        rec['foreign_keys'].append(c)
                    elif c['kind'] == 'default':
                        defaults[c['column']] = c['value']
        elif re.match(r'CREATE\s+(UNIQUE\s+|CLUSTERED\s+|NONCLUSTERED\s+|COLUMNSTORE\s+)*INDEX\b', head):
            r = S.parse_index(clean)
            if r:
                rec['indexes'].append(r[0])
        elif re.match(r'CREATE\s+(OR\s+ALTER\s+)?TRIGGER\b', head):
            r = S.parse_trigger(clean, nc, idx)
            if r:
                t = r[0]
                t['disabled'] = t['name'] in disabled
                rec['triggers'].append(t)
        elif 'sp_addextendedproperty' in head.lower() or 'SP_ADDEXTENDEDPROPERTY' in clean[:60].upper():
            for m in re.finditer(r"@value\s*=\s*N?'((?:[^']|'')*)'(.*)", nc, re.I | re.S):
                tail = m.group(2)
                l2 = re.search(r"@level2name\s*=\s*N?'([^']*)'", tail, re.I)
                l1t = re.search(r"@level1type\s*=\s*N?'([^']*)'", tail, re.I)
                if not l1t or l1t.group(1).upper() != 'TABLE':
                    continue
                txt = m.group(1).replace("''", "'")
                if l2:
                    col_desc[l2.group(1)] = txt
                else:
                    table_desc = txt
    for c in rec['columns']:
        if c.get('default') is None and c['name'] in defaults:
            c['default'] = defaults[c['name']]
        if c['name'] in col_desc:
            c['description'] = col_desc[c['name']][:300]
    if table_desc:
        rec['description'] = table_desc[:500]
    rec['has_TenantId'] = any(c['name'].lower() == 'tenantid' for c in rec['columns'])
    rec['trigger_calls'] = sorted({o for t in rec['triggers'] for o in t['called_objects']})
    return rec


def parse_file(key, typ, path, idx):
    text = S.read_sql(path)
    nocomment, clean = S.strip_text(text)
    info = {'line_count': text.count('\n') + (0 if text.endswith('\n') or not text else 1)}
    batches = S.split_batches(clean, nocomment)
    nh = S.norm_hash(clean)
    rec = {}
    if typ == 'table':
        rec = parse_table(batches, key, idx)
    elif typ in ('procedure', 'function', 'view'):
        r = None
        for c, n in batches:
            if S.ROUTINE_RE.search(c):
                r = S.parse_routine(c, n, idx)
                break
        if r:
            r.pop('name_in_file', None)
            r.pop('type', None)
            rec = r
        else:
            rec = {'parse_warning': 'no CREATE found'}
    elif typ == 'synonym':
        m = re.search(r'FOR\s+(.*?)\s*;?\s*$', clean.strip(), re.I | re.S)
        rec = {'target': re.sub(r'\s+', '', m.group(1)) if m else None}
    elif typ == 'udt':
        m = re.search(r'AS\s+TABLE\s*\(', clean, re.I)
        if m:
            e = S.balanced(clean, m.end() - 1)
            cols = []
            for item in S.split_top(clean[m.end():e - 1]):
                if item.strip() and not S.CONSTR_START.match(item):
                    c = S.parse_column(item)
                    if c:
                        cols.append({'name': c['name'], 'sql_type': c['sql_type']})
            rec = {'columns': cols}
    return rec, info, nh


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--eu', default=DEF_EU)
    ap.add_argument('--apac', default=DEF_APAC)
    ap.add_argument('--out', default=DEF_OUT)
    args = ap.parse_args()
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    regions = {'EU': args.eu, 'APAC': args.apac}
    scanned = {}
    for reg, root in regions.items():
        if not os.path.isdir(root):
            print('WARNING: %s root not found: %s' % (reg, root))
            scanned[reg] = ({}, [], Counter())
        else:
            scanned[reg] = scan(root)
            print('%s: %d object files' % (reg, len(scanned[reg][0])))

    # canonical key casing: EU first, then APAC; case-insensitive merge
    canon = {}
    for reg in ('EU', 'APAC'):
        for k in scanned[reg][0]:
            canon.setdefault(k.lower(), k)
    idx = S.Index(canon.values())

    objects, hashes = {}, {}
    for reg in ('EU', 'APAC'):
        for k0, (typ, path) in scanned[reg][0].items():
            key = canon[k0.lower()]
            rec, info, nh = parse_file(key, typ, path, idx)
            hashes[(key, reg)] = nh
            o = objects.setdefault(key, {'type': typ, 'regions': [], 'source_files': {}, '_parsed': {}})
            o['regions'].append(reg)
            o['source_files'][reg] = fwd(path)
            rec['line_count'] = info['line_count']
            o['_parsed'][reg] = rec
            if o['type'] != typ:
                o.setdefault('type_conflict', []).append({reg: typ})

    # merge: EU is the base (superset); APAC-only objects use APAC parse
    for key, o in objects.items():
        parsed = o.pop('_parsed')
        base_reg = 'EU' if 'EU' in parsed else 'APAC'
        rec = dict(parsed[base_reg])
        if len(parsed) == 2:
            same = hashes[(key, 'EU')] == hashes[(key, 'APAC')]
            o['region_identical'] = same
            if not same:
                a, b = parsed['EU'], parsed['APAC']
                if o['type'] == 'table':
                    ac = {c['name'] for c in a['columns']}
                    bc = {c['name'] for c in b['columns']}
                    if ac - bc:
                        o['eu_only_columns'] = sorted(ac - bc)
                    if bc - ac:
                        o['apac_only_columns'] = sorted(bc - ac)
                elif o['type'] in ('procedure', 'function', 'view'):
                    ar, br = set(a.get('referenced_objects', [])), set(b.get('referenced_objects', []))
                    if ar - br:
                        o['eu_only_refs'] = sorted(ar - br)
                    if br - ar:
                        o['apac_only_refs'] = sorted(br - ar)
                    o['apac_line_count'] = b.get('line_count')
        o.update(rec)
        o['is_backup_or_copy'] = C.is_backup_or_copy(key)

    # reverse indexes
    refd, writers = defaultdict(set), defaultdict(set)
    for key, o in objects.items():
        if o['type'] in ('procedure', 'function', 'view'):
            for r in o.get('referenced_objects', []):
                refd[r].add(key)
            for w in o.get('written_tables', []):
                writers[w].add(key)
        elif o['type'] == 'table':
            for t in o.get('triggers', []):
                for r in t['called_objects']:
                    refd[r].add(key + ' (trigger %s)' % t['name'])
                for w in t['written_tables']:
                    writers[w].add(key + ' (trigger %s)' % t['name'])
    for key, o in objects.items():
        if key in refd:
            o['referenced_by'] = sorted(refd[key])
        if o['type'] == 'table' and key in writers:
            o['written_by'] = sorted(writers[key])

    objs_sorted = {k: objects[k] for k in sorted(objects, key=str.lower)}
    with open(os.path.join(args.out, 'objects.json'), 'w', encoding='utf-8') as fh:
        json.dump(objs_sorted, fh, ensure_ascii=False, indent=1, sort_keys=False)

    # domains
    domains = C.build_domains(objs_sorted)
    with open(os.path.join(args.out, 'domains.json'), 'w', encoding='utf-8') as fh:
        json.dump(domains, fh, ensure_ascii=False, indent=1)

    # lookups
    lookups = R.build_lookups(objs_sorted)
    with open(os.path.join(args.out, 'lookups.json'), 'w', encoding='utf-8') as fh:
        json.dump(lookups, fh, ensure_ascii=False, indent=1)

    # security principal names only (never contents)
    princ = {}
    for reg in ('EU', 'APAC'):
        names = sorted({n for _, n in scanned[reg][1] if '@' not in n}, key=str.lower)
        email_users = sum(1 for _, n in scanned[reg][1] if '@' in n)
        princ[reg] = {'names': names, 'email_named_user_count': email_users}
    with open(os.path.join(args.out, 'security_principals.json'), 'w', encoding='utf-8') as fh:
        json.dump(princ, fh, ensure_ascii=False, indent=1)

    missing = R.write_diagnostic_md(os.path.join(args.out, 'diagnostic_tables.md'), objs_sorted, domains)
    stats = R.compute_stats(objs_sorted, domains, scanned, missing)
    R.write_readme(os.path.join(args.out, 'README.md'), stats, args)
    print(json.dumps(stats['summary'], indent=1))
    print('missing diagnostic tables:', missing)
    print('done in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
