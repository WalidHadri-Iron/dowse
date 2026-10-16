"""This PC's runtime must make the reference vectors (tests/reference): the same inputs, the same vectors, on every
backend and every llama.cpp update. An index is only as good as its vectors staying put.

The inputs come from scripts/make_reference_inputs.py. When vectors change on purpose (a new model), bump
`VECTOR_VERSION` and write them anew: DOWSE_WRITE_REFERENCE=1 pytest tests/test_reference.py
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dowse import runtime
from dowse.embedder import Embedder, Prompts

pytestmark = pytest.mark.server

REFERENCE = Path(__file__).parent / "reference"
VECTORS = REFERENCE / "vectors.json"
VECTOR_VERSION = 1
AGREEMENT = 0.999   # runtimes that agree measure ≥ 0.9997; less is a change people would see in their results

LONG = " ".join(f"Entry {i}: the greenhouse thermometer read {15 + i % 9} degrees at noon, so the vents were opened "
                "and the tomatoes were watered before lunch." for i in range(1, 61))   # ~1,800 tokens
TEXTS = {
    "query": Prompts.query("update failed access denied"),
    "query-fr": Prompts.query("un lac entre les montagnes"),
    "query-bn": Prompts.query("সকালের ট্রেন"),
    "document": Prompts.document("Saturday: hike to the lake above the village, lunch at the hut, and back down "
                                 "before the evening bus.", title="Weekend plan"),
    "document-long": Prompts.document(LONG),
}
IMAGES = {"screen-280": ("screen.png", 280), "landscape-140": ("landscape.jpg", 140),
          "frame-70": ("landscape.jpg", 70)}
NAMES = [*TEXTS, *IMAGES, "speech"]


@pytest.fixture(scope="module")
def made(server) -> dict[str, np.ndarray]:
    with Embedder(server.url, model=server.model) as embedder:
        vectors = dict(zip(TEXTS, embedder.texts(list(TEXTS.values()))))
        for name, (file, budget) in IMAGES.items():
            vectors[name] = embedder.images([Image.open(REFERENCE / file)], budget)[0]
        vectors["speech"] = embedder.audio([(REFERENCE / "speech.wav").read_bytes()])[0]
    if os.environ.get("DOWSE_WRITE_REFERENCE") == "1":
        VECTORS.write_text(json.dumps({
            "vector_version": VECTOR_VERSION, "made_with": f"llama.cpp {runtime.TAG} ({server.choice.describe()})",
            "vectors": {name: [round(float(x), 7) for x in vector] for name, vector in vectors.items()},
        }, indent=1, sort_keys=True))
    return vectors


@pytest.mark.parametrize("name", NAMES)
def test_vector_matches_reference(made, name):
    stored = json.loads(VECTORS.read_text())
    assert stored["vector_version"] == VECTOR_VERSION
    vector = made[name]
    assert not isinstance(vector, Exception), vector
    assert abs(np.linalg.norm(vector) - 1) < 1e-3
    assert float(vector @ np.asarray(stored["vectors"][name], np.float32)) >= AGREEMENT


def test_the_references_say_what_they_show(made):
    """The vectors mean something: each query finds its own input (a broken runtime could still be consistent)."""
    def best(query: str) -> str:
        return max(["screen-280", "landscape-140", "speech", "document"], key=lambda n: float(made[query] @ made[n]))

    assert best("query") == "screen-280"
    assert best("query-fr") == "landscape-140"
    assert best("query-bn") == "speech"
