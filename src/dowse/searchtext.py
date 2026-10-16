"""Text the way keyword search keeps and matches it, and the way documents are cut into passages."""

import re
import unicodedata

_DROP = {0x00AD, 0x061C, 0xFEFF, 0x0640, 0x0670, *range(0x200C, 0x2010), *range(0x202A, 0x202F),
         *range(0x2060, 0x2065), *range(0x2066, 0x206A), *range(0x064B, 0x0660)}
_ALEF = {0x0622, 0x0623, 0x0625, 0x0671}
_TABLE = {code: None for code in _DROP} | {code: "ا" for code in _ALEF} | {0x200B: " "}


def normalized(text: str) -> str:
    """NFKC (ligatures and full-width forms become plain letters), invisible formatting characters dropped (joiners,
    direction marks, soft hyphens), and Arabic as people type it: no tatweel, no short-vowel marks, one alef."""
    return unicodedata.normalize("NFKC", text).translate(_TABLE)


def _in(char: str, low: int, high: int) -> bool:
    return low <= ord(char) <= high


def inflects(word: str) -> bool:
    """Words of scripts that attach endings (Bengali, Hindi, Arabic) are matched as prefixes."""
    return any(_in(c, 0x0900, 0x0DFF) or _in(c, 0x0600, 0x06FF) for c in word)


def has_words(text: str) -> bool:
    """At least a fifth of what isn't space is letters (marks count). A table of numbers gets keyword search only:
    a meaning vector of digits matches nothing anyone would describe."""
    letters = others = 0
    for char in text:
        if char.isspace():
            continue
        if unicodedata.category(char)[0] in "LM":
            letters += 1
        else:
            others += 1
    return letters > 0 and letters * 4 >= others


STOPWORDS = frozenset("""
a an the of in on at to for with and or my me that this these those from about where what which who when is was are
were be been being it its some any find show file files photo photos picture pictures image images screenshot
screenshots video videos pdf pdfs document documents recording he she him her his hers they them their we us our you
your i not no but as by if so than then there has have had do does did can will would should could may might must
into out up down over under very just also how why all each both more most other such only own same too
""".split())

def _word_char(char: str) -> bool:
    """Letters, numbers and marks, as the FTS tokenizer counts them: a Bengali vowel sign or an Arabic haraka stays
    inside its word (Python's \\w would split "ইলিশ" after "ইল")."""
    return unicodedata.category(char)[0] in "LNM"


def words(text: str) -> list[tuple[int, int]]:
    """The (start, end) of each word in `text`."""
    spans, start = [], None
    for index, char in enumerate(text):
        if _word_char(char):
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def terms(text: str) -> list[str]:
    """A query's content words, normalized as keyword search keeps its text: kind words ("screenshot") and stopwords
    dropped, words of two letters or more, each once."""
    folded = normalized(text).lower()
    seen: dict[str, None] = {}
    for start, end in words(folded):
        word = folded[start:end]
        if len(word) >= 2 and word not in STOPWORDS:
            seen.setdefault(word)
    return list(seen)


def fts_patterns(words: list[str], prefix_last: bool = False) -> list[str]:
    """FTS5 patterns: each word quoted, as a prefix for the word being typed and for words that take endings."""
    out = []
    for index, word in enumerate(words):
        prefix = (prefix_last and index == len(words) - 1) or inflects(word)
        out.append('"' + word.replace('"', '""') + '"' + ("*" if prefix else ""))
    return out


def chunks(text: str, size: int = 1800, overlap: int = 200, limit: int = 5000) -> list[str]:
    """~`size`-character passages (~450 tokens) overlapping a little, cut at whitespace."""
    clean = re.sub(r"[ \t ]+", " ", text)
    clean = re.sub(r"\n\s*\n\s*\n+", "\n\n", clean).strip()
    out: list[str] = []
    start = 0
    while start < len(clean) and len(out) < limit:
        end = min(start + size, len(clean))
        if end < len(clean):
            space = max(clean.rfind(" ", start, end), clean.rfind("\n", start, end))
            if space - start > size // 2:
                end = space
        piece = clean[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(clean):
            break
        following = max(end - overlap, start)
        if following <= start:
            following = end
        while following < end and not clean[following].isspace():
            following += 1
        start = following if following < end else end
    return out


def html_to_text(html: str) -> str:
    """Good enough for search: scripts, styles and comments dropped, block ends as line breaks, tags stripped."""
    import html as entities

    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>|<!--.*?-->", " ", html)
    text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\b[^>]*>", "\n", text)
    text = entities.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def mark(text: str, terms: list[str]) -> str:
    """`text` with every word of `terms` marked « », as written (the whole text; `snippet` is one line of it)."""
    text = normalized(text)
    out, cursor = [], 0
    for start, end in words(text):
        word = text[start:end].lower()
        if any(word == term or (inflects(term) and word.startswith(term)) for term in terms):
            out += [text[cursor:start], "«", text[start:end], "»"]
            cursor = end
    out.append(text[cursor:])
    return "".join(out)


def snippet(text: str, terms: list[str], width: int = 12) -> str | None:
    """A line of `text` around the first of `terms` it holds, those words marked « » as written, or None. A word of
    a language that attaches endings marks the whole word it starts ("কুমির" marks all of "কুমিরের")."""
    if not terms:
        return None
    text = normalized(text)   # same length for nearly all text; marks then land where they belong
    spans = words(text)

    def matches(start: int, end: int) -> bool:
        word = text[start:end].lower()
        return any(word == term or (inflects(term) and word.startswith(term)) for term in terms)

    marked = [index for index, (start, end) in enumerate(spans) if matches(start, end)]
    if not marked:
        return None
    first = marked[0]
    begin, end = max(first - width // 3, 0), min(first + width - width // 3, len(spans))
    out, cursor = [], spans[begin][0]
    for index in marked:
        if begin <= index < end:
            start, stop = spans[index]
            out += [text[cursor:start], "«", text[start:stop], "»"]
            cursor = stop
    out.append(text[cursor:] if end == len(spans) else text[cursor:spans[end - 1][1]])
    piece = re.sub(r"\s+", " ", "".join(out)).strip()
    return ("…" if begin > 0 else "") + piece + ("…" if end < len(spans) else "")
