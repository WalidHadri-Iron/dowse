"""Hybrid search: meaning (EmbeddingGemma's vectors) + words (FTS5).

A file qualifies by standing out in meaning or by holding the query's words, and its score is meaning z + a word
bonus. Meaning is cosine similarity on one scale per query (`meaning`): every vector is scored
anyway, so the distribution comes for free, and only what stands out (`Ranking.z_gate`) counts, which also gives an
honest "nothing found".

Three corrections on top, each for something that made files come up for queries they don't match. Measured by
scripts/ranking_experiment.py on evals/testbed.json (47 queries, two long books among the files), on the testbed
alone and with 69 real videos mixed in (2026-10-12): without them R@1 0.87 / 0.81, with a book among the strong
results of 20 / 20 queries that aren't about one; with all three, R@1 0.96 / 0.98, and 2 / 2. Without best-of-n:
0.96 / 0.94, noise 15 / 13. Without rare words: same R@1, noise 5 / 4. Without hubs: 0.96 / 0.94.

- Best of many (`sidak`). A file's meaning is its best segment's, and the best of 470 passages stands out by
  chance alone. The best z is read as what it is, the maximum of n draws, and put back on a one-draw scale. The
  largest gain alone (R@1 0.87 → 0.94 on the testbed, book noise 20 → 7). Correlated segments would argue for fewer
  effective draws; n^0.5 and n^0.75 measured no better and let more books through.
- Common words (`Ranking.rare_words`). A book holds "dog" and "sleep" somewhere. The query's words must be found
  together, in one passage (or the file's name), and each counts by how rare it is in the index.
- Hubs (`Ranking.hub`). Some vectors (game noise, a black frame) sit close to every query. Half of each vector's
  mean similarity to a bank of everyday queries (`probes`) is taken off its score, as CSLS does. At full strength
  it over-corrects (0.94 vs 0.96 on the testbed); without it, the real videos' noise comes back (0.94 vs 0.98).

Tried and dropped: counting every file once in the scale instead of every vector (worse alone, no better with the
rest), and keeping a long file's later pages out of the scale (worse than counting them).

What a query says besides its meaning (2026-10-13; evals/testbed.json + evals/hard.json, 152 queries with an answer
and 8 with none, the same two conditions, the index as read before the readers changed): R@1 0.855 / 0.849.

- Dates and kinds (`Ranking.filters`). "From last summer" filters on when a photo was taken (else the file's last
  change) and is taken out of what the model is asked; a kind word that leads the query or says where ("photos of
  snow", "zebra in a video") filters. Without: 0.816 / 0.809.
- Spelling (`Ranking.spelling`). A word found nowhere is tried as the nearest word of a file or folder name
  ("sherlok" → "sherlock"). Without: 0.842 / 0.836, book noise 8 / 9 instead of 10 / 11.

The testbed read again with today's readers (names as titles, photos' words, spreadsheets, e-books, scripts; CPU
reading options): R@1 0.954, MRR 0.971, from 0.783 / 0.820 before all of this.

Saying when nothing matches (`Ranking.clear`, `Hit.clear`). Standing out can't tell: something always stands out (for
"a giraffe", the zebra photo, z 3.7, as far out as true matches go). What can: whether the hit is better described by
the query than by the everyday probes (those that aren't the query in other words, cosine under 0.85). A zebra photo
answers "a giraffe" best of anything here, but "a horse in a field" describes it better. A hit is clear when at most
`outranked` (1) probes beat the query on it, or when all the query's words are in it together; when the top hit
isn't clear, nothing is strong ("nothing clearly matches; the closest"). Ranking doesn't change. False alarms on the
8 queries with no answer, fresh read / testbed alone / whole index: 8 / 8 / 8 → 1 / 1 / 1 (with "wedding photos"
read as photos, a kind it names); right answers no longer said to be found: 4 / 5 / 4 of 152. With 0 probes allowed:
1 false alarm, 9 / 14 / 13 unsure; with 3: 3–4 false alarms, 0–2 unsure. (1 was chosen looking at these queries.)

At real size (2026-10-15, `ranking_experiment.py --fresh --among`): the testbed read now, alone and among 10,723
files of a real PC (downloads, desktop, documents, videos; 63,521 vectors). R@1 0.941 → 0.770, MRR 0.964 → 0.818,
strong results a query 1.4 → 5.2, false alarms 1 → 6 of 8. Most of it is the eval, not the search: of the 35 misses,
about 19 are the PC's own files answering too (dog videos for "a dog", a yellow flower, a drawing of a bicycle), 6 are
the testbed's own confusions, and about 10 are noise: sounds (generated speech, a sound effect) and spoken product
reviews coming first for what they don't answer. Of the 6 false alarms, 2 have a real answer there (passport scans,
sand photos), 1 is the wrong year ("tax return 2019": a 2024 tax notice), and 3 are false: "a giraffe", "video of a
birthday party", "fuel consumption of my car", each given clearly by a video or a sound. Counting the PC's right
answers as right, R@1 is about 0.89. Hubs at full strength: 0.796 there, 0.941 alone.
The testbed's person renamed to Alex (2026-10-16: a letter's signature, a greeting, the e-mails' To: address), read
again: R@1 0.941 → 0.934, MRR 0.964 → 0.961, the rest the same. The one change is "rendez-vous chez l'opticien":
optician.eml now second behind note-fr.txt, as it already was without the corrections, a near tie its header tipped.

Small indexes (2026-10-24, `scripts/small_index_experiment.py`: each query's answer among k − 1 files drawn from the
testbed, 5 draws). On one scale for the whole index, n vectors can't stand out further than (n − 1)/√n, so an index of
four photos never held a match ("a lake between the mountains": nothing, among four files). Measured against the
others instead, through Student's t (`apart`, under `SMALL` vectors), found and said to be found, one scale → now:
2 files 0.457 → 0.530, 3 files 0.484 → 0.571, 4 files 0.562 → 0.825, 6 files 0.847 → 0.909, 10 files 0.920 → 0.936;
false alarms 0 of 40 either way; from 20 files (~250 vectors) the same. The testbed (812 vectors) is unchanged.

More like this (`like`): a file, or a place in it, as the query. Its vectors are already here, so nothing is read or
embedded. A file with several (a PDF's pages, a video's frames and sound) is searched with each of them, up to
`LIKE_VECTORS`, and every vector keeps the best z any of them gave it: a file is like another when some part of one
is like some part of the other. Measured on a demo library of made-up files (270 files in 33 folders of 4 or more, sorted by hand), as precision@5 of neighbours from the file's own folder
(2026-10-15): the mean of its vectors 0.481 (PDFs 0.693, documents 0.353, videos 0.152), only its main modality's
0.479, z averaged over its vectors 0.470, the best z over them 0.530 (0.718, 0.511, 0.288). 8 vectors measured the
same as 32 (0.530) and 128 (0.527); `like` itself, with 16: 0.529. Pictures have one vector each: 0.434 whichever
way. A neighbour is plainly like it (`Hit.clear`) when it stands out as a search's match must (`Ranking.z_gate`).
"""

import itertools
import json
import math
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np

from . import dates, probes, search_scope, searchtext
from .embedder import DIM
from .rules import Kind
from .store import Store
from . import paths

BLOCK = 16384
LIKE_VECTORS = 16   # how many of an example's vectors search for what's like it (evenly spread through it)
_NORMAL = NormalDist()

KIND_WORDS = {
    "screenshot": Kind.SCREENSHOT, "screenshots": Kind.SCREENSHOT, "photo": Kind.IMAGE, "photos": Kind.IMAGE,
    "picture": Kind.IMAGE, "pictures": Kind.IMAGE, "image": Kind.IMAGE, "images": Kind.IMAGE, "pdf": Kind.PDF,
    "pdfs": Kind.PDF, "paper": Kind.PDF, "papers": Kind.PDF, "document": Kind.DOC, "documents": Kind.DOC,
    "note": Kind.DOC, "notes": Kind.DOC, "video": Kind.VIDEO, "videos": Kind.VIDEO, "clip": Kind.VIDEO,
    "movie": Kind.VIDEO, "audio": Kind.AUDIO, "song": Kind.AUDIO, "podcast": Kind.AUDIO, "voice": Kind.AUDIO,
    "recording": Kind.AUDIO,
}
# Kind words that say plainly what's wanted: these filter ("photos of snow" is only photos). The others only lean
# ("doctor's note" may well be a PDF).
KIND_FILTERS = {
    "screenshot": {Kind.SCREENSHOT}, "screenshots": {Kind.SCREENSHOT}, "photo": {Kind.IMAGE},
    "photos": {Kind.IMAGE}, "picture": {Kind.IMAGE, Kind.SCREENSHOT}, "pictures": {Kind.IMAGE, Kind.SCREENSHOT},
    "image": {Kind.IMAGE, Kind.SCREENSHOT}, "images": {Kind.IMAGE, Kind.SCREENSHOT}, "pdf": {Kind.PDF},
    "pdfs": {Kind.PDF}, "video": {Kind.VIDEO}, "videos": {Kind.VIDEO}, "movie": {Kind.VIDEO}, "clip": {Kind.VIDEO},
    "recording": {Kind.AUDIO}, "recordings": {Kind.AUDIO}, "podcast": {Kind.AUDIO}, "audio": {Kind.AUDIO},
}
for_meaning = dates.for_meaning   # what the model is asked: the query without its date


@dataclass(frozen=True)
class Ranking:
    """How results are ranked. The defaults are what the evals chose; each switch turns one correction off."""
    z_gate: float = 2.0          # how far a meaning match must stand out to count
    word_weight: float = 2.0     # the word bonus at full strength
    sidak: bool = True           # best-of-n correction (False: a file's best segment as it is)
    hub: float = 0.5             # how much of a vector's mean similarity to the probes is taken off (0: off)
    rare_words: bool = True      # words found together, weighted by rarity (False: anywhere in the file, all equal)
    filters: bool = True         # a date or a kind said plainly in the query filters (False: only the words count)
    spelling: bool = True        # a word found nowhere is tried as the names' nearest spelling
    clear: bool = True           # a hit counts as found only if it prefers this query to everyday ones (`Hit.clear`)
    outranked: int = 1           # how many everyday probes may describe a clear hit better than the query
    paraphrase: float = 0.85     # a probe this close to the query is the query in other words, not another one


@dataclass
class Hit:
    path: str
    kind: Kind
    score: float                 # for ordering only
    z: float | None              # how far the file stands out for this query (best-of-n corrected)
    cosine: float | None
    keyword_rank: int | None     # 1-based rank among keyword matches
    segment: str                 # the segment pointed at (`store.Segment`)
    loc: float | None            # PDF page, passage, or seconds
    loc_end: float | None
    excerpt: str | None
    mtime: float
    size: int
    info: dict
    moments: list[tuple[float, float | None]] = field(default_factory=list)   # more places in the file that match
    segment_id: int = 0          # the segment pointed at (its words: `text_of`)
    clear: bool = True           # it plainly matches: all the query's words are in it, or it's better described by
                                 # the query than by everyday ones ("a giraffe" for a zebra: no, "wild animals" is)
    evidence: list[dict] = field(default_factory=list)  # observed words and locations; never generated reasons


def is_lookup(terms: list[str]) -> bool:
    """Codes and IDs ("K7Q2LM", "TRX50") and long numbers mean nothing to an embedding but everything to keyword
    search: such a query weighs words double and meaning half."""
    return bool(terms) and all(any(c.isdigit() for c in t) and (any(c.isalpha() for c in t) or len(t) >= 4)
                               for t in terms)


def kind_hints(text: str) -> set[Kind]:
    return {KIND_WORDS[w] for w in searchtext.normalized(text).lower().split() if w in KIND_WORDS}


LEADING = frozenset("a an the my our your some all any".split())


def kind_filter(text: str) -> set[Kind]:
    """The kinds a query asks for plainly, or none: a kind word that leads it ("photos of snow", "the pdf about
    renting"), follows what it's of ("wedding photos") or says where ("zebra in a video"). Elsewhere it's what the
    file is about ("script that shrinks photos") and only leans."""
    words = [w.strip(".,;:!?'\"") for w in searchtext.normalized(text).lower().split()]
    if "screen recording" in " ".join(words):
        return {Kind.VIDEO}
    words = [w for w in words if w]
    while words and words[0] in LEADING:
        words = words[1:]
    out = set(KIND_FILTERS.get(words[0], set())) if words else set()
    if len(words) >= 2 and words[1] in KIND_FILTERS:   # "wedding photos", "sunflower photo": what they're of
        out |= KIND_FILTERS[words[1]]
    for index, word in enumerate(words[1:], 1):
        before = words[index - 1] if words[index - 1] not in LEADING or index < 2 else words[index - 2]
        if word in KIND_FILTERS and before == "in":
            out |= KIND_FILTERS[word]
    return out


def spelling_distance(a: str, b: str, most: int) -> int:
    """Edits from `a` to `b` (a letter added, dropped, changed, or two swapped), or `most` + 1 once over `most`."""
    if abs(len(a) - len(b)) > most:
        return most + 1
    before, row = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            current[j] = min(row[j] + 1, current[j - 1] + 1, row[j - 1] + (a[i - 1] != b[j - 1]))
            if before is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], before[j - 2] + 1)
        if min(current) > most:
            return most + 1
        before, row = row, current
    return row[-1]


def _trigrams(word: str) -> set[str]:
    padded = f"  {word} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


_taken = dates.taken


SMALL = 200   # under this many vectors, the scale itself is uncertain (`apart`)


def meaning(scores: np.ndarray, modality: np.ndarray) -> np.ndarray:
    """Each vector's score minus its modality's average (text, image and audio vectors sit at different levels for
    the same query), in standard deviations of all of them together. Groups under 20 vectors take the overall mean.
    A small index (under `SMALL` vectors) measures each against the others instead (`apart`)."""
    if not len(scores):
        return np.zeros(0)
    centered = scores.astype(np.float64)
    overall = centered.mean()
    for value in np.unique(modality):
        rows = modality == value
        centered[rows] -= centered[rows].mean() if rows.sum() >= 20 else overall
    if len(centered) < SMALL:
        return apart(centered)
    deviation = max(float(centered.std()), 0.02)
    return (centered - centered.mean()) / deviation


def apart(values: np.ndarray) -> np.ndarray:
    """How far each value stands from the others, on the scale of one draw from many. In standard deviations of all
    of them, n values can't stand out further than (n − 1)/√n (1.5 for four), so a folder of five photos could never
    hold a match. Each is measured against the n − 1 others instead, as a new draw from them (its distance from their
    mean in their deviation, √(1 + 1/(n − 1)) wider for their mean's own error), and read through Student's t with
    n − 2 degrees of freedom as the z with the same chance: few values give an uncertain deviation, so standing out
    among them takes more. Under three values there's no deviation to measure: nothing stands out."""
    n = len(values)
    if n < 3:
        return np.zeros(n)
    others = (values.sum() - values) / (n - 1)
    spread = ((values ** 2).sum() - values ** 2 - (n - 1) * others ** 2) / (n - 2)   # the others' sample variance
    deviation = np.maximum(np.sqrt(np.maximum(spread, 0)), 0.02) * math.sqrt(1 + 1 / (n - 1))
    return np.array([_t_as_z(float(t), n - 2) for t in (values - others) / deviation])


def _t_as_z(t: float, df: int) -> float:
    """The z with the same one-sided chance as Student's t with `df` degrees of freedom."""
    if t == 0:
        return 0.0
    tail = 0.5 * _beta_regularized(df / (df + t * t), df / 2, 0.5)   # P(T ≥ |t|)
    z = -_NORMAL.inv_cdf(min(max(tail, 1e-300), 0.5))
    return z if t > 0 else -z


def _beta_regularized(x: float, a: float, b: float) -> float:
    """I_x(a, b), the regularized incomplete beta function (its continued fraction, by Lentz's method)."""
    if x <= 0 or x >= 1:
        return 0.0 if x <= 0 else 1.0
    if x > (a + 1) / (a + b + 2):   # the fraction converges on the other side: I_x(a, b) = 1 − I_(1−x)(b, a)
        return 1 - _beta_regularized(1 - x, b, a)
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    fraction = d
    for m in range(1, 300):
        for step in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                     -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1 + step * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + step / c
            c = c if abs(c) > tiny else tiny
            fraction *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return front * fraction / a


def sidak(z: float, n: float) -> float:
    """The best of `n` z-scores, put back on the scale of one: z where P(one draw ≥ z) = P(the best of n ≥ the
    observed best), Šidák's correction. One segment: unchanged. A book's chance best of 3.9 over 470 passages: 2.0.
    A passage that truly matches (z 6) stays far out: 4.9."""
    if n <= 1:
        return z
    tail = 0.5 * math.erfc(z / math.sqrt(2))                     # P(one draw ≥ z)
    if tail >= 1:
        return z
    tail_n = -math.expm1(n * math.log1p(-tail))                  # P(the best of n ≥ z), without losing precision
    return -_NORMAL.inv_cdf(min(max(tail_n, 1e-300), 1 - 1e-12))


def strong_count(hits: list[Hit], ratio: float = 0.5) -> int:
    """Hits scoring at least `ratio` × the top one and plainly matching; the rest trail behind ("other matches").
    0 when the best isn't a plain match: nothing clearly matches, and these are only the closest."""
    if not hits or not hits[0].clear:
        return 0
    if hits[0].score <= 0:
        return len(hits)
    return sum(1 for _ in itertools.takewhile(lambda h: h.score >= hits[0].score * ratio and h.clear, hits))


@dataclass
class _Words:
    """A file's keyword match: its best place, and what the word bonus is made of."""
    rank: int                 # 1-based, by BM25 over the whole index
    segment: int              # where the words are: the segment holding the most of them together
    coverage: float = 0.0     # share of the query's words found together (weighted by rarity when `rare_words`)
    specificity: float = 1.0  # how rare the words found are, on average (1 without `rare_words`)
    patterns: list[str] = field(default_factory=list)
    tag_match: bool = False     # all words match music metadata, not only its folder


class Searcher:
    def __init__(self, store: Store, ranking: Ranking | None = None):
        self.store = store
        self.ranking = ranking or Ranking()
        self.reload()

    def reload(self) -> None:
        """Reads every vector into memory (fp16: 100k vectors are ~150 MB) and what ranking needs about them."""
        with self.store.snapshot():
            self.model_tag = self.store.meta("vectors")
            self._reload_snapshot()

    def _reload_snapshot(self) -> None:
        rows = self.store.db.execute(
            "SELECT s.id, s.file, s.modality, s.vec FROM segments s JOIN files f ON f.id = s.file "
            "WHERE s.vec IS NOT NULL AND f.active = 1 ORDER BY s.id").fetchall()
        self.row_segment = np.fromiter((r[0] for r in rows), np.int64, len(rows))
        self.row_file = np.fromiter((r[1] for r in rows), np.int64, len(rows))
        self.row_modality = np.array([r[2] for r in rows], dtype="<U5")
        self.matrix = (np.frombuffer(b"".join(r[3] for r in rows), "<f2").reshape(len(rows), DIM)
                       if rows else np.zeros((0, DIM), np.float16))
        self.file_kind = {i: Kind(k) for i, k in self.store.db.execute("SELECT id, kind FROM files WHERE active = 1")}
        files, counts = np.unique(self.row_file, return_counts=True)
        self.file_vectors = dict(zip(files.tolist(), counts.tolist()))
        self.row_hub = self._hubness()
        self.segment_count = self.store.db.execute(
            "SELECT COUNT(*) FROM segments s JOIN files f ON f.id = s.file WHERE f.active = 1").fetchone()[0]
        # When each file is from, for dates in queries: a photo's taking, or else the file's last change
        self.file_time = {file: _taken(taken) or mtime for file, mtime, taken in self.store.db.execute(
            "SELECT id, mtime, json_extract(info, '$.taken') FROM files WHERE active = 1")}
        self._scopes: dict[str, set[int]] = {}
        self._names: dict[str, set[str]] | None = None   # trigram → the words of file and folder names (on demand)

    def _hubness(self) -> np.ndarray:
        """Each vector's mean similarity to the probes: how close it sits to queries in general. (Over all of them:
        its 10 closest measured the same or worse.)"""
        bank = probes.load(self.store) if self.ranking.hub or self.ranking.clear else None
        self.bank = bank.astype(np.float32) if bank is not None else None
        if bank is None or not self.ranking.hub or not len(self.matrix):
            return np.zeros(len(self.matrix))
        centroid = bank.mean(axis=0)   # the mean of dot products is the dot product with the mean
        return self.scores(centroid).astype(np.float64)

    def _under(self, folder: str) -> set[int]:
        """The files under `folder`, compared as Windows does (any case, either slash), kept for the next keystroke
        (a searcher lives until the index changes)."""
        key = paths.fold(os.path.abspath(folder)).rstrip("\\/") + os.sep
        if key not in self._scopes:
            self._scopes[key] = {file for file, path in self.store.db.execute("SELECT id, path FROM files")
                                 if paths.fold(path).startswith(key)}
        return self._scopes[key]

    def scores(self, query: np.ndarray) -> np.ndarray:
        query = np.asarray(query, np.float32)
        out = np.empty(len(self.matrix), np.float32)
        for start in range(0, len(self.matrix), BLOCK):
            out[start:start + BLOCK] = self.matrix[start:start + BLOCK].astype(np.float32) @ query
        return out

    def search(self, text: str, vector: np.ndarray | None, kinds: set[Kind] | None = None, limit: int = 20,
               exclude: set[str] | None = None, prefix_last: bool = False, z_gate: float | None = None,
               within: str | list[str] | None = None, now: float | None = None, *, infer_date: bool = True,
               infer_kinds: bool = True, date_span: dates.Span | None = None,
               example: np.ndarray | None = None) -> list[Hit]:
        """`z_gate`: how far a meaning match must stand out to count; "find similar" opens it fully (its nearest
        neighbours always count; only the example itself would stand out). `within`: files under any selected folder.
        Scores stay on the whole index's scale, so a folder's best match is judged as it would be anywhere.
        A date in `text` ("from last summer", against `now`) and a kind named plainly ("photos") filter; `vector`
        should be the model's for `for_meaning(text)`. `example`: vectors standing for what's wanted instead (`like`)."""
        ranking = self.ranking
        z_gate = ranking.z_gate if z_gate is None else z_gate
        roots = search_scope.folders(within)
        scope = set().union(*(self._under(root) for root in roots)) if roots else None
        if scope is not None and not scope:
            return []
        inferred = dates.parse(text, now) if ranking.filters else None
        span = date_span or (inferred if infer_date else None)
        if inferred is not None:
            text = inferred.rest
        if not kinds and ranking.filters and infer_kinds:
            kinds = kind_filter(text) or None

        def allowed(file: int) -> bool:
            return file in self.file_kind and (not kinds or self.file_kind[file] in kinds) and \
                (scope is None or file in scope) and \
                (span is None or span.start <= self.file_time.get(file, 0) < span.end)

        # Meaning: every vector on this query's scale; each file keeps its best segment, corrected for how many
        # segments it had to choose from.
        best: dict[int, tuple[float, float, int]] = {}   # file → (z, cosine, row)
        row_z = np.zeros(0)
        if (vector is not None or example is not None) and len(self.matrix):
            if example is not None:
                cosine, row_z = self._like(example)
            else:
                cosine = self.scores(vector)
                row_z = meaning(cosine - ranking.hub * self.row_hub, self.row_modality)
            order = np.argsort(-row_z, kind="stable")
            files, first = np.unique(self.row_file[order], return_index=True)   # each file's best row
            for file, row in zip(files.tolist(), order[first].tolist()):
                if allowed(file):
                    z = sidak(float(row_z[row]), self.file_vectors[file]) if ranking.sidak else float(row_z[row])
                    best[file] = (z, float(cosine[row]), row)

        terms = searchtext.terms(text)
        words = self._words(terms, allowed, prefix_last, row_z,
                            filtered=bool(kinds) or scope is not None or span is not None)

        # A file qualifies by standing out in meaning or holding half the query's words. Score: meaning z + a bonus
        # growing with how many of the words it holds together and how rare they are, + 1 for the kind asked for.
        lookup = is_lookup(terms)
        meaning_weight, words_weight = (0.5, ranking.word_weight * 2) if lookup else (1.0, ranking.word_weight)
        hinted = kind_hints(text)
        candidates = {f for f, (z, _, _) in best.items() if z >= z_gate} | \
                     {f for f, w in words.items() if w.coverage >= 0.5}
        if (span is not None or inferred is not None or (not text.strip() and (kinds or scope is not None))) and not terms:
            candidates |= {f for f in self.file_kind if allowed(f)}
        scored = {}
        for file in candidates:
            z = best.get(file, (0.0,))[0]
            match = words.get(file)
            bonus = words_weight * match.coverage ** 1.5 * match.specificity if match else 0.0
            if match and match.tag_match:
                bonus += 0.5 * words_weight * match.specificity
            score = meaning_weight * (max(z, 0) if z_gate >= 0 else z) + bonus
            if self.file_kind.get(file) in hinted:
                score += 1
            if span is not None:   # between equals, the newest first
                score += self.file_time.get(file, 0) / 1e12
            scored[file] = score

        hits: list[Hit] = []
        for file, score in sorted(scored.items(), key=lambda item: (-item[1], item[0])):
            if len(hits) >= limit:
                break
            semantic, keyword = best.get(file), words.get(file)
            use_meaning = semantic is not None and (semantic[0] >= z_gate or keyword is None)
            segment_id = int(self.row_segment[semantic[2]]) if use_meaning else keyword.segment if keyword else \
                self.store.db.execute("SELECT id FROM segments WHERE file = ? ORDER BY kind = 'name', id LIMIT 1",
                                      (file,)).fetchone()[0]
            row = self.store.db.execute(
                "SELECT f.path, f.kind, f.mtime, f.size, f.info, s.kind, s.loc, s.loc_end, s.excerpt "
                "FROM files f JOIN segments s ON s.file = f.id WHERE s.id = ?", (segment_id,)).fetchone()
            if row is None or (exclude and row[0] in exclude):
                continue
            hit = Hit(row[0], Kind(row[1]), score, semantic[0] if semantic else None,
                      semantic[1] if semantic else None, keyword.rank if keyword else None, row[5], row[6], row[7],
                      self._excerpt(segment_id, row[5], keyword, terms) or row[8], row[2], row[3],
                      json.loads(row[4] or "{}"), segment_id=segment_id)
            if semantic is not None and row[6] is not None:
                hit.moments = self._moments(file, row_z, (row[6], row[7]),
                                            ranking.z_gate if example is not None else z_gate)
            if example is not None:   # like the example: standing out as a search's match must
                hit.clear = semantic is not None and semantic[0] >= ranking.z_gate
            elif ranking.clear and vector is not None and z_gate >= 0:   # ("find similar" asks for neighbours)
                hit.clear = (keyword is not None and keyword.coverage >= 0.999) or \
                    (semantic is not None and self._preferred(vector, semantic[2]))
            elif ranking.clear and vector is None:   # words only: found if all of them are
                hit.clear = keyword is not None and keyword.coverage >= 0.999
            hit.evidence = self._evidence(file, hit, keyword, use_meaning)
            hits.append(hit)
        return hits

    def like(self, example: np.ndarray, kinds: set[Kind] | None = None, limit: int = 20,
             exclude: set[str] | None = None, within: str | list[str] | None = None,
             date_span: dates.Span | None = None) -> list[Hit]:
        """More like this: the files nearest to `example` (vectors: a file's, a place in it, or a file read just for
        this), each at its place most like it. Every file is a neighbour; the first stand out."""
        return self.search("", None, kinds=kinds, limit=limit, exclude=exclude, z_gate=-math.inf, within=within,
                           infer_date=False, infer_kinds=False, date_span=date_span, example=example)

    def rows_of(self, path: str, loc: float | None = None) -> np.ndarray:
        """The rows of a file's vectors; with `loc`, those at that place: a PDF's page, a document's passage, or what's
        seen and heard `loc` seconds into a video or recording (the frame showing then, the sound around it). The
        whole file when nothing is there."""
        found = self.store.db.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if found is None:
            return np.zeros(0, np.int64)
        rows = np.flatnonzero(self.row_file == found[0])
        if loc is None or not len(rows):
            return rows
        places = {segment: (kind, at, end) for segment, kind, at, end in self.store.db.execute(
            f"SELECT id, kind, loc, loc_end FROM segments WHERE id IN ({','.join(str(int(self.row_segment[r])) for r in rows)})")}
        place = [places[int(self.row_segment[r])] for r in rows]
        if self.file_kind.get(found[0]) in (Kind.PDF, Kind.DOC):
            here = [r for r, (_, at, _) in zip(rows, place) if at is not None and round(at) == round(loc)]
        else:
            heard = [r for r, (kind, at, end) in zip(rows, place)
                     if kind != "frame" and at is not None and at <= loc <= (end if end is not None else at)]
            frames = [(at, r) for r, (kind, at, _) in zip(rows, place) if kind == "frame" and at is not None]
            shown = max(((at, r) for at, r in frames if at <= loc), default=None) or \
                min(frames, key=lambda f: abs(f[0] - loc), default=None)
            here = heard + ([shown[1]] if shown else [])
        return np.array(here, np.int64) if here else rows

    def vectors(self, rows: np.ndarray) -> np.ndarray:
        """These rows' vectors, at most `LIKE_VECTORS` of them, evenly spread (a book's beginning, middle and end)."""
        if len(rows) > LIKE_VECTORS:
            rows = rows[np.linspace(0, len(rows) - 1, LIKE_VECTORS).round().astype(int)]
        return self.matrix[rows].astype(np.float32)

    def _like(self, example: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(cosine, z) of every vector for an example of several vectors: for each, the best any of them gives it,
        each z on its own query's scale."""
        example = np.atleast_2d(np.asarray(example, np.float32))
        cosine = np.empty((len(self.matrix), len(example)), np.float32)
        for start in range(0, len(self.matrix), BLOCK):   # the index read once, whatever the number of vectors
            cosine[start:start + BLOCK] = self.matrix[start:start + BLOCK].astype(np.float32) @ example.T
        z = np.column_stack([meaning(cosine[:, i] - self.ranking.hub * self.row_hub, self.row_modality)
                             for i in range(len(example))])
        return cosine.max(axis=1), z.max(axis=1)

    def _preferred(self, query: np.ndarray, row: int) -> bool:
        """Whether this vector is described by the query at least as well as by all but `outranked` of the everyday
        probes (leaving out those that are the query in other words). A zebra photo answers "a giraffe" better than
        anything else here, yet "a horse in a field" describes it better: it isn't what was asked for."""
        if self.bank is None:
            return True
        query = np.asarray(query, np.float32)
        others = self.bank[self.bank @ query < self.ranking.paraphrase]
        vector = self.matrix[row].astype(np.float32)
        return int(((others @ vector) > float(query @ vector)).sum()) <= self.ranking.outranked

    def _words(self, terms: list[str], allowed, prefix_last: bool, row_z: np.ndarray,
               filtered: bool = False) -> dict[int, _Words]:
        """Files holding the query's words: up to 100, ranked by BM25, with how well each holds them and where.
        `filtered` (by kind or folder): every match is looked at, not the best 1,000, which might all be elsewhere.
        Where: the segment holding the most of them together (the rarest counting most), and between equals the one
        closest in meaning. (BM25's first is often a passage that repeats one common word.)"""
        patterns = searchtext.fts_patterns(terms, prefix_last)
        if not patterns:
            return {}
        for index, term in enumerate(terms):   # a word found nowhere: perhaps a name, misspelt
            if self.ranking.spelling and len(term) >= 4 and term.isalpha() and not self.store.db.execute(
                    "SELECT 1 FROM fts WHERE fts MATCH ? LIMIT 1", (patterns[index],)).fetchone() \
                    and (spellings := self._spelled(term)):
                patterns[index] = "(" + " OR ".join([patterns[index], *(f'"{w}"' for w in spellings)]) + ")"
        words: dict[int, _Words] = {}
        for file, segment in self.store.db.execute(
                "SELECT s.file, s.id FROM fts JOIN segments s ON s.id = fts.rowid JOIN files f ON f.id = s.file "
                "WHERE fts MATCH ? AND f.active = 1 "
                "ORDER BY CASE WHEN s.kind = 'tag' THEN 0 ELSE 1 END, bm25(fts, 3.0, 1.0)" +
                ("" if filtered else " LIMIT 1000"),
                (" OR ".join(patterns),)):
            if len(words) >= 100:
                break
            if file not in words and allowed(file):
                words[file] = _Words(len(words) + 1, segment)
        if not words:
            return words

        # Which words each segment of these files holds (a file's name counts as part of every one of its segments).
        ids = ",".join(map(str, words))
        held: dict[int, dict[int, set[int]]] = {f: {} for f in words}   # file → segment → word indexes
        names: dict[int, set[int]] = {f: set() for f in words}
        tags: dict[int, set[int]] = {f: set() for f in words}
        for index, pattern in enumerate(patterns):
            for segment, file, kind in self.store.db.execute(
                    f"SELECT s.id, s.file, s.kind FROM fts JOIN segments s ON s.id = fts.rowid "
                    f"WHERE fts MATCH ? AND s.file IN ({ids})", (pattern,)):
                if kind == "tag":
                    tags[file].add(index)
                (names[file] if kind == "name" else held[file].setdefault(segment, set())).add(index)

        rarity = self._rarity(patterns) if self.ranking.rare_words else [1.0] * len(patterns)
        total = sum(rarity)
        segment_z: dict[int, float] = {}
        if len(row_z):
            rows = np.flatnonzero(np.isin(self.row_file, list(words)))
            segment_z = dict(zip(self.row_segment[rows].tolist(), row_z[rows].tolist()))
        for file, match in words.items():
            match.patterns = patterns
            places = held[file]
            if places:
                match.segment = max(places, key=lambda segment: (sum(rarity[i] for i in places[segment]),
                                                                 segment_z.get(segment, -math.inf)))
            if self.ranking.rare_words:
                found = held[file].get(match.segment, set()) | names[file]
            else:   # the words anywhere in the file
                found = set().union(names[file], *held[file].values())
            match.coverage = sum(rarity[i] for i in found) / total if total else 0.0
            match.specificity = (sum(rarity[i] for i in found) / len(found)) if found and self.ranking.rare_words \
                else 1.0
            match.tag_match = bool(patterns) and len(tags[file]) == len(patterns)
        return words

    def _evidence(self, file: int, hit: Hit, keyword: _Words | None, semantic: bool) -> list[dict]:
        """FTS's actual matched tokens, including spelling/prefix matches, at their own locations.

        A semantic page must never borrow a quote from a different keyword page. Photo OCR is a separate source.
        The name index also holds folder names, so those must not be claimed as filename matches.
        """
        out = []
        if keyword and keyword.patterns:
            sources = {hit.segment_id}
            if hit.segment == "image":
                sources.add(keyword.segment)
            if self.store.db.execute("SELECT kind FROM segments WHERE id = ?", (keyword.segment,)).fetchone() == ("tag",):
                sources.add(keyword.segment)   # an audio vector may win, but its matching music tags are still evidence
            rows = self.store.db.execute(
                "SELECT s.id, s.kind, s.loc, s.loc_end, highlight(fts, 0, '«', '»'), "
                "highlight(fts, 1, '«', '»') FROM fts JOIN segments s ON s.id = fts.rowid "
                "WHERE s.file = ? AND fts MATCH ? AND (s.kind = 'name' OR s.id IN (" +
                ",".join(map(str, sources)) + "))", (file, " OR ".join(keyword.patterns)))
            for segment, kind, loc, end, name, body in rows:
                if kind == "name":
                    stem = searchtext.normalized(Path(hit.path).stem.replace("_", " ").replace("-", " ")).lower()
                    stem_words = {stem[a:b] for a, b in searchtext.words(stem)}
                    filename, folder = [], []
                    for word in re.findall("«(.*?)»", name or ""):
                        (filename if word.lower() in stem_words else folder).append(word)
                    for source, words in (("filename", filename), ("folder", folder)):
                        if words:
                            out.append({"source": source, "words": list(dict.fromkeys(words)), "loc": None})
                else:
                    title_words = re.findall("«(.*?)»", name or "")
                    if title_words:
                        out.append({"source": "title", "words": list(dict.fromkeys(title_words)),
                                    "loc": loc, "locEnd": end, "segment": segment})
                    words = re.findall("«(.*?)»", body or "")
                    if words:
                        out.append({"source": "ocr" if kind == "ocr" else "tag" if kind == "tag" else "text",
                                    "words": list(dict.fromkeys(words)), "loc": loc, "locEnd": end,
                                    "segment": segment})
        if semantic:
            out.append({"source": "semantic", "words": [], "loc": hit.loc, "locEnd": hit.loc_end,
                        "modality": "visual" if hit.segment in ("image", "frame", "page_image") else
                        "audio" if hit.segment == "audio" else "text"})
        return out

    def _spelled(self, term: str) -> list[str]:
        """Words of file and folder names a letter or two from `term` ("sherlok" → "sherlock"): one edit away for
        words up to 6 letters, two for longer ones. The closest, three at most."""
        if self._names is None:
            self._names = {}
            for (name,) in self.store.db.execute(
                    "SELECT fts.name FROM fts JOIN segments s ON s.id = fts.rowid WHERE s.kind = 'name'"):
                folded = searchtext.normalized(name).lower()
                for start, end in searchtext.words(folded):
                    word = folded[start:end]
                    if len(word) >= 3 and word.isalpha():
                        for gram in _trigrams(word):
                            self._names.setdefault(gram, set()).add(word)
        most = 1 if len(term) <= 6 else 2
        nearby = set().union(*(self._names.get(gram, set()) for gram in _trigrams(term)))
        distances = {word: spelling_distance(term, word, most) for word in nearby if word != term}
        close = sorted((d, w) for w, d in distances.items() if d <= most)
        return [w for d, w in close if d == close[0][0]][:3] if close else []

    def fit(self, vector: np.ndarray) -> dict[int, float]:
        """How far each file stands out for a query vector, on the search's own scale (its best vector's z, best of n
        corrected): what a search would find, for every file at once, without building hits."""
        if not len(self.matrix):
            return {}
        row_z = meaning(self.scores(vector) - self.ranking.hub * self.row_hub, self.row_modality)
        order = np.argsort(-row_z, kind="stable")
        files, first = np.unique(self.row_file[order], return_index=True)
        return {int(file): sidak(float(row_z[row]), self.file_vectors[int(file)]) if self.ranking.sidak else float(row_z[row])
                for file, row in zip(files.tolist(), order[first].tolist()) if int(file) in self.file_kind}

    def _rarity(self, patterns: list[str]) -> list[float]:
        """How rare each word is in the index, from 1 (in one segment) to ~0 (in all of them): its IDF over the
        index's largest possible IDF. "K7Q2LM" is 1; "dog" in a library with novels in it, around a half."""
        segments = max(self.segment_count, 2)
        out = []
        for pattern in patterns:
            found = self.store.db.execute(
                "SELECT COUNT(*) FROM fts JOIN segments s ON s.id = fts.rowid JOIN files f ON f.id = s.file "
                "WHERE fts MATCH ? AND f.active = 1", (pattern,)).fetchone()[0]
            out.append(min(max(math.log(segments / max(found, 1)) / math.log(segments), 0.05), 1.0))
        return out

    def _excerpt(self, segment_id: int, segment_kind: str, keyword: _Words | None, terms: list[str]) -> str | None:
        """The query's words as written in the segment pointed at (a hit on page 5 never quotes page 2), or for a
        picture, in what OCR read in it."""
        if not terms:
            return None
        source = keyword.segment if keyword and segment_kind == "image" else segment_id
        found = self.store.db.execute("SELECT coalesce(original, body) FROM fts WHERE rowid = ?", (source,)).fetchone()
        return searchtext.snippet(found[0] or "", terms) if found else None

    def _moments(self, file: int, row_z: np.ndarray, shown: tuple[float, float | None], z_gate: float,
                 most: int = 3) -> list[tuple[float, float | None]]:
        """Other places in a long file that stand out for the query too (pages, passages, moments), best first,
        none overlapping the one shown or each other: "3 more moments in this video". Each must stand out as the
        file's best does: on the best-of-n scale."""
        rows = np.flatnonzero(self.row_file == file)
        if len(rows) < 2:
            return []
        n = len(rows) if self.ranking.sidak else 1
        locations = dict(self.store.db.execute(
            f"SELECT id, json_array(loc, loc_end) FROM segments WHERE id IN "
            f"({','.join(str(int(self.row_segment[r])) for r in rows)})"))
        taken = [shown]
        out: list[tuple[float, float | None]] = []
        for row in rows[np.argsort(-row_z[rows])]:
            if len(out) >= most or sidak(float(row_z[row]), n) < max(z_gate, 0):
                break
            loc, loc_end = json.loads(locations[int(self.row_segment[row])])
            if loc is None or any(_overlaps((loc, loc_end), other) for other in taken):
                continue
            taken.append((loc, loc_end))
            out.append((loc, loc_end))
        return out


def text_of(store: Store, segment_id: int) -> str | None:
    """A segment's words as written (a page, a passage, what OCR read)."""
    row = store.db.execute("SELECT coalesce(original, body) FROM fts WHERE rowid = ?", (segment_id,)).fetchone()
    return row[0] if row else None


def _overlaps(a: tuple[float, float | None], b: tuple[float, float | None]) -> bool:
    """Places overlap when their spans do; a page or passage (no end) only overlaps itself."""
    a_end, b_end = a[1] if a[1] is not None else a[0], b[1] if b[1] is not None else b[0]
    return a[0] <= b_end and b[0] <= a_end and (a[1] is not None or b[1] is not None or a[0] == b[0])
