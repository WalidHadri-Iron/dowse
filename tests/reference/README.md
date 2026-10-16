Inputs of the reference-vector test (`tests/test_reference.py`), made by `scripts/make_reference_inputs.py`, and
the vectors this pipeline makes for them (`vectors.json`, vector version 1). The test checks that every backend and
every llama.cpp update makes the same vectors (cosine ≥ 0.999), so an update that would change everyone's results is
caught before it ships.
