# How Dowse works

The engine under the app: what reads your files, the model, the index, and how results are ranked, with the measurements behind each choice. For using the app, see [using-dowse.md](using-dowse.md).

## The pipeline

```
your folders ─► crawler (rules: no AppData, no keys, no OneDrive online-only files, no code projects)
             ─► readers: Pillow (+HEIC), PDFium, docx/pptx/xlsx/odt/odp/ods/html/rtf/eml/epub/csv and scripts,
                 Windows OCR for screenshots, scans and the words in photos,
                 FFmpeg (PyAV) for sound (30 s windows every 25 s) and video (a frame per shot + the soundtrack)
             ─► llama-server.exe (llama.cpp's official build: Vulkan, CUDA or CPU), /v1/embeddings
             ─► one SQLite file: an fp16 vector per picture, page, passage, video frame and 30 s of sound
                 + an FTS5 keyword index
your words   ─► the same model (text part only, ~310 MB) ─► z-scored cosine per modality + exact-word coverage
```

- **No compiling.** ggml-org publishes Windows builds for every backend, and the stock server embeds text, images
  and audio for EmbeddingGemma 2. `dowse setup` downloads the build that fits this PC.
- **The right GPU.** llama.cpp lists an AMD APU's 16 GB of shared memory above a 12 GB RTX card, so the GPU is
  picked by the dedicated VRAM Windows records for it (2 GB or more, otherwise the CPU).
- **Pictures sized like the model's own processor.** Images are resized for their token budget exactly as
  EmbeddingGemma 2's image processor does it (Pillow bicubic), and the server takes them as they are. The same input
  makes the same vector on every backend: CPU against Vulkan, cosine ≥ 0.9994 on all reference inputs.
- **Sound and video.** The model hears at most 30 s of one input, so sound is cut into overlapping windows and every
  match points to its moment. Silent windows are skipped. Video decodes only keyframes (94 s of 1440p60 in 1.7 s)
  and seeks into gaps longer than 15 s, so a screen recording with one keyframe a minute still gets a frame every
  few seconds. One frame per shot is kept (8×8 average hash).
- **Music tags.** `extract/music_tags.py` reads title, artist, album, album artist, track, date/year and genre from
  audio metadata through TinyTag (MIT, read-only). Values are saved as `music_tags` file info and indexed as one untimed keyword-only segment, even with a
  text-only model or when a song is too short/silent to embed. "Why this result?" attributes matching words to
  **Music tags**, not to a transcript of the sound. Metadata reads are capped at 1 MiB and 8,192 I/O operations;
  kept values at 256 characters. Cover art is skipped by seeking, so a large cover doesn't discard small text tags.
  Missing, broken or over-budget metadata doesn't prevent sound indexing. Multiple artists are joined. The same
  reader handles MP3, FLAC, M4A, Ogg and WAV metadata. Audio reader version 3 adds tags to already indexed recordings
  without decoding/embedding their sound again, preserving the resume point of unfinished recordings. Tag candidates
  supplement, never replace, the usual keyword candidates; scores use the same rules as other text, without a music boost.
- **Long files, a step at a time.** A PDF over 50 pages is read 30 pages at a time, a document 40 passages at a
  time, a recording or video 10 minutes at a time (5 on a CPU). The first pass reads every waiting file's first step,
  so everything is searchable soon; the later pass reads the rest. Every step is saved in one transaction with
  where the next starts, so a run that stops goes on from there, and an index built in stops is identical to one
  built in one go (tested). Media and PDF reading holds one step in memory. Documents are read before being split
  into passages, with a 64 MiB input limit. For Office, OpenDocument and EPUB files, this is a shared limit on
  decompressed content and metadata, checked before each member is opened and enforced on the actual read too;
  unused embedded images aren't decompressed. A document over the limit is marked failed without stopping the
  rest of indexing. Readers don't write temporary copies of originals to disk.
- **Ranking that holds up with long files.** Meaning and words fused (meaning z + a word bonus), plus three corrections for
  what made long files and noise come up for unrelated queries (`searcher.py`, `scripts/ranking_experiment.py`):
  a file's best segment is read as the best of n (Šidák), the query's words must be found together and count by
  rarity, and vectors close to every query (hubs) are taken down by their similarity to a bank of everyday probes.
  Hits on long files point to their best page or moment and list the next ones ("also p.3, p.1").
- **Live.** `dowse watch` keeps the index in step as files come and go: Windows' own change notifications
  (ReadDirectoryChangesW, one thread per folder), settled before anything is read (quiet for 2 s, the same size,
  and openable, so a download or a document being saved waits). A new photo is searchable a few seconds after it
  lands; a rename or move is re-pathed without embedding again; a deleted file or folder leaves the index. A burst
  (an unzip) or an overflow (changes faster than Windows' buffer) gets a crawl of its folder instead, and everything
  is crawled every 30 minutes anyway. A folder that goes away (an unplugged drive) keeps its files in the index
  until it's back. The model starts only when there's something to read and stops after 90 s without any, and it
  dies with Dowse however Dowse ends (a Windows job object), so it never holds the GPU on its own.
- **Names as titles.** The model reads every passage with its document's title, as it was trained to. A document
  without one is titled by its name when the name says something ("Grandma's soup.txt" is a list of vegetables;
  "IMG_1001" and "scan_0042" say nothing and aren't used).
- **What a query says besides its meaning.** A date said plainly ("from last summer", "in July 2025", "yesterday",
  "3 weeks ago") keeps results to that time: when a photo was taken, otherwise when the file last changed. A kind
  that leads the query ("photos of snow", "the pdf about…") or says where ("zebra in a video") keeps results to it.
  A word found nowhere is matched to the nearest word of a file or folder name ("sherlok" → sherlock).
- **Readers that improve reach old files.** Each kind's reader has a version; files read by an older one are read
  again in the background, searchable as they were until then. A photo read before photos' words were kept gets
  only its words read, not its picture embedded again.
- **CPU profile.** On a CPU a picture costs about 15× more, so photos get 70 tokens instead of 140, screenshots
  140 instead of 280, and videos a frame every 10 s (at most 400) instead of every 3 s (1,200). Screenshots' words
  come from OCR either way.

## Measured (RTX 4070 Ti / 12-thread CPU, Windows 10, llama.cpp b11574)

| | Vulkan GPU (4 slots) | CPU (2 slots) |
|---|---|---|
| search query | 15–40 ms | 12–15 ms |
| passage (~450 tokens) | 37 ms | 171 ms |
| picture @ 70 / 140 / 280 tokens | 28 / 52 / 100 ms | 369 / 789 / 1810 ms |
| testbed (33 files, incl. a podcast, a memo, a video) | 5.5 s | 16.8 s |

Ranking, `dowse eval evals/testbed.json` (47 queries; sound and video hits must land on the right moment; the
files include two public-domain novels, and the "whole index" adds 69 real screen recordings as distractors):

| | testbed alone | whole index |
|---|---|---|
| without the corrections | R@1 0.87, MRR 0.93, a book in 20 unrelated queries' results | R@1 0.81, MRR 0.88, 20 |
| Dowse | R@1 0.96, MRR 0.98, 2 | R@1 0.98, MRR 0.99, 2 |

A harder set, `evals/hard.json` (113 queries written once: vaguer wording, near neighbours, names, words in photos,
dates, kinds, spreadsheets and e-books, typos, other languages, and 8 things that aren't there), with the first set,
on the testbed read with the CPU's options: R@1 0.78 → **0.95**, MRR 0.82 → 0.97, after the changes above (by group:
typos 0.55 → 1.00, dates 0.40 → 0.90, kinds 0.67 → 1.00, names 0.71 → 1.00, new file types 0 → 0.91).

**Saying when nothing matches.** Something always stands out, so standing out can't say a file was found: "a
giraffe" brings up the zebra photo as far out as true matches go. Dowse asks the hit instead: is it better described
by the query than by everyday queries ("a horse in a field" describes the zebra better)? If not, and the words aren't
all in it either, the answer is "nothing clearly matches", with the closest files below. False alarms on the 8
queries with no answer: 8 → 1; right answers no longer said to be found: 4 of 152.

The remaining miss is honest ambiguity: "where they talk about sleep" ranks a Sherlock Holmes page about whistling
in one's sleep above the podcast's sleep segment. Its one miss ("a dog" ranks a lease that mentions dogs above the dog photo) is
the kind of trade-off the weights should be tuned for on a real-size eval, not on this set.
