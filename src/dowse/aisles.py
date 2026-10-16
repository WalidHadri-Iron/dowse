"""Aisles you make: a name and what belongs in it ("Housing: renting a flat, leases, landlords and rent"), kept in the
settings, and the files that go in each.

Every file is ranked for every aisle of yours (`explore.standing`: the description finds a few clear examples, and
the aisle takes the files like them where the description agrees; ranks are compared within kinds, pictures and
recordings together). A file goes to the one aisle it ranks highest for, from `GATE`; from `NEAR` it only might
belong and is offered. Pinned files are in whatever their rank (and pull the aisle's meaning toward them), kept-out
files never, and an aisle can be kept to some kinds of file. What no aisle of yours takes is grouped automatically.

Measured against hand-labelled files on a real collection (2026-10-13; 23 pictures and recordings showing people):
as a saved search ("stands out as a search result would"), "Pictures of people" took a CSV of contacts, two notes and
a script, and no recording: a broad topic fits many files moderately and none stands out. Ranked among all files by
its description, 5 of the 23 (and 3 documents about people). Kept to pictures and recordings, with examples and
likeness: 16 of 23, nothing else. Kinds matter (without them, documents about people come in), so the editor picks
them from the words ("pictures of…"). A vocabulary-rank rule failed for broad aisles ("Animals" got no animal photo).

With many aisles at once (the demo library's twelve, 2026-10-14), each file is claimed by the one it ranks highest
for, so the placing bar can be lower: at 1.5, most electricity bills (1.1 to 1.4) and recordings of people stayed
out; at 1.0, the bills aisle takes every electricity and water bill and "Pictures of people" 17 of the 23, with
competing aisles keeping the rest where they belong.
"""

import uuid
from dataclasses import dataclass, field

GATE = 1.0   # how high a file must rank for an aisle (standard deviations above its kind's mean) to be placed in it
NEAR = 0.5   # from here to GATE it might belong: offered, not placed
KINDS = ("image", "screenshot", "pdf", "doc", "audio", "video")


@dataclass
class Definition:
    id: str
    name: str
    about: str = ""                                    # what belongs here, in the user's words
    pinned: list[str] = field(default_factory=list)    # files put here by hand, whatever their rank
    excluded: list[str] = field(default_factory=list)  # files kept out by hand
    kinds: list[str] = field(default_factory=list)     # only these kinds of file (none: any)

    @property
    def text(self) -> str:
        """What the model is asked: the name and the description together."""
        return f"{self.name}: {self.about}" if self.about.strip() else self.name

    def json(self) -> dict:
        return {"id": self.id, "name": self.name, "about": self.about, "pinned": list(self.pinned),
                "excluded": list(self.excluded), "kinds": list(self.kinds)}

    @classmethod
    def from_json(cls, data: dict) -> "Definition":
        name = " ".join(str(data.get("name", "")).split())[:80]
        if not name:
            raise ValueError("an aisle needs a name")
        kinds = [k for k in dict.fromkeys(str(k) for k in data.get("kinds") or []) if k in KINDS]
        return cls(str(data.get("id") or uuid.uuid4().hex[:12]), name, str(data.get("about", "")).strip()[:400],
                   _paths(data.get("pinned")), _paths(data.get("excluded")), kinds)


def _paths(value) -> list[str]:
    return list(dict.fromkeys(str(p) for p in value or [] if p))


def definitions(settings: dict) -> list[Definition]:
    """The user's aisles, in their order (a damaged entry is skipped, not fatal)."""
    out = []
    for data in settings.get("aisles", []):
        try:
            out.append(Definition.from_json(data))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def saved(settings: dict, definition: Definition) -> list[dict]:
    """The settings' aisles with `definition` replacing the one of its id, or added at the end."""
    current = definitions(settings)
    if any(d.id == definition.id for d in current):
        current = [definition if d.id == definition.id else d for d in current]
    else:
        current.append(definition)
    return [d.json() for d in current]


@dataclass
class Placement:
    home: dict[int, str]                        # file id → the aisle it's in (on the map, in the list)
    rank: dict[str, dict[int, float]]           # aisle → file → how high it ranks for it (NEAR and above)
    also: dict[str, list[int]]                  # aisle → files pinned there whose home is another aisle
    near: dict[str, list[int]]                  # aisle → files that might belong (NEAR to GATE), not in it

    def members(self, aisle: str) -> list[int]:
        """The aisle's files, highest ranking first: its home files, then those pinned in it too."""
        rank = self.rank.get(aisle, {})
        home = [f for f, a in self.home.items() if a == aisle]
        return sorted(home, key=lambda f: -rank.get(f, GATE)) + self.also.get(aisle, [])


def place(defined: list[Definition], standing: dict[str, dict[int, float]], ids: dict[str, int],
          kinds: dict[int, str]) -> Placement:
    """Where each file goes among `defined`. `standing[aisle][file]` is how high the file ranks for the aisle
    (`explore.standing`); `ids` maps paths to file ids (pinned and kept-out files are kept by path), `kinds` file ids
    to their kind. Each file goes to the one aisle it ranks highest for."""
    ranks: dict[str, dict[int, float]] = {}
    pinned: dict[int, list[str]] = {}
    for definition in defined:
        out = {ids[p] for p in definition.excluded if p in ids}
        allowed = set(definition.kinds)
        ranks[definition.id] = {f: z for f, z in standing.get(definition.id, {}).items()
                                if z >= NEAR and f not in out and (not allowed or kinds.get(f) in allowed)}
        for path in definition.pinned:
            if path in ids and ids[path] not in out:
                pinned.setdefault(ids[path], []).append(definition.id)
    home: dict[int, str] = {}
    for file, aisles in pinned.items():   # pinned: in the pinned aisle it ranks highest for
        home[file] = max(aisles, key=lambda a: ranks[a].get(file, -1e9))
    for aisle, rank in ranks.items():      # the rest: the aisle it ranks highest for, from GATE
        for file, z in rank.items():
            if file in pinned or z < GATE:
                continue
            if file not in home or z > ranks[home[file]].get(file, -1e9):
                home[file] = aisle
    also = {a: [f for f, aisles in pinned.items() if a in aisles and home[f] != a] for a in ranks}
    near = {a: sorted((f for f, z in rank.items() if z < GATE and f not in home), key=lambda f: -rank[f])
            for a, rank in ranks.items()}
    return Placement(home, ranks, also, near)


def dismissals(settings: dict) -> list[tuple[str, frozenset[str]]]:
    """The suggestions you dismissed: (name, its files). Older settings kept names only."""
    out = []
    for item in settings.get("aisles_dismissed", []):
        if isinstance(item, str):
            out.append((item, frozenset()))
        elif isinstance(item, dict):
            out.append((str(item.get("label", "")), frozenset(str(p) for p in item.get("members", []))))
    return out


def dismissed(label: str, members: list[str], gone: list[tuple[str, frozenset[str]]]) -> bool:
    """Whether a group is one you dismissed: the same files, mostly (half of them or more in common), whatever the name
    it was given this time (the rest is grouped again whenever your aisles change, and named anew); an old dismissal
    without its files, by name."""
    files = set(members)
    for name, those in gone:
        if not those:
            if name == label:
                return True
        elif files and len(files & those) / len(files | those) >= 0.5:
            return True
    return False

