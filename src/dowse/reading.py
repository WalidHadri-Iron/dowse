"""Files as steps: what to embed and what to keep for keyword search, a bounded piece of a file at a time.

Every kind reads the same way. `steps(file, options, start)` yields `Step`s, each carrying the `Piece`s it read and
the place the next step starts from. A picture is one step. A PDF is 30 pages a step, a document 40 passages, a
recording or a video 5–10 minutes. The indexer saves each step in one transaction and records `position`, so a long
file becomes searchable after its first step, and a run that stops goes on later from where it was. Memory holds
one step, whatever the file's size.

Readers run on worker threads. Everything a piece needs from the model (a prompted text, or an image or audio
request part, sized and encoded) is prepared here, so the indexer only embeds and saves.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePath

from . import searchtext
from .fmt import clock
from .embedder import Prompts, audio_part, image_part
from .extract import ExtractError, docs, flac_tags, id3v2, images, media, ocr, pdf
from .rules import NOTHING, Kind, Skip
from .store import FileRow, Modality, Segment

# What reading a kind keeps, by version: when it changes, files read before are read again (what they hold stays
# searchable until their new read is saved). 2: photos' words (OCR); documents and PDFs titled by their name when
# they have no title of their own. Audio 2: ID3v2 tags; 3: FLAC Vorbis comments and tag evidence.
READERS = {Kind.IMAGE: 2, Kind.PDF: 2, Kind.DOC: 2, Kind.AUDIO: 3}

# Words of file names that say nothing about what's in the file
GENERIC = frozenset("img dsc dscn pxl vid mvimg scan doc docs document documents untitled new copy file files image "
                    "photo picture screenshot capture recording final draft version page export download".split())
JUNK_TITLES = ("untitled", "document", "presentation", "title", "microsoft word - ", "microsoft powerpoint - ")


@dataclass
class Options:
    """How files are read. Vision token budgets: 70 / 140 / 280 / 560 / 1120."""
    photo_budget: int = 140
    screenshot_budget: int = 280
    page_budget: int = 280
    frame_budget: int = 70          # video frames: many of them, and a frame needs fewer details than a photo
    min_image_side: int = 256
    min_page_text: int = 200        # fewer characters: the page is embedded as a picture
    big_pdf_pages: int = 50         # a PDF up to this many pages is read in one step
    pdf_step: int = 30              # pages
    doc_step: int = 40              # passages (~72,000 characters)
    max_pages: int = 5000
    max_passages: int = 5000
    frame_interval: float = 3.0     # seconds between video frames at least (one per shot is kept)
    max_frames: int = 1200          # a file's frames at most: longer videos space them out
    audio_window: float = 30.0      # the model ignores audio past 30 s of one input
    audio_hop: float = 25.0
    media_step: float = 600.0       # seconds of a recording or video per step (a multiple of `audio_hop`)
    skip: Skip = NOTHING           # the file types the user chose not to search
    embed_media: bool = True       # text-only models retain filenames/OCR but do not decode sound/video for embeddings
    photo_ocr: bool = True          # the words in photos (a sign, a receipt, a whiteboard), for keyword search

    @classmethod
    def for_device(cls, gpu: bool, skip: Skip = NOTHING) -> "Options":
        """A CPU embeds a picture ~15× slower than a GPU (0.79 s vs 0.05 s at 140 tokens) and 30 s of sound a few
        times slower, so it gets fewer, smaller pictures and shorter steps. Screenshots keep more tokens: their words
        matter, and OCR adds them anyway."""
        if gpu:
            return cls(skip=skip)
        return cls(photo_budget=70, screenshot_budget=140, page_budget=140, frame_interval=10.0, max_frames=400,
                   media_step=300.0, skip=skip)


@dataclass
class Piece:
    """One segment-to-be: what the model embeds (`text` or `part`, or neither: keyword search only), and the words
    keyword search keeps."""
    kind: Segment
    modality: Modality = Modality.NONE   # what the vector will be
    text: str | None = None              # a prompted text, for a text vector
    part: dict | None = None             # an image or audio request part, for an image or audio vector
    loc: float | None = None
    loc_end: float | None = None
    body: str = ""


@dataclass
class Step:
    pieces: list[Piece]
    position: float | None     # where the next step starts; None: the file is read
    info: dict                 # what reading learned (pages, duration, title…): merged into the file's info
    detail: str | None = None  # for progress, or why there's nothing to read
    keyword_only: bool = False
    keep: bool = False         # what the file already holds stays: these pieces are added to it


def title_of(path: str, given: str | None) -> str | None:
    """What a document is called: its own title, or else its name when the name says something ("Grandma's soup",
    not "IMG_1001" or "scan_0042"). The model reads it with every passage, as it was trained to."""
    if given and not given.strip().lower().startswith(JUNK_TITLES):
        return given
    words = [w for w in re.split(r"[\s_\-.()\[\]]+", PurePath(path).stem) if w]
    if any(sum(c.isalpha() for c in w) >= 3 and w.lower() not in GENERIC for w in words):
        return " ".join(words)
    return None


def steps(file: FileRow, options: Options, start: float = 0) -> Iterator[Step]:
    """`file`'s steps from `start` (a page or passage number, or seconds), each one read as it's asked for, each
    marked with the version of its kind's reader."""
    version = READERS.get(file.kind, 1)
    for step in _steps(file, options, start):
        step.info["reader"] = version
        yield step


def _steps(file: FileRow, options: Options, start: float) -> Iterator[Step]:
    if file.kind is Kind.IMAGE and options.embed_media and file.info.get("width") and \
            file.info.get("reader", 1) < 2:   # a photo read before its words were: just its words
        yield _photo_words_only(file, options)
    elif not options.embed_media and file.kind is Kind.AUDIO:
        tags, info = _music_tags(file.path)
        yield Step(_tag_pieces(tags), None, info,
                   "This text-only model searches music tags and filenames, not sound", keyword_only=True)
    elif not options.embed_media and file.kind in (Kind.IMAGE, Kind.VIDEO):
        yield Step([], None, {}, "This text-only model searches this file by its name, not its contents", keyword_only=True)
    elif file.kind in (Kind.IMAGE, Kind.SCREENSHOT):
        yield _picture(file, options)
    elif file.kind is Kind.PDF:
        yield from _pdf(file, options, int(start))
    elif file.kind is Kind.DOC:
        yield from _document(file, options, int(start))
    elif file.kind is Kind.AUDIO:
        yield from _recording(file, options, start)
    elif file.kind is Kind.VIDEO:
        yield from _video(file, options, start)
    else:
        raise ExtractError(f"{file.kind} files aren't read")


def _picture(file: FileRow, options: Options) -> Step:
    if file.kind is Kind.IMAGE:
        picture = images.load(file.path, options.min_image_side)
        pieces = [Piece(Segment.IMAGE, Modality.IMAGE, part=image_part(picture.image, options.photo_budget))]
        if options.photo_ocr and (words := _photo_words(picture.image)):
            pieces.append(Piece(Segment.OCR, body=words))
    else:
        # Screenshots are read at full size: small UI text is what they're full of. Their words are for keyword
        # search only: the picture carries their meaning, and a vector of UI text would match too much.
        picture = images.load(file.path, options.min_image_side, max_side=4096)
        pieces = ([Piece(Segment.IMAGE, Modality.IMAGE, part=image_part(picture.image, options.screenshot_budget))]
                  if options.embed_media else [])
        if words := ocr.text(picture.image).strip():
            pieces.append(Piece(Segment.OCR, body=words))
    info = {"width": picture.width, "height": picture.height} | ({"taken": picture.taken} if picture.taken else {})
    return Step(pieces, None, info, keyword_only=not options.embed_media)


def _photo_words(image) -> str | None:
    """What OCR reads in a photo, when it's words: two of three letters or more (not the specks a texture makes).
    Keyword search only, as for screenshots: the picture carries the meaning."""
    words = ocr.text(image).strip()
    real = [w for w in words.split() if sum(c.isalpha() for c in w) >= 3]
    return words if len(real) >= 2 else None


def _photo_words_only(file: FileRow, options: Options) -> Step:
    picture = images.load(file.path, options.min_image_side)
    words = _photo_words(picture.image) if options.photo_ocr else None
    return Step([Piece(Segment.OCR, body=words)] if words else [], None, {}, keep=True)


def _pdf(file: FileRow, options: Options, start: int) -> Iterator[Step]:
    total = pdf.page_count(file.path)
    end = min(total, options.max_pages)
    step = end if start == 0 and total <= options.big_pdf_pages else options.pdf_step
    while start < end:
        stop = min(start + step, end)
        document = pdf.pages(file.path, range(start, stop), options.min_page_text)
        titled = title_of(file.path, document.title)
        pieces = []
        for page in document.pages:
            if page.image is not None:   # a scan or a slide: its picture, and what OCR reads on it for its words
                words = (page.text + "\n" + ocr.text(page.image)).strip()
                if options.embed_media:
                    pieces.append(Piece(Segment.PAGE_IMAGE, Modality.IMAGE, part=image_part(page.image, options.page_budget),
                                        loc=page.number, body=words))
                else:
                    text = Prompts.document(words, titled) if searchtext.has_words(words) else None
                    pieces.append(Piece(Segment.PAGE, Modality.TEXT if text else Modality.NONE, text=text,
                                        loc=page.number, body=words))
            elif page.text:
                text = Prompts.document(page.text, titled) if searchtext.has_words(page.text) else None
                pieces.append(Piece(Segment.PAGE, Modality.TEXT if text else Modality.NONE, text=text,
                                    loc=page.number, body=page.text))
        info = {"pages": total} | ({"title": document.title} if document.title else {})
        yield Step(pieces, stop if stop < end else None, info,
                   f"pages {start + 1}–{stop} of {total}" if stop < end or start else None)
        start, step = stop, options.pdf_step


def _document(file: FileRow, options: Options, start: int) -> Iterator[Step]:
    """The text is read once for all the steps that follow (a resumed read reads it once more)."""
    text, title = docs.read(file.path)
    titled = title_of(file.path, title)
    passages = searchtext.chunks(text, limit=options.max_passages)
    if not passages:
        yield Step([], None, {}, "no text")
        return
    info = {"passages": len(passages), "chars": len(text)} | ({"title": title} if title else {})
    while start < len(passages):
        stop = min(start + options.doc_step, len(passages))
        pieces = []
        for index in range(start, stop):
            passage = passages[index]
            prompted = Prompts.document(passage, titled) if searchtext.has_words(passage) else None
            pieces.append(Piece(Segment.CHUNK, Modality.TEXT if prompted else Modality.NONE, text=prompted,
                                loc=index, body=passage))
        yield Step(pieces, stop if stop < len(passages) else None, info,
                   f"passages {start + 1}–{stop} of {len(passages)}" if stop < len(passages) or start else None)
        start = stop


class _Peekable:
    def __init__(self, iterator):
        self.iterator, self.head, self.done = iterator, None, False

    def peek(self):
        if self.head is None and not self.done:
            self.head = next(self.iterator, None)
            self.done = self.head is None
        return self.head

    def pop(self):
        head, self.head = self.peek(), None
        return head


def _windows_until(windows: _Peekable, end: float | None) -> list[media.Window]:
    """The windows that start before `end` (all of them when None)."""
    out = []
    while (window := windows.peek()) is not None and (end is None or window.start < end):
        out.append(windows.pop())
    return out


def _sound_pieces(windows: list[media.Window]) -> list[Piece]:
    return [Piece(Segment.AUDIO, Modality.AUDIO, part=audio_part(w.wav), loc=w.start, loc_end=w.end)
            for w in windows]


def _span(start: float, stop: float, duration: float) -> str:
    return f"{clock(start)}–{clock(stop)} of {clock(duration)}"


def _tag_pieces(tags: dict[str, str]) -> list[Piece]:
    """One untimed, keyword-only segment; never attach a song title to an audio window."""
    return [Piece(Segment.TAG, body=" ".join(f"{name}: {value}" for name, value in tags.items()))] if tags else []


def _music_tags(path: str) -> tuple[dict[str, str], dict]:
    """Dispatch by container: keep each format's metadata parser independent.

    The per-format info key records which reader supplied the searchable words.
    Files without supported tags still follow normal sound indexing.
    """
    if path.lower().endswith(".flac"):
        tags = flac_tags.read(path)
        return tags, {"flac": tags} if tags else {}
    tags = id3v2.read(path)
    return tags, {"id3v2": tags} if tags else {}


def _recording(file: FileRow, options: Options, start: float) -> Iterator[Step]:
    # Read tags only on the first step; resumed recordings retain the first step's
    # metadata and keyword segment. Audio decoding is independent of tag parsing.
    tags, tag_info = _music_tags(file.path) if start == 0 else ({}, {})
    with media.open_media(file.path) as container:
        duration = media.duration_of(container)
    windows = _Peekable(media.sound(file.path, start, options.audio_window, options.audio_hop))
    info = {"duration": round(duration, 1)} | tag_info
    tag_pieces = _tag_pieces(tags)
    if windows.peek() is None:
        yield Step(tag_pieces, None, info, "silent or shorter than 2 s" if start == 0 else None)
        return
    while windows.peek() is not None:
        stop = start + options.media_step
        pieces = tag_pieces + _sound_pieces(_windows_until(windows, stop))
        tag_pieces = []
        more = windows.peek() is not None
        yield Step(pieces, stop if more else None, info,
                   _span(start, stop, duration) if more or start else None)
        start = stop


def _video(file: FileRow, options: Options, start: float) -> Iterator[Step]:
    """Frames and sound a stretch of time at a time, both from the same reader position. Shots are told apart across
    steps too: the last frame's hash is kept in the file's info."""
    with media.FrameReader(file.path) as reader:
        duration = reader.duration
        info = {"duration": round(duration, 1), "width": reader.size[0], "height": reader.size[1]}
        if duration <= 0 or (reader.stream is None and not reader.has_audio):
            yield Step([], None, info, "no frames or sound")
            return
        spacing = max(options.frame_interval, duration / options.max_frames)
        windows = _Peekable(media.sound(file.path, start, options.audio_window, options.audio_hop)
                            if reader.has_audio else iter(()))
        last_hash = file.info.get("frame_hash") if start else None
        while start < duration or windows.peek() is not None:
            stop = start + options.media_step
            frames = []
            for frame in reader.frames(start, min(stop, duration), spacing):
                hashed = media.average_hash(frame.image)
                if media.same_shot(last_hash, hashed):   # the same shot: the frame kept before stands for it
                    continue
                frames.append(frame)
                last_hash = hashed
            pieces = [Piece(Segment.FRAME, Modality.IMAGE,
                            # Sized and PNG-encoded here, on the reading threads: a step's frames stay small.
                            part=image_part(f.image, options.frame_budget, compact=True), loc=f.time,
                            loc_end=following.time if following else min(stop, duration))
                      for f, following in zip(frames, [*frames[1:], None])]
            pieces += _sound_pieces(_windows_until(windows, stop))
            more = stop < duration or windows.peek() is not None
            step_info = info | ({"frame_hash": last_hash} if last_hash is not None else {})
            if not pieces and start == 0 and not more:
                yield Step([], None, step_info, "no frames or sound")
                return
            yield Step(pieces, stop if more else None, step_info,
                       _span(start, min(stop, duration), duration) if more or start else None)
            start = stop
