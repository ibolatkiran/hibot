"""BM25 ticket similarity + average-link grouping into recurring problem groups (stdlib only, deterministic).

tokenize(text)                 -> list of terms (fold, ids -> placeholders, stopwords removed, summary bigrams optional)
BM25Index(docs)                -> .neighbors(i, k) ; .self_score(i)
similar_and_groups(docs, ...)  -> (similar: list of top-k [(j, score_norm)], groups: list of lists of doc indexes)

Normalised similarity sim(a,b) = (bm25(a->b)+bm25(b->a)) / (self(a)+self(b)) in [0,~1].
Grouping: average-link agglomerative clustering on the sparse graph of pairs with sim >= EDGE_MIN. Two clusters merge if the
average pairwise sim (missing pairs count as 0) >= GROUP_THRESHOLD. Tuned values are documented in the README.
"""
import heapq
import math
import re
from collections import Counter, defaultdict

from common import fold

K1, B = 1.2, 0.75
EDGE_MIN = 0.22
GROUP_THRESHOLD = 0.30

STOP_EN = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that these those
i we you he she they them our your their my me us not no yes do does did done can could will would should shall may might must
have has had having there here what which who whom when where why how all any some each other more most such than then so too very
just also into over under about after before again up down out off only own same both few per via etc please kindly pls thanks thank
regards dear hello hi hey team colleagues colleague best br sincerely good morning afternoon day need needs needed want help helps
support issue issues problem problems request requests ticket case following below above attached attachment see screenshot
sirius sirisu syrius sirus""".split())
STOP_TR = set("""ve veya ile bir bu su o da de mi mu icin gibi olarak daha cok en ama fakat ancak ise ki ne nasil neden hangi
lutfen rica ederim tesekkurler tesekkur selamlar merhaba merhabalar iyi gunler gunler arkadaslar degerli sayin ilgili konu
konusunda hakkinda dikkatine yardim yardimci destek talep sorun sorunu hata var yok olan olarak sonra once kadar icin""".split())
STOP_RO = set("si sa se nu in la pe cu de din este sunt pentru va rog buna ziua multumesc ca un o".split())
STOPWORDS = STOP_EN | STOP_TR | STOP_RO

_TOKEN = re.compile(r"[^\W_]+(?:[-/.][^\W_]+)*", re.UNICODE)


def _norm_token(t):
    if t.isdigit():
        n = len(t)
        if n == 8 and t[:2] in ("10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "22"):
            return "<wo>"
        if n >= 9:
            return "<id%d>" % min(n, 13)
        if n >= 5:
            return "<n5>"
        return "<n>" if len(t) > 2 else None
    if re.fullmatch(r"c\d{8}", t):
        return "<mat>"
    if re.search(r"\d", t) and len(t) >= 6:   # codes with digits (serial numbers, model strings)
        return "<code>"
    return t


def tokenize(text, bigrams=False):
    toks = []
    for m in _TOKEN.finditer(fold(text)):
        w = m.group(0)
        if "-" in w or "/" in w or "." in w:
            parts = re.split(r"[-/.]", w)
            if len(parts) > 1 and not any(p.isdigit() for p in parts):
                # keep "w/o", "s/n" like short forms joined, split long compounds
                toks_here = [w] if len(w) <= 4 else parts
            else:
                toks_here = [w]
        else:
            toks_here = [w]
        for w2 in toks_here:
            w2 = _norm_token(w2)
            if not w2 or w2 in STOPWORDS or (len(w2) < 2 and not w2.startswith("<")):
                continue
            toks.append(w2)
    if bigrams:
        real = [t for t in toks if not t.startswith("<")]
        toks = toks + [a + "_" + b for a, b in zip(real, real[1:])]
    return toks


class BM25Index:
    def __init__(self, docs_tokens):
        self.docs = docs_tokens
        self.n = len(docs_tokens)
        self.tf = [Counter(d) for d in docs_tokens]
        self.dl = [len(d) for d in docs_tokens]
        self.avgdl = (sum(self.dl) / self.n) if self.n else 1.0
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.post = defaultdict(list)
        for i, c in enumerate(self.tf):
            for t, f in c.items():
                self.post[t].append((i, f))
        self.self_scores = [self._score_pair(i, i) for i in range(self.n)]

    def _term_weight(self, t, f, dl):
        return self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avgdl))

    def _score_pair(self, qi, di):
        s = 0.0
        tf_d = self.tf[di]
        for t in self.tf[qi]:   # binary query terms
            f = tf_d.get(t)
            if f:
                s += self._term_weight(t, f, self.dl[di])
        return s

    def scores(self, qi):
        """bm25(q=doc qi -> every other doc), sparse dict."""
        acc = defaultdict(float)
        for t in self.tf[qi]:
            plist = self.post[t]
            if len(plist) > 0.5 * self.n:      # ubiquitous term, negligible idf
                continue
            for di, f in plist:
                acc[di] += self._term_weight(t, f, self.dl[di])
        acc.pop(qi, None)
        return acc


def similar_and_groups(docs_tokens, top_k=5, edge_min=EDGE_MIN, group_threshold=GROUP_THRESHOLD):
    idx = BM25Index(docs_tokens)
    n = idx.n
    raw = [idx.scores(i) for i in range(n)]
    sim = [dict() for _ in range(n)]
    for i in range(n):
        for j, s in raw[i].items():
            if j < i and i in sim[j]:
                sim[i][j] = sim[j][i]
                continue
            s2 = raw[j].get(i, 0.0)
            den = idx.self_scores[i] + idx.self_scores[j]
            v = (s + s2) / den if den else 0.0
            sim[i][j] = v
    similar = []
    for i in range(n):
        top = sorted(sim[i].items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        similar.append([(j, round(v, 3)) for j, v in top])
    groups = average_link(n, sim, edge_min, group_threshold)
    return similar, groups, sim


def average_link(n, sim, edge_min, threshold):
    """Average-link agglomerative clustering restricted to edges >= edge_min (missing pairs = 0)."""
    members = {i: [i] for i in range(n)}
    link = defaultdict(dict)       # cluster -> {other cluster: sum of pair sims}
    heap = []
    for i in range(n):
        for j, v in sim[i].items():
            if i < j and v >= edge_min:
                link[i][j] = v
                link[j][i] = v
                heapq.heappush(heap, (-v, i, j))
    alive = set(range(n))
    nxt = n
    ver = {i: 0 for i in range(n)}
    while heap:
        negv, a, b = heapq.heappop(heap)
        if a not in alive or b not in alive:
            continue
        s = link[a].get(b)
        if s is None:
            continue
        avg = s / (len(members[a]) * len(members[b]))
        if abs(avg + negv) > 1e-12:     # stale entry; re-queue with current average if still promising
            if avg >= threshold:
                heapq.heappush(heap, (-avg, a, b))
            continue
        if avg < threshold:
            break
        c = nxt
        nxt += 1
        members[c] = members.pop(a) + members.pop(b)
        alive.discard(a)
        alive.discard(b)
        alive.add(c)
        newlink = {}
        for x in (a, b):
            for o, v in link[x].items():
                if o in (a, b):
                    continue
                newlink[o] = newlink.get(o, 0.0) + v
        for x in (a, b):
            for o in list(link[x]):
                link[o].pop(x, None)
            del link[x]
        link[c] = newlink
        for o, v in newlink.items():
            link[o][c] = v
            avg2 = v / (len(members[c]) * len(members[o]))
            if avg2 >= threshold:
                heapq.heappush(heap, (-avg2, c, o))
    out = [sorted(members[c]) for c in alive]
    out.sort(key=lambda g: (-len(g), g[0]))
    return out
