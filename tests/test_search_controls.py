"""Removable inferred restrictions and explanations anchored to actual indexed sources."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from dowse import dates, search_controls
from dowse.desktop.app import hit_json
from dowse.rules import Kind
from dowse.searcher import Hit, Searcher
from dowse.store import Modality, NewSegment, Segment, Store
from test_desktop import app, request
from test_index import unit


def search(app, **body):
    status, _, raw = request(app, "POST", "/api/search", body)
    assert status == 200, raw
    return json.loads(raw)


def test_inferred_filters_are_visible_even_when_there_are_no_results(app):
    running, folder, _ = app
    answer = search(running.server, q="photos banana from last summer", within=str(folder))
    assert not answer["hits"]
    assert [(f["id"], f["source"]) for f in answer["filters"]] == [
        ("folder", "manual"), ("kind", "query"), ("date", "query")]
    assert answer["filters"][1]["kinds"] == ["image"]
    assert answer["filters"][2]["from"].endswith("-06-01")
    assert answer["terms"] == ["banana"]


def test_removing_inferred_chips_broadens_search_without_editing_query(app):
    running, _, _ = app
    query = "photos banana from last summer"
    dated = search(running.server, q=query, ignoredFilters=["date"])
    assert [f["id"] for f in dated["filters"]] == ["kind"] and not dated["hits"]
    unfiltered = search(running.server, q=query, ignoredFilters=["date", "kind"])
    assert unfiltered["query"] == query and unfiltered["when"] is None and not unfiltered["filters"]
    assert {Path(h["path"]).name for h in unfiltered["hits"]} == {"cake.txt", "recipe.txt"}
    ordinary = search(running.server, q="banana")
    # The controls do not alter the scoring/ranking formula.
    assert [(h["path"], h["score"]) for h in unfiltered["hits"]] == [
        (h["path"], h["score"]) for h in ordinary["hits"]]


def test_manual_type_and_date_override_inference_and_can_be_removed(app):
    running, _, _ = app
    answer = search(running.server, q="photos banana from last summer", kinds=["doc"],
                    date={"from": "1970-01-01", "to": "2099-12-30"})
    assert len(answer["hits"]) == 2
    assert all(f["source"] == "manual" for f in answer["filters"])
    assert answer["filters"][0]["kinds"] == ["doc"]
    removed = search(running.server, q=answer["query"], ignoredFilters=["kind", "date"])
    assert not removed["filters"] and len(removed["hits"]) == 2


@pytest.mark.parametrize("body", [
    {"date": {"from": "2026-10-15", "to": "2026-10-01"}},
    {"date": {"from": "2026-02-30"}}, {"date": {}}, {"date": "yesterday"},
    {"ignoredFilters": ["folder"]}, {"ignoredFilters": "date"}, {"ignoredFilters": {}},
    {"kinds": ["unknown"]},
])
def test_invalid_controls_are_bad_requests_not_server_crashes(app, body):
    running, _, _ = app
    status, _, raw = request(running.server, "POST", "/api/search", {"q": "banana", **body})
    assert status == 400 and "error" in json.loads(raw)


def test_filters_work_without_a_query_or_awake_model(app):
    running, folder, _ = app
    answer = search(running.server, q="", kinds=["doc"], within=str(folder))
    assert len(answer["hits"]) == 2 and not answer["meaning"]
    recent = json.loads(request(running.server, "GET", "/api/recent?kinds=doc&from=1970-01-01&to=2099-12-30")[2])
    assert len(recent["hits"]) == 3 and all(h["kind"] == "doc" for h in recent["hits"])
    recent = json.loads(request(running.server, "GET", "/api/recent?to=1970-01-01")[2])
    assert recent["hits"] == []


def test_explanations_contain_observed_words_and_real_locations(app):
    running, _, _ = app
    answer = search(running.server, q="banana")
    for hit in answer["hits"]:
        evidence = hit["evidence"]
        assert any(e["source"] == "text" and "banana" in e["words"] and e["place"] == "Passage 1" for e in evidence)
        assert not any(e["source"] == "semantic" for e in evidence)  # words-only search
        assert all("score" not in e and "confidence" not in e for e in evidence)
    cake = next(h for h in search(running.server, q="cake")["hits"] if h["name"] == "cake.txt")
    assert any(e["source"] == "filename" and e["words"] == ["cake"] for e in cake["evidence"])


def test_page_evidence_never_borrows_words_from_a_different_page(tmp_path):
    store = Store(tmp_path / "evidence.sqlite")
    file = store.add_file(str(tmp_path / "paper.pdf"), Kind.PDF, 10, 100)
    store.add_segments(file, [
        NewSegment(Segment.PAGE, Modality.TEXT, unit(45), loc=1, body="pineapple on the first page"),
        NewSegment(Segment.PAGE, Modality.TEXT, unit(7), loc=5, body="a tropical orchard"),
    ])
    for i in range(20, 50):
        other = store.add_file(str(tmp_path / f"other-{i}.txt"), Kind.DOC, 10, 100)
        store.add_segments(other, [NewSegment(Segment.CHUNK, Modality.TEXT, unit(i), loc=0, body="other words")])
    hit = next(h for h in Searcher(store).search("pineapple", unit(7)) if h.path.endswith("paper.pdf"))
    assert hit.loc == 5
    evidence = hit_json(hit, 0, 1)["evidence"]
    assert any(e["source"] == "semantic" and e["place"] == "Page 5" for e in evidence)
    assert not any("pineapple" in e["words"] for e in evidence)
    store.close()


def test_spelling_and_prefix_explanations_use_actual_words_not_the_query(tmp_path):
    store = Store(tmp_path / "evidence.sqlite")
    file = store.add_file(str(tmp_path / "sherlock.txt"), Kind.DOC, 10, 100)
    store.add_segments(file, [NewSegment(Segment.CHUNK, Modality.TEXT, loc=0, body="walnuts and bread")])
    hits = Searcher(store).search("sherlok", None)
    assert any(e["source"] == "filename" and e["words"] == ["sherlock"] for e in hits[0].evidence)
    hits = Searcher(store).search("wal", None, prefix_last=True)
    assert any(e["source"] == "text" and e["words"] == ["walnuts"] for e in hits[0].evidence)
    store.close()


def test_manual_date_and_inferred_date_use_the_same_photo_capture_time(tmp_path):
    store = Store(tmp_path / "photos.sqlite")
    for i, taken in enumerate(("2025:07:01 12:00:00", "2026:07:01 12:00:00")):
        file = store.add_file(str(tmp_path / f"photo-{i}.jpg"), Kind.IMAGE, 10, datetime(2026, 10, 14).timestamp())
        store.add_segments(file, [NewSegment(Segment.IMAGE, Modality.IMAGE, unit(7))])
        store.finish(file, "done", info={"taken": taken})
    searcher = Searcher(store)
    inferred = searcher.search("photos from July 2025", unit(7))
    span, _ = search_controls.resolve("photos", frozenset(), None, [], {"from": "2025-07-01", "to": "2025-07-31"})
    manual = searcher.search("photos", unit(7), date_span=span)
    assert [(h.path, h.score) for h in inferred] == [(h.path, h.score) for h in manual]
    assert len(manual) == 1 and manual[0].path.endswith("photo-0.jpg")
    # Filter-only searches work while the model is asleep, too.
    assert len(searcher.search("from July 2025", None)) == 1
    assert len(searcher.search("from July 2025", None, infer_date=False)) == 2
    store.close()


def test_timestamp_evidence_is_a_real_segment_range_not_an_invented_transcript():
    hit = Hit("talk.mp3", Kind.AUDIO, 9, 3, .4, None, "audio", 80, 110, None, 100, 10, {},
              evidence=[{"source": "semantic", "modality": "audio", "words": [], "loc": 80, "locEnd": 110}])
    assert hit_json(hit, 0, 1)["evidence"] == [{"source": "semantic", "modality": "audio", "words": [],
                                             "loc": 80, "locEnd": 110, "place": "1:20–1:50"}]


def test_epoch_date_filters_work_in_positive_offset_windows_timezones():
    span, filters = search_controls.resolve("from 1970", frozenset(), None, [], None)
    assert span is not None and filters[-1]["from"] == "1970-01-01"
    assert filters[-1]["to"] == "1970-12-31"
