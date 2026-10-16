import hashlib
import json

import httpx
import numpy as np
import pytest

from dowse import modelfiles, probes, runtime
from dowse.desktop.indexing import Config
from dowse.embedder import Embedder, EmbedError
from dowse.indexer import Indexer
from dowse.reading import Options, steps
from dowse.rules import Kind
from dowse.selection import Selection
from dowse.store import FileRow, Store
from test_index import FakeEmbedder
from test_selection import touch


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWSE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_models_have_distinct_spaces_and_pinned_downloads(home):
    full, text = modelfiles.MODELS
    assert full.vector_tag != text.vector_tag and full.vector_tag.endswith("/v1")
    assert full.files == modelfiles.ALL and len(text.files) == 1
    assert text.text.url.endswith(f"/{text.text.revision}/{text.text.name}")
    assert len(text.text.sha256) == 64
    assert not text.media and full.media


def test_runtime_uses_the_selected_weights_and_no_projector_for_text_models(home):
    full, text = modelfiles.MODELS
    for profile, media in [(full, True), (text, False)]:
        server = runtime.Server(runtime.Choice("cpu", None), model=profile)
        command = server.command()
        assert command[command.index("-m") + 1].endswith(profile.text.name)
        assert ("--mmproj" in command) == media
        assert ("--no-mmproj" in command) != media
    assert "--no-mmproj" in runtime.Server(runtime.Choice("cpu", None), model=full, text_only=True).command()


def test_existing_model_files_are_verified_before_activation(home):
    content = b"unit test weights"
    file = modelfiles.ModelFile("stand-in.gguf", len(content), hashlib.sha256(content).hexdigest())
    profile = modelfiles.Model("stand-in", "Stand-in", "Testing only", file)
    touch(modelfiles.path(file), content)
    modelfiles.verify(profile)
    touch(modelfiles.path(file), b"different weights")
    with pytest.raises(OSError, match="checksum"):
        modelfiles.verify(profile)


def test_text_model_does_not_receive_media_requests_and_rejects_invalid_vectors(home):
    with Embedder("http://stand-in", model=modelfiles.MODELS[1]) as embedder:
        assert embedder.media([{"content": []}, {"content": []}]) == [None, None]
        embedder.client.close()
        embedder.client = httpx.Client(base_url="http://stand-in", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=json.dumps({"data": [{"index": 0, "embedding": [float("nan")] * 768}]}).encode())))
        with pytest.raises(EmbedError, match="non-finite"):
            embedder.query("a document")


@pytest.mark.parametrize("kind", [Kind.IMAGE, Kind.AUDIO, Kind.VIDEO])
def test_text_only_media_files_are_name_searchable_without_reading_the_contents(home, kind):
    file = FileRow(1, "a path that does not exist", kind, 10, 1, "pending", {})
    result = list(steps(file, Options(embed_media=False)))
    assert len(result) == 1 and result[0].keyword_only and not result[0].pieces
    config = Config(runtime.Choice("cpu", None), Selection(), model=modelfiles.MODELS[1].id)
    assert not config.options().embed_media


def test_indexer_refuses_mixing_models_and_atomically_replaces_old_text_on_the_first_pass(home, tmp_path):
    path = touch(tmp_path / "notes.txt", b"banana bread recipe")
    store = Store()
    indexer = Indexer(store)
    indexer.sync(Selection(files=(path,)))
    indexer.run(FakeEmbedder())
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind='chunk'").fetchone()[0] == 1
    model = modelfiles.MODELS[1]
    standin = FakeEmbedder()
    standin.model = model
    with pytest.raises(RuntimeError, match="choose the matching model"):
        indexer.run(standin)
    store.change_model(model)
    assert store.vector_count() == 0 and store.db.execute("SELECT COUNT(*) FROM fts WHERE body!=''").fetchone()[0]
    indexer.run(standin)
    assert store.db.execute("SELECT COUNT(*) FROM segments WHERE kind='chunk'").fetchone()[0] == 1
    assert store.vector_count() == 1
    store.close()


def test_late_old_model_workers_cannot_cache_vectors_in_the_new_index(home):
    store = Store()
    store.set_meta("vectors", modelfiles.MODELS[0].vector_tag)
    old = FakeEmbedder()
    old.model = modelfiles.MODELS[0]
    original = old.texts
    def texts(values):
        result = original(values)
        store.change_model(modelfiles.MODELS[1])
        return result
    old.texts = texts
    with pytest.raises(RuntimeError, match="model changed"):
        probes.ensure(store, old)
    assert store.meta("probes") is None
    store.close()
