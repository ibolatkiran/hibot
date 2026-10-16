#!/usr/bin/env python3
"""HiBot code traceability map generator.

Usage (from d:\\repos\\HiBot):
    set PYTHONIOENCODING=utf-8
    python tools/code_map/build.py [--portal PATH] [--crm PATH] [--partner PATH] [--technician PATH]
                                   [--api PATH] [--out PATH]

Reads the Sirius repos READ-ONLY; writes only into knowledge/codemap/. Deterministic (no timestamps).
Secret-bearing files (appsettings*, .env*, translate.js, web.config, *.pfx ...) are never opened.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frontend  # noqa: E402
import backend as be  # noqa: E402
import i18n  # noqa: E402
import tracejoin as tr  # noqa: E402

APPS = [('portal', 'portal', 'Portal', r'd:\repos\SiriusHeadOffice'),
        ('crm', 'crm', '', r'd:\repos\SiriusCallCenter'),
        ('partner', 'partner', '', r'd:\repos\SiriusServiceShopCallCenter'),
        ('technician', 'technician', '', r'd:\repos\SiriusTechnician')]


def dump(obj, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    for aid, _, _, default in APPS:
        ap.add_argument('--' + aid, default=default, help='path to %s repo' % aid)
    ap.add_argument('--api', default=r'd:\repos\SiriusApi')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.dirname(HERE)), 'knowledge', 'codemap'))
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)
    sizes = {}
    results, fe_stats, i18n_stats = {}, {}, {}

    # ---------------- frontends
    for aid, _, prefix, _d in APPS:
        root = getattr(args, aid)
        if not os.path.isdir(root):
            print('skip (missing):', aid, root)
            continue
        t0 = time.time()
        available, data = i18n.load_locales(root)
        en_ns = data.get('en', {})
        fe, res = frontend.build_app(aid, root, prefix, en_ns)
        res['stats'] = fe.stats
        results[aid] = res
        fe_stats[aid] = fe.stats
        sizes['frontend_%s.json' % aid] = dump(res, os.path.join(out, 'frontend_%s.json' % aid))
        # i18n file
        usage = {}
        for sc in res['screens']:
            for k in sc['i18n']['keys']:
                if k.get('dynamic') or not k.get('ns'):
                    continue
                usage.setdefault('%s::%s' % (k['ns'], k['key']), set()).add(sc['file'])
        key_usage = {k: sorted(v) for k, v in sorted(usage.items())}
        known = {'%s::%s' % (ns, k) for ns, kv in en_ns.items() for k in kv}
        used_keys = set(key_usage)
        doc = {'app': aid, 'ns_prefix': prefix, 'languages_available': {l: len(n) for l, n in available.items()},
               'languages_included': sorted(data.keys()), 'namespaces': available.get('en', []),
               'translations': data, 'reverse_index': i18n.reverse_index(data),
               'normalization': "lowercase (tr-aware), trimmed, @Placeholder / {{x}} / {0} / %s replaced by '*', whitespace collapsed, edge punctuation stripped",
               'key_usage': key_usage}
        sizes['i18n_%s.json' % aid] = dump(doc, os.path.join(out, 'i18n_%s.json' % aid))
        i18n_stats[aid] = {'languages': len(available), 'namespaces_en': len(available.get('en', [])),
                           'keys_en': sum(len(v) for v in en_ns.values()),
                           'keys_tr': sum(len(v) for v in data.get('tr', {}).values()),
                           'used_keys_resolved': len(used_keys), 'used_keys_found_in_en': len(used_keys & known)}
        print('%-10s frontend done in %.1fs %s' % (aid, time.time() - t0, fe.stats))

    # ---------------- backend
    t0 = time.time()
    b = be.Backend(args.api)
    b.load()
    b.analyse()
    ctrl_classes = {c.name for cs in b.classes.values() for c in cs if c.name.endswith('Controller')}
    methods = [m for m in b.methods if m['class'] not in ctrl_classes and not m.get('ctor')
               and (m['db_objects'] or m['external_calls'] or m['error_messages'] or m['calls'])]
    methods.sort(key=lambda m: (m['file'], m['line_start']))
    endpoints = [{k: v for k, v in e.items() if not k.startswith('_')} for e in b.endpoints]
    endpoints.sort(key=lambda e: (e['file'], e['line']))
    interfaces = {}
    for iname, impls in b.iface_impl.items():
        if iname in b.classes and any(c.kind == 'interface' for c in b.classes[iname]):
            interfaces[iname] = sorted({c.name for c in impls if c.kind == 'class'})
    bk = {'meta': {'source': 'SiriusApi', 'files_parsed': len(b.files), 'methods_total': len(b.methods),
                   'methods_listed': len(methods),
                   'methods_listing_rule': 'non-controller, non-ctor methods having at least one of calls/db_objects/external_calls/error_messages'},
          'endpoints': endpoints, 'methods': methods,
          'interface_implementations': dict(sorted(interfaces.items()))}
    sizes['backend.json'] = dump(bk, os.path.join(out, 'backend.json'))
    print('backend done in %.1fs: %d endpoints, %d methods listed (%d total)' % (time.time() - t0, len(endpoints), len(methods), len(b.methods)))

    # ---------------- trace
    trace = tr.build_trace(results, b)
    sizes['trace_index.json'] = dump(trace, os.path.join(out, 'trace_index.json'))
    print(json.dumps(trace['stats'], indent=1))

    # ---------------- README
    write_readme(out, results, fe_stats, i18n_stats, bk, trace, sizes, b)
    print('done; outputs in', out)


def write_readme(out, results, fe_stats, i18n_stats, bk, trace, sizes, b):
    L = []
    A = L.append
    A('# HiBot code traceability map\n')
    A('Generated by `python tools/code_map/build.py` (Python 3 stdlib only, deterministic, read-only on the Sirius repos).')
    A('Chain modelled: **screen -> API call -> backend endpoint (controller action) -> manager method(s) -> DB objects (SP / function / table / inline SQL)**.\n')
    A('## Regenerate\n')
    A('```')
    A('cd d:\\repos\\HiBot')
    A('set PYTHONIOENCODING=utf-8')
    A('python tools/code_map/build.py')
    A('# optional: --portal --crm --partner --technician --api <path>  --out <dir>')
    A('```')
    A('Runtime is roughly 15-30 seconds. Re-running with unchanged sources produces byte-identical files.\n')
    A('Security: files matching `appsettings*`, `.env*`, `.env-cmdrc.json`, `translate.js`, `web.config`, `*.pfx`, `*.pem`, `*.key`, `secrets*.json` are excluded at the file-walk level and are never opened. Dirs `node_modules build dist bin obj .git git` are skipped. No connection strings or keys are present in the outputs (only SQL object names, route templates, literal UI/error texts).\n')
    A('## Files\n')
    A('| file | size | content |')
    A('|---|---|---|')
    desc = {'backend.json': 'endpoints + analysed methods (db objects, external calls, error messages)',
            'trace_index.json': 'joined frontend->backend->db graph, unmatched / unused lists'}
    for f in sorted(sizes):
        d = desc.get(f) or ('per-app routes, screens (api calls, i18n usage), call sites' if f.startswith('frontend_') else 'locale texts (en, tr), reverse index text->(ns,key), key->screens usage')
        A('| `%s` | %.1f MB | %s |' % (f, sizes[f] / 1e6, d))
    A('')
    A('Apps: `portal` = SiriusHeadOffice, `crm` = SiriusCallCenter, `partner` = SiriusServiceShopCallCenter, `technician` = SiriusTechnician.\n')
    A('## Formats\n')
    A('### frontend_<app>.json')
    A('- `axios_instances`: per `src/utils` axios instance, the URL prefix that is prepended (base URL suffix + interceptor prefix such as `/Technician`), and whether it points at a non-Sirius service (`external`).')
    A('- `routes`: `{path, component, file, layout, exact, line, closure_files, api_endpoints}` parsed from `src/routes.js` (nested `routes:` arrays and `lazy(() => import())` handled). `api_endpoints` = every endpoint reachable from the route component through the import closure of views/components/layouts.')
    A('- `screens`: one entry per file in `src/views` (+ components that call APIs / use i18n): `routes` (route(s) whose component is this file), `reachable_from_routes` (routes whose import closure contains it), `i18n {namespaces, keys[{key,ns,line}], dynamic_t_calls}`, `api_calls [{method, url_template, resolved_endpoint, axios_instance, via, file, line, chain}]`.')
    A('  - `via`: `raw` (axios call written in the view/component), `slice` (createAsyncThunk in store/slices), `saga` (view -> action creator -> action type -> saga handler -> api fn), `api` (store/api function without saga).')
    A('  - `chain`: the hops, e.g. `view -> actions::taskHistoryRequest -> action-type ...::GET_TASK_HISTORY_REQUEST -> sagas::getArtemisTaskHistory -> api::getArtemisTaskHistory`.')
    A('- `api_call_sites`: every axios call site in the app (even if no screen reaches it), with `used_by_screens`.')
    A('- `resolved_endpoint` is `Controller/Action` (instance prefix applied, `api/` stripped, query string and params dropped); `path_norm` keeps param positions as `{}`.\n')
    A('### i18n_<app>.json')
    A('`translations[lang][namespace][key] = text` for `en` and `tr` (other languages present in `public/locales` are only listed in `languages_available` with namespace counts); `reverse_index[lang][normalized_text] = [[ns,key],...]`; `key_usage["ns::key"] = [screen files]`. To trace a user-quoted message: normalize it (lowercase, trim, replace dynamic parts with `*`), look it up in `reverse_index`, then use `key_usage` to find the screen(s) and `frontend_<app>.json` for routes.\n')
    A('### backend.json')
    A('- `endpoints`: `{http_method, route, controller, action, file, line (method signature), attribute_line, line_end, calls[Class.method], db_objects, external_calls, error_messages}` (the last three are what the controller action itself does).')
    A('- `methods`: `{class, method, file, line_start, line_end, db_objects[{name, kind: sp|function|table|inline_sql, via: dapper|ef|ado|inline}], calls, external_calls[{kind,target}], error_messages[]}`; overloads are separate rows.')
    A('- `interface_implementations`: interface -> implementing classes (used to resolve `_manager.X()` to `TechnicianManager.X`).\n')
    A('### trace_index.json')
    A('`frontend_calls` (one row per unique app+method+path: callers, matched backend endpoint, match quality, manager methods reachable to depth 4, db objects, external calls), `by_screen`, `by_endpoint` (reverse: backend endpoint -> screens per app), `endpoint_downstream` (full downstream incl. error messages per endpoint), `unmatched_frontend_calls`, `unused_backend_endpoints`, `stats`.')
    A('Match quality: `exact` (verb matches and path segments are compatible: literal segments equal, `{param}` matches anything, optional trailing params allowed), `verb-mismatch` (path compatible but HTTP verb differs), `controller+action` (controller and action name match but the number/shape of path segments differs; probably a query/param mismatch), `verb-mismatch+controller+action`.\n')
    A('## Statistics\n')
    A('| app | files | routes (with file) | screens (views) | api call sites | reachable from a screen | sites external/unresolved |')
    A('|---|---|---|---|---|---|---|')
    for a, s in fe_stats.items():
        A('| %s | %d | %d (%d) | %d (%d) | %d | %d | %d |' % (a, s['files_parsed'], s['routes'], s['routes_with_file'], s['screens'], s['views'], s['api_call_sites'], s['api_call_sites_reachable_from_screen'], s['external_or_unresolved_sites']))
    A('')
    A('| app | unique calls | matched | match rate | exact verb+shape | site match rate (internal sites) |')
    A('|---|---|---|---|---|---|')
    for a, s in trace['stats']['per_app'].items():
        A('| %s | %d | %d | %.1f%% | %.1f%% | %.1f%% |' % (a, s['unique_calls'], s['unique_calls_matched'], 100 * s['unique_call_match_rate'], 100 * s['unique_call_exact_rate'], 100 * s['site_match_rate_internal']))
    o = trace['stats']['overall']
    A('')
    A('Overall: %d unique frontend calls, %d matched (**%.1f%%**); %d internal call sites, %d matched (%.1f%%).' % (o['unique_calls'], o['unique_calls_matched'], 100 * o['unique_call_match_rate'], o['internal_call_sites'], o['internal_call_sites_matched'], 100 * o['site_match_rate']))
    A('Backend: %d endpoints in %d controller classes, %d used by at least one frontend call, %d unused (see `unused_backend_endpoints`; many are mobile/webhook/integration endpoints called by other clients).' % (o['backend_endpoints'], len({e['controller'] for e in bk['endpoints']}), o['backend_endpoints_used_by_frontend'], o['backend_endpoints_unused']))
    A('Backend methods analysed: %d (listed in backend.json: %d). Methods with db_objects: %d; with external calls: %d; with literal error messages: %d.' % (bk['meta']['methods_total'], bk['meta']['methods_listed'], sum(1 for m in bk['methods'] if m['db_objects']), sum(1 for m in bk['methods'] if m['external_calls']), sum(1 for m in bk['methods'] if m['error_messages'])))
    A('')
    A('| app | locale langs | en keys | tr keys | keys used by screens (literal) | of which found in en |')
    A('|---|---|---|---|---|---|')
    for a, s in i18n_stats.items():
        A('| %s | %d | %d | %d | %d | %d |' % (a, s['languages'], s['keys_en'], s['keys_tr'], s['used_keys_resolved'], s['used_keys_found_in_en']))
    A('')
    A('Unmatched frontend unique calls: %d (list in `trace_index.json -> unmatched_frontend_calls`). Sample:' % trace['unmatched_frontend_calls']['count'])
    for u in trace['unmatched_frontend_calls']['items'][:12]:
        A('- `%s` %s %s  (%s)' % (u['app'], u['method'], u['endpoint'], u['callers'][0] if u['callers'] else ''))
    A('')
    # worked example
    A('## Worked example: "Work order History" tab\n')
    ex = [g for g in trace['frontend_calls'] if 'GetSiriusWorkOrderHistory' in (g.get('frontend_endpoint') or '') or 'GetSiriusWorkOrderHistory' in (g.get('path_template') or '')]
    for g in ex:
        A('- **%s** `%s %s` -> callers: %s' % (g['app'], g['method'], g['path_template'], '; '.join('%s:%d (%s)' % (c['file'], c['line'], ', '.join(c['screens'][:3])) for c in g['callers'])))
        if g.get('backend'):
            bkd = g['backend']
            A('  - backend: `%s %s` -> `%s:%d` -> `%s`' % (bkd['http_method'], bkd['route'], bkd['file'], bkd['line'], ', '.join(g['backend_calls'])))
            A('  - downstream methods: %s; db objects: %s; match=%s' % (', '.join(g['manager_methods']), ', '.join(g['db_objects']), g['match']))
    for app in ('technician', 'portal'):
        if app not in results:
            continue
        for sc in results[app]['screens']:
            for c in sc['api_calls']:
                if 'GetSiriusWorkOrderHistory' in c['url_template'] and sc['file'].endswith(('History.js', 'History/index.js')):
                    A('\nChain in `%s` (%s) for `%s` (route(s): %s):' % (sc['file'], app, c['url_template'], ', '.join(sc['routes'] or sc['reachable_from_routes'][:3]) or 'n/a'))
                    A('```')
                    A(' -> '.join(c['chain']))
                    A('   => %s %s  [%s:%d]' % (c['method'], c['resolved_endpoint'], c['file'], c['line']))
                    A('```')
    A('')
    notes = os.path.join(HERE, 'spotcheck_notes.md')
    if os.path.exists(notes):
        with open(notes, encoding='utf-8') as f:
            A(f.read().rstrip() + '\n')
    A('## Limitations\n')
    for t in [
        'Static, regex + bracket-matching analysis; no real JS/C# AST. JSX text apostrophes and regex literals can occasionally confuse the JS masker; C# raw strings / deeply nested interpolation are approximated.',
        'Frontend url resolution handles string, template and `+` concatenation, and file-level `const` strings. URLs held in variables built elsewhere, or passed through wrapper functions with a dynamic path, are listed as `unresolved` and excluded from the match-rate denominator.',
        'Calls to non-Sirius services (`customAxios`, `axiosNotification`, absolute http URLs) are flagged `external` and not matched.',
        'Saga/action chain resolution relies on `take*(Types.X, handler)` and `type: Types.X` patterns; sagas using string literal types, `takeEvery` with arrays, or dynamic dispatch are missed. Default-exported API objects are narrowed by member name when possible, otherwise the whole object is linked (can over-attribute).',
        'Screens: a call is attributed to a view if the view (not a nested component) references the thunk/action/api function. `routes` / `reachable_from_routes` / route `api_endpoints` use the import closure, so a route lists everything any nested component could call (superset).',
        'Matching is by controller + action name (case-insensitive); overloaded routes with the same action but different verbs are disambiguated by verb then path shape. A match means the URL exists, not that the runtime request is accepted (auth filters, tenant routing are not modelled).',
        'Backend call resolution: `_field.Method()` is resolved through constructor/field types and interface->class maps; calls via dynamic types, `var x = factory.Create()`, static helpers on unknown classes, delegates and DI of generic base types are not followed. Overloads of the same name are merged when following downstream.',
        'DB objects come from literals (direct or assigned to a local variable), Dapper/ADO/EF call arguments and inline SQL text. SQL built by concatenation across statements, stored in constants/resources, or object names inside dynamic SQL are only partially captured; temp tables, variables and `sys.*` are ignored. SP bodies are not in the repo, so SP -> table dependencies are not covered.',
        'Error messages are literals assigned to ErrorDescription/ErrorMessage/Message/Description/Error or passed to exception constructors; messages composed at runtime or loaded from resources are not captured.',
        'External calls are heuristic (HttpClient verbs with literal URLs, `*Client`/Sap/Soap/MailOrbit style types, `new XxxClient()`).',
        'Only en and tr translation texts are embedded; other languages are listed (name + namespace count).',
    ]:
        A('- ' + t)
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
