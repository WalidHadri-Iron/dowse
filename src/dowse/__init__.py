"""Dowse: search your files by what is in them."""

import os

__version__ = "0.1.0"

# numpy's BLAS commits a buffer per processor thread when it's imported (750 MB on a 24-thread CPU). Dowse's one
# matrix product a query is bound by converting fp16 vectors, not by threads: one is as fast (measured: 157 ms against
# 153 ms over 100k vectors) and commits 197 MB instead of 936. Set before anything imports numpy.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
