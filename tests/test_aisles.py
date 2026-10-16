"""Aisles you make: definitions kept in the settings, files placed by how far they stand out for each description,
pinned and kept-out files, and Explore laying yours out first with the rest grouped automatically."""

from pathlib import Path

import numpy as np
import pytest

from dowse import aisles, explore
from dowse.aisles import GATE, NEAR, Definition
from dowse.embedder import DIM
from dowse.indexer import Indexer
from dowse.selection import Selection
from dowse.store import Store
from test_index import FakeEmbedder, word_vector
from test_selection import touch


def test_definitions_are_kept_in_the_settings():
    first = Definition.from_json({"name": "  Housing ", "about": "leases and rent", "pinned": ["a", "a", "b"]})
    assert first.name == "Housing" and first.pinned == ["a", "b"] and first.text == "Housing: leases and rent"
    with pytest.raises(ValueError):
        Definition.from_json({"name": "   "})
    settings = {"aisles": [first.json(), {"name": ""}]}           # a damaged one is skipped
    assert [d.name for d in aisles.definitions(settings)] == ["Housing"]
    renamed = Definition.from_json({**first.json(), "name": "Home"})
    settings["aisles"] = aisles.saved(settings, renamed)
    assert [d.name for d in aisles.definitions(settings)] == ["Home"]   # same id: replaced, not added
    settings["aisles"] = aisles.saved(settings, Definition.from_json({"name": "Recipes"}))
    assert [d.name for d in aisles.definitions(settings)] == ["Home", "Recipes"]


def test_each_file_goes_to_the_aisle_it_ranks_highest_for():
    ids = {f"f{i}": i for i in range(7)}
    kinds = {0: "pdf", 1: "doc", 2: "pdf", 3: "image", 4: "doc", 5: "doc", 6: "image"}
    high, just, between = GATE + 1.5, GATE + 0.3, (GATE + NEAR) / 2   # well in; just in; only might belong
    standing = {"h": {0: high, 1: just, 2: just, 3: NEAR - 1, 6: high}, "m": {1: high, 4: just, 5: between}}
    housing = Definition("h", "Housing", pinned=["f3"], excluded=["f2"])
    money = Definition("m", "Money", pinned=["f3"], kinds=["doc"])
    placed = aisles.place([housing, money], standing, ids, kinds)
    assert placed.home == {0: "h", 1: "m", 3: "h", 4: "m", 6: "h"}   # 1 ranks higher for Money; 3 is pinned in both
    assert placed.members("m") == [1, 4, 3]                           # pinned in Money too: listed there
    assert placed.near == {"h": [], "m": [5]}                          # NEAR to GATE: might belong; 2 is kept out
    only_docs = aisles.place([Definition("m", "Money", kinds=["doc"])], {"m": {0: high, 1: just}}, ids, kinds)
    assert only_docs.home == {1: "m"}                                  # the PDF ranks higher, but Money takes docs
    assert NEAR < GATE


class Asker:
    """A stand-in query model: a query's vector is its first word's (`word_vector`)."""
    model = None

    def texts(self, texts):
        return np.stack([word_vector(t.split("query: ")[-1].replace(":", " ").split()[0].lower()) for t in texts])


def _beaches_and_invoices(tmp_path) -> Store:
    root = tmp_path / "files"
    for i in range(12):
        touch(root / f"beach {i}.txt", f"beach sand sea {i}".encode())
        touch(root / f"invoice {i}.txt", f"invoice total due {i}".encode())
    store = Store(tmp_path / "index.sqlite")
    Indexer(store, workers=2).sync(Selection((str(root),)))
    Indexer(store, workers=2).run(FakeEmbedder())
    return store


def test_an_aisle_takes_what_ranks_high_for_it(tmp_path):
    store = _beaches_and_invoices(tmp_path)
    defined = [Definition("b", "beach")]
    standing = explore.standing(store, Asker(), defined)
    names = {Path(p).name.split()[0] for p, f in store.db.execute("SELECT path, id FROM files") if f in standing["b"]}
    assert names == {"beach"}                                          # the invoices rank low for it


def test_explore_lays_out_yours_first_and_groups_the_rest(tmp_path):
    store = _beaches_and_invoices(tmp_path)
    ids = dict((path, file) for file, path in store.db.execute("SELECT id, path FROM files"))
    kinds = dict(store.db.execute("SELECT id, kind FROM files"))
    beaches = {f: GATE + 2 for p, f in ids.items() if Path(p).name.startswith("beach")}
    defined = [Definition("b", "Seaside", "the beach")]
    placement = aisles.place(defined, {"b": beaches}, ids, kinds)
    built = explore.build(store, FakeEmbedder(), defined, placement)
    first = built["aisles"][0]
    assert (first["label"], first["custom"], first["id"], first["count"]) == ("Seaside", True, "b", 12)
    assert all(not a["custom"] and a["id"].startswith("auto-") for a in built["aisles"][1:])
    assert {Path(p).name.split()[0] for p in built["membership"]["b"]} == {"beach"}
    rest = {Path(p).name.split()[0] for a in built["aisles"][1:] for p in built["membership"][a["id"]]}
    assert rest == {"invoice"} and all(g["about"] and g["members"] for g in built["groups"])   # suggestions
    # Groups you dismissed, or no groups at all: the rest is in "Everything else"
    dismissed = [(g["label"], frozenset(g["members"])) for g in built["groups"]]
    for floor in (explore.build(store, FakeEmbedder(), defined, placement, dismissed=dismissed),
                  explore.build(store, FakeEmbedder(), defined, placement, auto=False)):
        assert [a["label"] for a in floor["aisles"]] == ["Seaside", "Everything else"]
        assert floor["aisles"][1]["count"] == 12 and floor["groups"]   # still offered as suggestions


def test_a_dismissed_group_stays_dismissed_under_another_name():
    gone = aisles.dismissals({"aisles_dismissed": ["Old · name", {"label": "Text · document", "members": ["a", "b", "c"]}]})
    assert aisles.dismissed("Old · name", ["x"], gone)                     # an old dismissal: by its name
    assert aisles.dismissed("Work · notes", ["a", "b", "d"], gone)         # half its files or more in common
    assert not aisles.dismissed("Work · notes", ["a", "d", "e", "f"], gone)


def test_files_without_an_aisle_find_the_one_they_fit_before_everything_else(tmp_path):
    store = _beaches_and_invoices(tmp_path)
    ids = dict((path, file) for file, path in store.db.execute("SELECT id, path FROM files"))
    kinds = dict(store.db.execute("SELECT id, kind FROM files"))
    beaches = sorted(f for p, f in ids.items() if Path(p).name.startswith("beach"))
    # Six beaches fit clearly; the other six only somewhat (as an aisle's files do once it's removed)
    standing = {"b": {f: (GATE + 2 if i < 6 else (GATE + NEAR) / 2) for i, f in enumerate(beaches)}}
    defined = [Definition("b", "Seaside", "the beach")]
    placement = aisles.place(defined, standing, ids, kinds)
    assert len([f for f, a in placement.home.items() if a == "b"]) == 6
    floor = explore.build(store, FakeEmbedder(), defined, placement, auto=False)
    by = {a["label"]: a["count"] for a in floor["aisles"]}
    assert by == {"Seaside": 12, "Everything else": 12}   # the six that fit somewhat joined it; the invoices didn't
    assert len(floor["membership"]["b"]) == 12 and not floor["near"]["b"]
    grouped = explore.build(store, FakeEmbedder(), defined, placement)   # groups kept: they have a place already
    assert {a["label"]: a["count"] for a in grouped["aisles"]}["Seaside"] == 6


def test_documents_also_count_how_a_search_for_the_description_finds_them(tmp_path):
    store = _beaches_and_invoices(tmp_path)
    ids = dict((path, file) for file, path in store.db.execute("SELECT id, path FROM files"))
    invoices = {f for p, f in ids.items() if Path(p).name.startswith("invoice")}
    defined = [Definition("m", "beach")]   # its words find the beaches; a search finds the invoices too
    found = explore.standing(store, Asker(), defined, search=lambda text: {f: 6.0 for f in invoices})
    assert invoices <= set(found["m"]) and all(found["m"][f] == 6.0 - explore.SEARCH_OFFSET for f in invoices)
    assert explore.standing(store, Asker(), defined, search=lambda text: {}) == explore.standing(store, Asker(), defined)


def test_a_group_keeps_only_files_like_the_rest_of_it():
    """K-means puts every file somewhere: one like nothing else left (a ski video among e-mails) leaves its group."""
    rng = np.random.default_rng(3)
    topic = rng.standard_normal(DIM)
    alike = np.stack([topic + 0.6 * rng.standard_normal(DIM) for _ in range(5)])
    stray = rng.standard_normal(DIM)[None, :]
    vectors = np.vstack([alike, stray])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    kept, loose = explore.cohesive(vectors, np.arange(6))
    assert kept.tolist() == [0, 1, 2, 3, 4] and loose.tolist() == [5]
    assert explore.cohesive(vectors, np.array([2]))[0].size == 0   # alone: nothing to be like


def test_a_file_like_no_group_is_in_everything_else_and_offered_where_it_nearly_fits(tmp_path, monkeypatch):
    store = _beaches_and_invoices(tmp_path)
    ids = dict((path, file) for file, path in store.db.execute("SELECT id, path FROM files"))
    kinds = dict(store.db.execute("SELECT id, kind FROM files"))
    path = {f: p for p, f in ids.items()}
    stray = next(f for p, f in ids.items() if Path(p).name.startswith("invoice"))
    stray_row = int(np.flatnonzero(explore.files(store)[0] == stray)[0])
    real = explore.cohesive

    def cohesive(vectors, members):   # this invoice is like nothing else in its group
        kept, loose = real(vectors, members)
        if stray_row in kept:
            kept, loose = kept[kept != stray_row], np.append(loose, stray_row)
        return kept, loose

    monkeypatch.setattr(explore, "cohesive", cohesive)
    beaches = {f: GATE + 2 for p, f in ids.items() if Path(p).name.startswith("beach")}
    defined = [Definition("b", "Seaside", "the beach")]
    built = explore.build(store, FakeEmbedder(), defined,
                          aisles.place(defined, {"b": {**beaches, stray: NEAR + 0.1}}, ids, kinds))
    assert all(path[stray] not in g["members"] for g in built["groups"])   # never in a group it isn't like
    assert path[stray] in built["membership"]["rest"]                     # a guess isn't a place: Everything else
    assert path[stray] in built["near"]["b"] and path[stray] not in built["membership"]["b"]   # offered there


def test_a_long_document_is_placed_by_all_of_it_not_its_best_passage(tmp_path):
    """A novel's one passage on tax-deductible donations (every Project Gutenberg licence) isn't what it's about."""
    store = _beaches_and_invoices(tmp_path)
    ids = dict((path, file) for file, path in store.db.execute("SELECT id, path FROM files"))
    invoice = next(f for p, f in ids.items() if Path(p).name.startswith("invoice"))
    defined = [Definition("t", "Taxes", "tax forms")]
    found = lambda text: {invoice: 9.0}   # noqa: E731 (search finds the invoice, very clearly)
    assert explore.standing(store, Asker(), defined, search=found)["t"][invoice] >= 8
    store.db.execute("INSERT INTO segments (file, kind, modality, vec, loc) SELECT file, kind, modality, vec, loc + 1000 "
                     "FROM segments WHERE file = ? AND vec IS NOT NULL", (invoice,))
    for _ in range(5):   # now it's long: 64 passages and more
        store.db.execute("INSERT INTO segments (file, kind, modality, vec, loc) SELECT file, kind, modality, vec, loc + 1 "
                         "FROM segments WHERE file = ? AND vec IS NOT NULL", (invoice,))
    assert explore.standing(store, Asker(), defined, search=found)["t"].get(invoice, 0) < 8
