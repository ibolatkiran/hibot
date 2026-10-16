"""Join frontend api calls to backend endpoints and downstream manager/db graph."""
import re
from collections import defaultdict


def norm_route(route):
    segs = [s for s in route.strip('/').split('/') if s]
    if segs and segs[0].lower() == 'api':
        segs = segs[1:]
    return [(('{}?' if s.rstrip('}').endswith('?') or '?}' in s else '{}') if s.startswith('{') else s.lower()) for s in segs]


def compat(fs, bs):
    """frontend segments vs backend route segments ('{}' = param, '{}?' = optional param)."""
    req = sum(1 for x in bs if x != '{}?')
    if not (req <= len(fs) <= len(bs)):
        return False
    for f, b in zip(fs, bs):
        if b in ('{}', '{}?') or f == '{}' or f == b:
            continue
        return False
    return True


def lit_compat(fs, bs):
    """weaker test: no literal segment of the backend route contradicts the frontend path."""
    for i, b in enumerate(bs):
        if b in ('{}', '{}?'):
            continue
        if i >= len(fs) or (fs[i] != '{}' and fs[i] != b):
            return False
    return True


def compact_db(objs):
    return [f"{o['kind']}:{o['name']}" for o in objs]


def build_trace(apps, backend):
    """apps: {app_id: frontend_result}; backend: Backend instance (analysed)."""
    eps_by_key = defaultdict(list)
    for e in backend.endpoints:
        segs = norm_route(e['route'])
        e['_segs'] = segs
        if len(segs) >= 2:
            eps_by_key[(segs[0], segs[1])].append(e)
        elif segs:
            eps_by_key[(segs[0], '')].append(e)

    def ekey(e):
        return f"{e['http_method']} {e['route']}"

    downstream_cache = {}

    def down(e):
        k = ekey(e)
        if k not in downstream_cache:
            direct = {'db_objects': e['db_objects'], 'external_calls': e['external_calls']}
            d = backend.downstream(e['calls'])
            for o in e['db_objects']:
                d['db_objects'].insert(0, dict(o, via_method=f"{e['controller']}Controller.{e['action']}"))
            downstream_cache[k] = d
        return downstream_cache[k]

    used = set()
    groups_out = []
    unmatched = []
    stats = {'per_app': {}}
    by_screen = {}
    by_endpoint = defaultdict(lambda: defaultdict(set))

    for app, fe in apps.items():
        groups = {}
        for s in fe['api_call_sites']:
            if s.get('external') or s.get('unresolved'):
                continue
            ep = s.get('resolved_endpoint')
            if not ep:
                continue
            gk = (s['method'], s.get('path_norm') or ep)
            g = groups.setdefault(gk, {'app': app, 'method': s['method'], 'frontend_endpoint': ep,
                                       'path_template': s.get('path_norm'), 'callers': []})
            g['callers'].append({'file': s['file'], 'line': s['line'], 'decl': s.get('decl'),
                                 'axios_instance': s['axios_instance'], 'screens': s['used_by_screens']})
        n_sites = sum(1 for s in fe['api_call_sites'])
        n_ext = sum(1 for s in fe['api_call_sites'] if s.get('external') or s.get('unresolved') or not s.get('resolved_endpoint'))
        matched_g = exact_g = 0
        matched_sites = 0
        for gk, g in groups.items():
            segs = norm_route(g['path_template'] or g['frontend_endpoint'])
            cands = eps_by_key.get((segs[0], segs[1] if len(segs) > 1 else ''), []) if segs else []
            best, quality = None, None
            if cands:
                same_m = [c for c in cands if c['http_method'] == g['method']]
                for pool, q in ((same_m, 'exact'), (cands, 'verb-mismatch')):
                    ex = [c for c in pool if compat(segs, c['_segs'])]
                    if ex:
                        best, quality = ex[0], q
                        break
                if not best:
                    pool = [c for c in (same_m or cands) if lit_compat(segs, c['_segs'])]
                    if not pool:
                        pool = [c for c in cands if lit_compat(segs, c['_segs'])]
                        same_m = []
                    if pool:
                        best, quality = pool[0], ('controller+action' if same_m else 'verb-mismatch+controller+action')
            if best:
                matched_g += 1
                matched_sites += len(g['callers'])
                if quality == 'exact':
                    exact_g += 1
                used.add(ekey(best))
                d = down(best)
                g['match'] = quality
                g['backend'] = {'http_method': best['http_method'], 'route': best['route'], 'controller': best['controller'],
                                'action': best['action'], 'file': best['file'], 'line': best['line']}
                g['backend_calls'] = best['calls']
                g['manager_methods'] = d['methods']
                g['db_objects'] = compact_db(d['db_objects'])
                g['external_calls'] = [f"{e['kind']}:{e['target']}" for e in d['external_calls']]
                for c in g['callers']:
                    for sc in c['screens']:
                        by_endpoint[ekey(best)][app].add(sc)
                        by_screen.setdefault(app, {}).setdefault(sc, set()).add(ekey(best))
            else:
                g['match'] = None
                unmatched.append({'app': app, 'method': g['method'], 'endpoint': g['frontend_endpoint'],
                                  'path_template': g['path_template'],
                                  'callers': [f"{c['file']}:{c['line']}" for c in g['callers']][:4]})
            groups_out.append(g)
        stats['per_app'][app] = {
            'call_sites_total': n_sites, 'call_sites_external_or_unresolved': n_ext,
            'call_sites_internal': n_sites - n_ext, 'call_sites_matched': matched_sites,
            'site_match_rate_internal': round(matched_sites / max(1, n_sites - n_ext), 4),
            'unique_calls': len(groups), 'unique_calls_matched': matched_g, 'unique_calls_exact_method_and_shape': exact_g,
            'unique_call_match_rate': round(matched_g / max(1, len(groups)), 4),
            'unique_call_exact_rate': round(exact_g / max(1, len(groups)), 4)}
    unused = sorted(ekey(e) for e in backend.endpoints if ekey(e) not in used)
    # also treat endpoints that match by route regardless of method as used for the unused calc
    tot_u = sum(v['unique_calls'] for v in stats['per_app'].values())
    tot_m = sum(v['unique_calls_matched'] for v in stats['per_app'].values())
    tot_s = sum(v['call_sites_internal'] for v in stats['per_app'].values())
    tot_sm = sum(v['call_sites_matched'] for v in stats['per_app'].values())
    stats['overall'] = {'unique_calls': tot_u, 'unique_calls_matched': tot_m,
                        'unique_call_match_rate': round(tot_m / max(1, tot_u), 4),
                        'internal_call_sites': tot_s, 'internal_call_sites_matched': tot_sm,
                        'site_match_rate': round(tot_sm / max(1, tot_s), 4),
                        'backend_endpoints': len(backend.endpoints), 'backend_endpoints_used_by_frontend': len(used),
                        'backend_endpoints_unused': len(unused)}
    unused_by_ctrl = defaultdict(int)
    for u in unused:
        parts = norm_route(u.split(' ', 1)[1])
        unused_by_ctrl[parts[0] if parts else ''] += 1
    out = {
        'meta': {'description': 'frontend api call -> backend endpoint -> manager methods -> db objects (joined graph)',
                 'match_rule': 'frontend resolved path (instance prefix + url, query stripped, params -> {}) compared to backend route (api/ stripped, lowercase) on controller+action; quality tells how strong the match is'},
        'stats': stats,
        'frontend_calls': groups_out,
        'by_screen': {a: {s: sorted(v) for s, v in d.items()} for a, d in by_screen.items()},
        'by_endpoint': {k: {a: sorted(v) for a, v in d.items()} for k, d in by_endpoint.items()},
        'endpoint_downstream': {k: v for k, v in ((k, downstream_cache[k]) for k in downstream_cache)},
        'unmatched_frontend_calls': {'count': len(unmatched), 'items': unmatched},
        'unused_backend_endpoints': {'count': len(unused), 'by_controller': dict(sorted(unused_by_ctrl.items(), key=lambda x: -x[1])),
                                     'samples': unused[:100], 'all': unused},
    }
    return out
