"""A fixed bank of everyday queries, for telling hubs apart from matches.

Some vectors sit close to everything (a stretch of game noise, a black frame, a blank page): whatever the query,
they score high, and they'd come up for every search. A vector's typical similarity to these probes says how much of
its score is its own level rather than the query (`Searcher` subtracts it, as CSLS does for cross-modal retrieval).
The probes are deliberately generic and none is an eval query, so the evals measure the effect honestly.
"""

import base64

import numpy as np

from .embedder import DIM, Embedder, Prompts
from .store import Store

PROBES = [
    # Things and scenes
    "a cat sleeping", "a horse in a field", "birds on a branch", "a fish in an aquarium", "a car on a highway",
    "a bicycle against a wall", "an airplane in the sky", "a train station", "a boat on a lake", "a city skyline",
    "a mountain trail", "a sandy beach", "a forest in autumn", "a snowy street", "a sunset over the sea",
    "a garden with flowers", "a kitchen table", "a living room", "an office desk", "a classroom",
    "a birthday party", "a wedding", "a family photo", "a selfie", "a crowd at a concert", "a football stadium",
    "a plate of food", "a cup of coffee", "a bowl of salad", "a chocolate cake", "a glass of wine",
    "a smartphone", "a laptop keyboard", "a television", "a camera", "a pair of shoes", "a red dress",
    "a book on a shelf", "a painting in a museum", "a church", "a bridge over a river", "a map",
    # Documents and screens
    "an invoice", "a bank statement", "a tax form", "a contract", "a receipt", "a passport", "a boarding pass",
    "a resume", "a letter", "meeting notes", "a shopping list", "a recipe", "a train timetable", "a manual",
    "a scientific paper", "a news article", "a spreadsheet with numbers", "a chart", "a slide presentation",
    "an error message", "a login page", "a chat conversation", "an email", "source code", "a settings window",
    "a calendar", "a weather forecast", "a web page", "a video game menu",
    # Sound and topics
    "people talking", "a phone call", "a lecture", "a podcast interview", "a song with guitar", "piano music",
    "drums", "a choir singing", "applause", "rain falling", "wind", "traffic noise", "a dog barking",
    "birds singing", "a baby crying", "laughter", "an announcement", "a news broadcast", "silence",
    "health and fitness", "money and investing", "travel plans", "politics", "history", "science", "cooking",
    "sports results", "technology news", "school homework", "a job interview", "a doctor's appointment",
]


def ensure(store: Store, embedder: Embedder) -> np.ndarray:
    """The probes' vectors, embedded once per index (they're the same model's, so the index is where they live)."""
    model = getattr(embedder, "model", None)
    expected = model.vector_tag if model else store.meta("vectors")
    if store.meta("vectors") not in (None, expected):
        raise RuntimeError("The query model does not match the index")
    found = load(store)
    if found is not None:
        return found
    vectors = embedder.texts([Prompts.query(p) for p in PROBES]).astype(np.float32)
    store.set_model_meta("probes", f"{len(PROBES)}:" + base64.b64encode(vectors.tobytes()).decode("ascii"), expected)
    return vectors


def load(store: Store) -> np.ndarray | None:
    value = store.meta("probes")
    if not value:
        return None
    count, _, data = value.partition(":")
    if int(count) != len(PROBES):   # the bank changed: embed it again
        return None
    return np.frombuffer(base64.b64decode(data), np.float32).reshape(len(PROBES), DIM)
