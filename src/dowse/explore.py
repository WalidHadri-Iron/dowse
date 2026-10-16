"""The whole collection laid out by what's in it: aisles of files that are alike (beach photos near a video of waves
near a recording of the sea, whatever folder they're in), each named with words the model chose for it.

- A file is the mean of its vectors (its pictures, pages, passages, moments). EmbeddingGemma puts every kind in one
  space but in different regions of it (the modality gap): clustered as they are, photos would only find photos and
  pages pages. Each modality's mean is taken off its vectors first, so what's left is what they're about.
- The aisles you made come first (`aisles`: each file goes to the one it ranks highest for, `standing`); what they
  don't take is grouped automatically: k-means clusters (cosine, seeded: the same index gives the same map). Those
  groups are the suggestions, and an aisle each on the floor, except those you dismissed and when you'd rather not
  (`auto`): their files are in "Everything else".
- K-means puts every file in some group, also one like nothing left (a ski video once your aisles hold the other
  snow pictures). A group keeps a file only if it's like the rest of the group (`COHESION`, the cosine to the other
  members' mean: 0 is no more alike than any two files). The others are in "Everything else", and offered by an
  aisle of yours they nearly fit (`near`). Placed there at `aisles.NEAR` instead (as the files of a group you
  dismissed are), they were guesses: the demo's home-office photo in "Animals" (0.58), a dog on a beach in "Lisbon
  trip" (0.92), the library in "Animals" (0.84). On the demo library (2026-10-15, 60 files left by its 14 aisles, 4
  groups): members that belong scored 0.14 to 0.48 (Japanese articles and recordings, French poems, books, e-mails),
  those that didn't -0.05 to 0.09 (the ski video in "Calendar · spreadsheet", sea sounds among field photos, a
  thunderstorm among Japanese pages).
- An aisle's name is the two words of a fixed everyday vocabulary that are closest to it and to no other aisle as
  much (the vocabulary's own vectors, embedded once by the query model, centred the same way).
- Aisles are placed by their likeness (the first two principal axes of their centres), spread so they don't overlap,
  and each aisle's files spiral out from its middle, the most typical first.
"""

import base64
import hashlib
import math
from dataclasses import dataclass

import numpy as np

from .aisles import GATE, NEAR, Definition, Placement, dismissed as was_dismissed
from .embedder import DIM, Embedder, Prompts
from .store import Store

WORDS = """
beach sea waves sand sunset sunrise sky clouds rain snow mountains forest trees flowers garden park river lake
waterfall desert island city street buildings bridge night lights road car train airplane boat bicycle traffic
house kitchen bedroom office desk computer screen keyboard phone camera television game music guitar piano drums
concert singing speech talk interview podcast lecture meeting class school university book page text letter
document contract lease invoice receipt bill bank money tax form table chart graph spreadsheet presentation slides
diagram map plan drawing painting art photo portrait selfie family friends children baby wedding party birthday
holiday travel trip museum church food meal cooking recipe cake coffee drink restaurant market shopping clothes
shoes sport football running fitness doctor health medicine hospital animal dog cat horse bird fish insect wolf
nature ocean storm fire smoke water ice night dark light colors black white screenshot error message code
software website app settings login chat email news politics history science space stars moon sun planet
technology robot machine tools building construction work job resume notes list calendar time clock sound noise
voice laughter applause silence wind birdsong animals farm field village castle ruins statue sculpture fashion
""".split()

TARGET = 1200   # files shown on the map at most: beyond, each aisle shows its most typical ones and a count
COHESION = 0.1  # how much like the rest of its automatic group a file must be to stay in it


@dataclass
class Aisle:
    index: int
    label: str
    count: int
    x: float
    y: float
    radius: float
    id: str = ""           # yours: its id in the settings; automatic: "auto-<n>"
    custom: bool = False   # made by you
    about: str = ""        # yours: your description; automatic: the words that describe it best


def _file_vectors(store: Store) -> tuple[np.ndarray, np.ndarray]:
    """(file ids, one unit vector each): the mean of each file's vectors, every modality centred on its own mean."""
    rows = store.db.execute("SELECT s.file, s.modality, s.vec FROM segments s JOIN files f ON f.id = s.file "
                            "WHERE s.vec IS NOT NULL AND f.active = 1 ORDER BY s.file").fetchall()
    if not rows:
        return np.zeros(0, np.int64), np.zeros((0, DIM), np.float32)
    files = np.fromiter((r[0] for r in rows), np.int64, len(rows))
    modality = np.array([r[1] for r in rows])
    vectors = np.frombuffer(b"".join(r[2] for r in rows), "<f2").reshape(len(rows), DIM).astype(np.float32)
    for value in np.unique(modality):
        chosen = modality == value
        vectors[chosen] -= vectors[chosen].mean(axis=0)
    ids, start = np.unique(files, return_index=True)
    sums = np.add.reduceat(vectors, start, axis=0)
    norms = np.linalg.norm(sums, axis=1, keepdims=True)
    return ids, sums / np.maximum(norms, 1e-6)


def kmeans(points: np.ndarray, k: int, iterations: int = 30, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """(labels, unit centres): spherical k-means with k-means++ seeding."""
    rng = np.random.default_rng(seed)
    centres = [points[rng.integers(len(points))]]
    for _ in range(1, k):
        distance = 1 - np.max(points @ np.stack(centres).T, axis=1)
        weights = np.maximum(distance, 0) ** 2
        centres.append(points[rng.choice(len(points), p=weights / weights.sum())] if weights.sum() > 0
                       else points[rng.integers(len(points))])
    centres = np.stack(centres)
    labels = np.zeros(len(points), np.int64)
    for _ in range(iterations):
        new = np.argmax(points @ centres.T, axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for c in range(k):
            members = points[labels == c]
            if len(members):
                mean = members.sum(axis=0)
                centres[c] = mean / max(np.linalg.norm(mean), 1e-6)
    return labels, centres


def word_vectors(store: Store, embedder: Embedder | None, raw: bool = False) -> np.ndarray | None:
    """The vocabulary's vectors (embedded once per index and kept with it), centred (`raw`: as the model gave them);
    None without a model."""
    key = hashlib.sha1(" ".join(WORDS).encode()).hexdigest()[:12]
    model = getattr(embedder, "model", None)
    expected = model.vector_tag if model else store.meta("vectors")
    if store.meta("vectors") not in (None, expected):
        raise RuntimeError("The Explore model does not match the index")
    saved = store.meta("explore_words")
    if saved and saved.startswith(key + ":"):
        vectors = np.frombuffer(base64.b64decode(saved.split(":", 1)[1]), np.float32).reshape(len(WORDS), DIM)
    elif embedder is not None:
        vectors = embedder.texts([Prompts.query(w) for w in WORDS]).astype(np.float32)
        store.set_model_meta("explore_words", key + ":" + base64.b64encode(vectors.tobytes()).decode("ascii"), expected)
    else:
        return None
    if raw:
        return vectors
    vectors = vectors - vectors.mean(axis=0)
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-6)


def cohesive(vectors: np.ndarray, members: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(kept, loose): the members like the rest of their group (`COHESION`: each against the mean of the others),
    and those that aren't. A group of one has no rest to be like."""
    if len(members) < 2:
        return members[:0], members
    own = vectors[members]
    total = own.sum(axis=0)
    others = total[None, :] - own
    alike = np.einsum("ij,ij->i", own, others) / np.maximum(np.linalg.norm(others, axis=1), 1e-6)
    return members[alike >= COHESION], members[alike < COHESION]


def _names(centres: np.ndarray, words: np.ndarray | None) -> list[str]:
    if words is None:
        return [f"Aisle {i + 1}" for i in range(len(centres))]
    scores = centres @ words.T                                   # aisle × word
    specific = scores - scores.mean(axis=0, keepdims=True)       # what sets this aisle apart from the others
    names, used = [], set()
    for row, own in zip(specific, scores):
        order = np.argsort(-(row + 0.5 * own))
        picked = [WORDS[i] for i in order if WORDS[i] not in used][:2]
        used.update(picked[:1])                                  # no two aisles lead with the same word
        names.append(" · ".join(w.capitalize() if j == 0 else w for j, w in enumerate(picked)))
    return names


def _about(centre: np.ndarray, words: np.ndarray | None, count: int = 5) -> str:
    """An automatic aisle described in words, to start a suggestion's description from."""
    if words is None:
        return ""
    return ", ".join(WORDS[i] for i in np.argsort(-(words @ centre))[:count])


def files(store: Store) -> tuple[np.ndarray, np.ndarray]:
    """(file ids, one unit vector each): what Explore and the aisles compare (kept by the app until the index changes)."""
    with store.snapshot():
        return _file_vectors(store)


FAMILIES = {"image": "seen", "screenshot": "seen", "video": "seen", "pdf": "text", "doc": "text", "audio": "heard"}
SEEDS = 5   # the files a description finds first, whose likes then fill the aisle
SEARCHED = ("pdf", "doc", "audio")   # kinds whose rank also counts how a search for the description finds them
SEARCH_OFFSET = 1.0   # search's "found" level (z 2.0) lines up with the placing bar (1.0)
SEARCH_MOST = 20      # passages: a longer document is about what all of it is about, not its best passage
AGREE = 0.0           # how well the description must fit a file (z) for its likeness to the examples to count
                      # (a demo library's aisles: 294 of 319 files placed right at 0, 292 at 0.5, 293 at 1)


def standing(store: Store, embedder: Embedder | None, defined: list[Definition],
             vectors: tuple[np.ndarray, np.ndarray] | None = None,
             search=None) -> dict[str, dict[int, float]]:
    """How high each file ranks for each aisle of yours (from `aisles.NEAR` up only).

    The description finds a few clear examples, and the aisle takes the files that look like them: a sentence matches
    a recording (the mean of all its frames and sound) only weakly, but recordings of the same thing look alike.
    The description is the model's query vector, centred like the vocabulary and pulled toward pinned files; its best
    `SEEDS` files (pinned ones too) are the examples; a file's rank is the better of how well the description fits it
    and how like the examples it is (refined once from what that takes), where the description fits it at least as
    well as it fits an average file (a sunflower looks like a pizza photo, but not like "cooking recipes"). Ranks are standardised within each family of
    kinds (what's seen: pictures and recordings; text; sound): a recording is measured against pictures and
    recordings, not documents. Only the aisle's kinds (if it has some), and only the
    families its examples are in, take part. Empty without a model.

    Documents and recordings also count how a search for the description finds them (`search(text)`: file → z, the
    search's own scale, `Searcher.fit`), when that's higher: a document's best passage says what it's about better
    than the mean of all of them. Bills from one template and the statements that pay them look alike as wholes
    (electricity bills went to the bank aisle, statements to the home aisle); passage by passage, each went where
    it belongs (2026-10-14, the demo library: every bill, statement and payslip in its own aisle, with no pins).
    Only for documents of up to `SEARCH_MOST` passages: a book's best passage isn't what it's about. Every Project
    Gutenberg book ends with its licence, on tax-deductible donations, and that one passage put the demo's seven
    novels in "Taxes & insurance" (z 4.1 to 4.7, as high as the tax forms). The demo's own documents (bills,
    letters, notes, memos, podcasts) have 1 to 16 passages; its books and encyclopedia articles 20 to 436.

    Measured against hand-labelled files on a real collection (23 recordings and pictures showing people, of 103):
    the description alone, ranked among all files, found 5 (and 3 documents about people); ranked within kinds, 9 of
    19 taken; with the aisle kept to pictures and recordings, this takes 15 to 17, and nothing else."""
    raw = word_vectors(store, embedder, raw=True)
    if not defined or embedder is None or raw is None:
        return {}
    ids, matrix = vectors if vectors is not None else files(store)
    if not len(ids):
        return {}
    row_of = {int(f): i for i, f in enumerate(ids)}
    paths = {p: f for f, p in store.db.execute("SELECT id, path FROM files")}
    kinds = dict(store.db.execute("SELECT id, kind FROM files"))
    family = np.array([FAMILIES.get(kinds.get(int(f), ""), "other") for f in ids])
    kind = np.array([kinds.get(int(f), "") for f in ids])
    asked = embedder.texts([Prompts.query(d.text) for d in defined]).astype(np.float32) - raw.mean(axis=0)
    passages = dict(store.db.execute("SELECT file, COUNT(*) FROM segments WHERE vec IS NOT NULL GROUP BY file")) \
        if search is not None else {}

    def ranked(scores: np.ndarray, rows: np.ndarray, families: set[str]) -> np.ndarray:
        """`scores` standardised within each family, for `rows` in `families` (the rest: never)."""
        out = np.full(len(scores), -np.inf)
        for name in families:
            these = rows & (family == name)
            if these.sum() >= 2:
                out[these] = (scores[these] - scores[these].mean()) / max(float(scores[these].std()), 1e-6)
        return out

    out = {}
    for definition, meaning in zip(defined, asked):
        rows = np.isin(kind, definition.kinds) if definition.kinds else np.ones(len(ids), bool)
        meaning = meaning / max(np.linalg.norm(meaning), 1e-6)
        pinned = [row_of[paths[p]] for p in definition.pinned if p in paths and paths[p] in row_of]
        if pinned:
            pulled = matrix[pinned].sum(axis=0)
            meaning = meaning + pulled / max(np.linalg.norm(pulled), 1e-6)
            meaning /= max(np.linalg.norm(meaning), 1e-6)
        first = ranked(matrix @ meaning, rows, set(family[rows]))   # what the description finds by itself
        seeds = list(dict.fromkeys(pinned + [int(i) for i in np.argsort(-first)[:SEEDS] if first[i] > -np.inf]))
        if not seeds:
            out[definition.id] = {}
            continue
        families = set(family[seeds])
        members = seeds
        for _ in range(2):   # files like the examples, then like what they took
            centre = matrix[members].sum(axis=0)
            centre /= max(np.linalg.norm(centre), 1e-6)
            like = ranked(matrix @ centre, rows, families)
            rank = np.maximum(first, np.where(first >= AGREE, like, -np.inf))   # likeness counts where the words agree
            members = sorted(set(np.flatnonzero(rank >= GATE).tolist()) | set(pinned)) or seeds
        if search is not None:   # documents and recordings: how a search for the description finds them, too
            for file, z in search(definition.text).items():
                i = row_of.get(file)
                if i is not None and rows[i] and kind[i] in SEARCHED and passages.get(file, 0) <= SEARCH_MOST:
                    rank[i] = max(rank[i], z - SEARCH_OFFSET)
        out[definition.id] = {int(ids[i]): float(rank[i]) for i in np.flatnonzero(rank >= NEAR)}
    return out


def build(store: Store, embedder: Embedder | None = None, defined: list[Definition] = (),
          placement: Placement | None = None, auto: bool = True, dismissed: list[tuple[str, frozenset[str]]] = (),
          vectors: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
    """The map: {"aisles", "pieces", "membership", "near", "groups"}, in cell units (one cell holds one file).
    `defined` are your aisles, `placement` where their files go (`aisles.place`); the rest is grouped here, each
    group an aisle unless `auto` is off or it's one you `dismissed` (`aisles.dismissals`): then in "Everything else"."""
    if not store.vector_count():
        return {"aisles": [], "pieces": [], "total": 0, "membership": {}, "near": {}, "groups": []}
    model = getattr(embedder, "model", None)
    expected = model.vector_tag if model else store.meta("vectors")
    words = word_vectors(store, embedder)
    with store.snapshot():
        if store.meta("vectors") not in (None, expected):
            raise RuntimeError("The model changed while Explore was being prepared. Try again.")
        return _build_snapshot(store, words, list(defined), placement, auto, dismissed, vectors)


def _build_snapshot(store: Store, words: np.ndarray | None, defined: list[Definition] = (),
                    placement: Placement | None = None, auto: bool = True,
                    dismissed: list[tuple[str, frozenset[str]]] = (),
                    vectors_: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
    ids, vectors = vectors_ if vectors_ is not None else _file_vectors(store)
    if not len(ids):
        return {"aisles": [], "pieces": [], "total": 0, "membership": {}, "near": {}, "groups": []}
    row_of = {int(f): i for i, f in enumerate(ids)}
    rows = {r[0]: r[1:] for r in store.db.execute("SELECT id, path, kind FROM files")}

    # Yours first: each takes its home files, in the order they rank for it
    labels = np.full(len(ids), -1, np.int64)
    mine: dict[str, list[int]] = {}
    for definition in defined:
        mine[definition.id] = [row_of[f] for f in (placement.members(definition.id) if placement else [])
                               if f in row_of and placement.home.get(f) == definition.id]
        labels[mine[definition.id]] = 0   # (taken; the aisle's own number comes below)

    # The rest, grouped automatically: each group a suggestion, and an aisle unless it's not wanted on the floor
    rest = np.flatnonzero(labels < 0)
    groups, unsorted, kept, loose_ = [], [], [], []
    if len(rest):
        k = int(np.clip(round(math.sqrt(len(rest) / 3)), 1, 18)) if len(rest) >= 6 else 1
        found, _ = kmeans(vectors[rest], k)
        clusters = []
        for c in range(k):   # each group without what isn't like it (that finds an aisle below, or Everything else)
            members, loose = cohesive(vectors, rest[found == c])
            loose_.extend(loose.tolist())
            if len(members):
                clusters.append(members)
        centres_ = [vectors[m].sum(axis=0) / max(np.linalg.norm(vectors[m].sum(axis=0)), 1e-6) for m in clusters]
        taken = {d.name.lower() for d in defined}
        for c, name in enumerate(_names(np.stack(centres_), words) if clusters else []):
            members = clusters[c][np.argsort(-(vectors[clusters[c]] @ centres_[c]))]
            label = name if name.lower() not in taken else f"{name} ({c + 1})"
            groups.append({"id": f"auto-{c}", "label": label, "about": _about(centres_[c], words),
                           "count": len(members), "members": [rows[int(ids[r])][0] for r in members]})
            groups[-1]["dismissed"] = was_dismissed(label, groups[-1]["members"], list(dismissed))
            if not auto or groups[-1]["dismissed"]:
                unsorted.extend(members.tolist())
            else:
                kept.append((groups[-1], centres_[c], members))

    # What has no aisle on the floor goes to the aisle of yours it fits best, if it fits one at all (from NEAR, where
    # placing alone takes GATE): a file left by an aisle you removed, or by a group you dismissed, finds another of
    # yours before "Everything else"
    rescued: dict[str, list[int]] = {}
    if placement and unsorted:
        still = []
        for r in unsorted:
            file = int(ids[r])
            best = max(mine, key=lambda a: placement.rank.get(a, {}).get(file, -np.inf), default=None)
            if best is not None and placement.rank.get(best, {}).get(file, -np.inf) >= NEAR:
                mine[best].append(r)
                rescued.setdefault(best, []).append(file)
            else:
                still.append(r)
        unsorted = still
    unsorted += loose_   # like no group and placed by no aisle: offered where they nearly fit (`near`), not placed

    centres, names, order, abouts, aisle_ids = [], [], [], [], []
    for definition in defined:
        members = mine[definition.id]
        if not members:
            continue
        labels[members] = len(centres)
        mean = vectors[members].sum(axis=0)
        centres.append(mean / max(np.linalg.norm(mean), 1e-6))
        names.append(definition.name)
        order.append(np.array(members))
        abouts.append(definition.about)
        aisle_ids.append(definition.id)
    custom = len(centres)
    for group, centre, members in kept:
        labels[members] = len(centres)
        centres.append(centre)
        names.append(group["label"])
        order.append(members)
        abouts.append(group["about"])
        aisle_ids.append(group["id"])
    if unsorted:   # what's on the floor without an aisle of its own
        members = np.array(unsorted)
        labels[members] = len(centres)
        mean = vectors[members].sum(axis=0)
        centres.append(mean / max(np.linalg.norm(mean), 1e-6))
        names.append("Everything else")
        order.append(members)
        abouts.append("")
        aisle_ids.append("rest")
    centres = np.stack(centres)
    k = len(centres)

    # Aisles placed by likeness: the centres' first two principal axes, then spread until none overlap.
    counts = np.bincount(labels, minlength=k)
    shown = np.minimum(counts, np.maximum(4, np.round(counts * min(1.0, TARGET / len(ids))).astype(int)))
    radius = np.sqrt(shown) * 0.78 + 0.6
    if k >= 3:
        centred = centres - centres.mean(axis=0)
        _, _, axes = np.linalg.svd(centred, full_matrices=False)
        place = centred @ axes[:2].T
        place = place / max(np.abs(place).max(), 1e-6) * radius.sum() * 0.3
    else:
        place = np.array([[i * radius.max() * 2.4, 0.0] for i in range(k)])
    for _ in range(300):   # push apart until no two aisles overlap
        moved = False
        for a in range(k):
            for b in range(a + 1, k):
                delta = place[b] - place[a]
                distance = np.linalg.norm(delta) or 1e-3
                need = radius[a] + radius[b] + 0.9
                if distance < need:
                    push = (need - distance) / 2 * (delta / distance)
                    place[a] -= push
                    place[b] += push
                    moved = True
        place *= 0.995 if not moved else 1.0
        if not moved:
            break
    place -= place.mean(axis=0)

    aisles, pieces = [], []
    for c in range(k):
        typical = order[c][:shown[c]]
        aisles.append(Aisle(c, names[c], int(counts[c]), float(place[c, 0]), float(place[c, 1]), float(radius[c]),
                            aisle_ids[c], c < custom, abouts[c]))
        for n, member in enumerate(typical):   # a sunflower spiral: dense, round, the most typical at the middle
            angle = n * 2.39996
            r = math.sqrt(n + 0.5) * 0.78   # (half a turn out: from the middle, the first two cases overlapped)
            path, kind = rows[int(ids[member])]
            pieces.append({"path": path, "kind": kind, "aisle": c,
                           "x": float(place[c, 0] + r * math.cos(angle)), "y": float(place[c, 1] + r * math.sin(angle))})
    path = {f: rows[f][0] for f in rows}
    membership = {aisle_ids[c]: [rows[int(ids[r])][0] for r in order[c]] for c in range(k)}
    near = {}
    if placement:
        for definition in defined:   # an aisle of yours: its pinned files homed elsewhere too, and what might belong
            extra = rescued.get(definition.id, [])
            membership[definition.id] = [path[f] for f in placement.members(definition.id) + extra if f in path]
            near[definition.id] = [path[f] for f in placement.near.get(definition.id, []) if f in path and f not in extra]
    return {"aisles": [a.__dict__ for a in aisles], "pieces": pieces, "total": int(len(ids)),
            "membership": membership, "near": near, "groups": groups}
