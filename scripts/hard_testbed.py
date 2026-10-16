"""The hard part of the testbed (Testbed/hard), for evals/hard.json: what the first testbed doesn't ask.

- Near neighbours: three more recipes, an older lease, more appointment e-mails. The right one must beat its kin.
- Names that say what the text doesn't ("Grandma's soup.txt" is a list of vegetables).
- Words in photos (a whiteboard, a receipt, a sign, a poster), photographed, not screenshots.
- Dates: copies of the same photo taken in different seasons and years, notes and e-mails from different weeks
  (dated against the eval's fixed "now", 2026-10-13).
- Kinds the first testbed has none of: a spreadsheet, a CSV, scripts, an e-book, a slide deck.

Made by make_testbed.py; nothing here is downloaded (the photos it copies are the testbed's own).
"""

import os
import random
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFilter

RECIPES = {
    "carrot cake.txt": "Carrot cake\n\nGrate four carrots, mix with oil, brown sugar, eggs, flour, cinnamon and walnuts. "
                       "Bake 45 minutes. Top with cream cheese frosting once cool.",
    "pancakes.txt": "Sunday pancakes\n\nWhisk flour, milk, two eggs and a pinch of salt. Rest the batter ten minutes, "
                    "fry thin in butter, serve with maple syrup and blueberries.",
    "lemon drizzle.txt": "Lemon drizzle\n\nCream butter and caster sugar, beat in eggs and self-raising flour with the "
                         "zest of two lemons. While still warm, pour over lemon juice mixed with icing sugar.",
}

# What the name says and the text doesn't
NAMED = {
    "Grandma's soup.txt": "2 carrots\n1 onion\n3 potatoes\n1 litre of stock\n1 bay leaf\nSimmer for 40 minutes, blend "
                          "half of it, salt to taste.",
    "Packing list Iceland.txt": "thermal base layers\nwool socks\ncrampons\nheadlamp\nwaterproof boots\npassport\n"
                                "plug adapter\nsunglasses",
    "Speech for Tom's 40th.txt": "Some of you have known him longer than I have, but nobody has heard him sing in the "
                                 "car as often. He is the kindest stubborn person I know. Here's to the next forty. "
                                 "Cheers!",
    "Bike fit measurements.txt": "saddle height 74.5 cm\nsaddle setback 6 cm\nreach 52 cm\nstem 100 mm\n"
                                 "crank length 172.5 mm\nbar width 42 cm",
    "Neighbour complaint draft.txt": "Dear Mr Patel, I am writing again about the music after midnight on weekdays. "
                                     "We have a baby who wakes at every bass line. Could we agree on quiet after "
                                     "eleven? Kind regards, Alex",
}

COVER_LETTER = ("I am excited to apply for the data engineer position. Over the last five years I have built "
                "pipelines that move billions of rows a day, and I would love to bring that experience to your team.")

OLD_LEASE = [
    ("Tenancy agreement (2019)", "Flat 3, 18 Harbour Road. The tenancy runs for twelve months from 1 March 2019."),
    ("Rent and deposit", "Rent is 780 euros a month, due on the first. The deposit is two months of rent, 1,560 euros, "
                         "held in a protected scheme."),
    ("Smoking", "Smoking is not permitted anywhere in the flat or on the balcony."),
    ("Repairs", "The landlord repairs the boiler and the roof; the tenant replaces light bulbs and keeps drains clear."),
]

EMAILS = {
    "optician.eml": ("Vision Express <bookings@example.org>", "Your eye test on 4 November",
                     "Your eye test is booked for 4 November at 10:30. Please bring your current glasses and a list "
                     "of any medication.", "2026-09-28 09:12"),
    "car service.eml": ("Harbour Motors <service@example.org>", "Annual service booking",
                        "Your Golf is booked in for its annual service on Monday 8 September. Drop it off before "
                        "8:30; a courtesy car is ready.", "2026-09-01 16:40"),
    "rent increase.eml": ("Peter Landlord <peter@example.org>", "Rent from January",
                          "Hello Alex, after reviewing costs the rent will rise from 950 to 1,010 euros from January. "
                          "The other terms of the lease stay as they are.", "2026-10-12 18:05"),
}

STANDUP = {
    "standup notes.txt": ("Standup\n\nAisha: finishing the export to CSV. Ben: blocked on the staging database "
                          "credentials. Me: reviewing the onboarding flow.", "2026-10-07 09:45"),
    "standup notes old.txt": ("Standup\n\nAisha: on holiday. Ben: fixing flaky tests in the payment module. Me: "
                              "writing the quarterly report.", "2026-08-04 09:40"),
}

SCRIPTS = {
    "resize_photos.py": '"""Shrinks every JPEG in a folder to 1600 px on its long side, so they fit in an e-mail."""\n\n'
                        "import sys\nfrom pathlib import Path\n\nfrom PIL import Image\n\n"
                        "for path in Path(sys.argv[1]).glob('*.jpg'):\n    image = Image.open(path)\n"
                        "    image.thumbnail((1600, 1600))\n    image.save(path.with_stem(path.stem + '_small'))\n",
    "nightly_backup.ps1": "# Copies Documents to the NAS every night (run by Task Scheduler at 2:00).\n"
                          "$source = \"$env:USERPROFILE\\Documents\"\n$target = '\\\\nas\\backup\\documents'\n"
                          "robocopy $source $target /MIR /R:2 /W:5 /LOG:\"$env:TEMP\\backup.log\"\n",
    "countdown.js": "// A countdown timer for the kitchen: shows minutes and seconds left and beeps at zero.\n"
                    "function countdown(seconds, show, done) {\n  const end = Date.now() + seconds * 1000;\n"
                    "  const tick = () => {\n    const left = Math.max(0, end - Date.now());\n"
                    "    show(Math.floor(left / 60000), Math.floor(left / 1000) % 60);\n"
                    "    if (left > 0) setTimeout(tick, 250); else done();\n  };\n  tick();\n}\n",
}

CONTACTS = ("Name,Phone,Role\nMarta Ruiz,+34 600 111 222,Plumber\nJonas Berg,+46 70 555 0101,Electrician\n"
            "Aiko Tanaka,+81 90 1234 5678,Piano teacher\nDavid Okafor,+44 7700 900123,Accountant\n")

BUDGET = [("Item", "Monthly", "Yearly"), ("Rent", 950, 11400), ("Groceries", 420, 5040), ("Car insurance", 61, 732),
          ("Internet", 35, 420), ("Gym", 29, 348), ("Childcare", 640, 7680)]

LIGHTHOUSE = [
    ("The keeper", "Every evening for thirty years, Ewan climbed the hundred and twelve steps of the lighthouse on "
                   "Skerry Point to light the lamp. He talked to the gulls and wrote the weather in a green book."),
    ("The storm", "In the winter of the great storm the waves reached the gallery. A fishing boat lost its engine "
                  "off the reef, and Ewan kept the light turning by hand all night so the crew could find the "
                  "harbour."),
    ("Morning", "At dawn the boat was safe in the harbour and the three fishermen walked up the cliff path with "
                "bread and a bottle of whisky for the keeper, who was asleep at the foot of the stairs."),
]

OFFSITE = ["Team offsite: Lisbon", "Day 1: strategy for next year, customer stories, dinner in Alfama",
           "Day 2: hiking in Sintra, then a retrospective on the train back", "Bring: walking shoes, a rain jacket"]


def _font(size: int):
    from make_testbed import font   # the testbed's own face, so both halves look alike
    return font(size)


def _date(path: Path, when: str) -> None:
    stamp = datetime.strptime(when, "%Y-%m-%d %H:%M").timestamp()
    os.utime(path, (stamp, stamp))


def _photo_on(source: Path, target: Path, taken: str) -> None:
    """A copy of a testbed photo, taken (EXIF) and modified on `taken`."""
    image = Image.open(source).convert("RGB")
    exif = image.getexif()
    exif.get_ifd(0x8769)[0x9003] = datetime.strptime(taken, "%Y-%m-%d %H:%M").strftime("%Y:%m:%d %H:%M:%S")
    image.save(target, quality=90, exif=exif)
    _date(target, taken)


def _photographed(lines: list[tuple[str, int]], target: Path, paper: tuple, ink: tuple, surround: tuple,
                  tilt: float, seed: int) -> None:
    """Text on a surface, as a phone photographs it: in perspective, a little blurred, grainy, on a background."""
    rng = random.Random(seed)
    board = Image.new("RGB", (1400, 1000), paper)
    draw = ImageDraw.Draw(board)
    y = 90
    for text, size in lines:
        draw.text((90, y), text, fill=ink, font=_font(size))
        y += int(size * 1.6)
    scene = Image.new("RGB", (1800, 1350), surround)
    for _ in range(2500):   # a textured wall or table
        x, yy = rng.randrange(1800), rng.randrange(1350)
        shade = tuple(max(0, min(255, c + rng.randint(-18, 18))) for c in surround)
        ImageDraw.Draw(scene).point((x, yy), fill=shade)
    scene.paste(board.rotate(tilt, expand=True, fillcolor=surround), (170, 120))
    width, height = scene.size
    skew = width * 0.06   # seen a little from one side
    coefficients = _perspective([(0, 0), (width, 0), (width, height), (0, height)],
                                [(skew, height * 0.03), (width - skew * 0.4, 0), (width, height), (0, height * 0.97)])
    scene = scene.transform(scene.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC,
                            fillcolor=surround)
    scene = scene.filter(ImageFilter.GaussianBlur(1.1)).resize((1600, 1200), Image.Resampling.LANCZOS)
    scene.save(target, quality=84)


def _perspective(target: list[tuple[float, float]], source: list[tuple[float, float]]) -> list[float]:
    import numpy as np

    rows = []
    for (x, y), (u, v) in zip(target, source):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    solution = np.linalg.solve(np.array(rows, float), np.array([c for point in source for c in point], float))
    return solution.tolist()


def _xlsx(rows: list[tuple], sheet: str, target: Path) -> None:
    """A minimal workbook: one sheet, strings inline, numbers as numbers."""
    def cell(ref: str, value) -> str:
        if isinstance(value, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'
    body = "".join(f'<row r="{r}">' + "".join(cell(f"{'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[c]}{r}", v) for c, v in enumerate(row)) + "</row>"
                   for r, row in enumerate(rows, 1))
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml",
                         '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/'
                         '2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
                         'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                         'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" '
                         'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                         '</Types>')
        archive.writestr("_rels/.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
                         f'openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="{rel}/officeDocument" '
                         'Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{main}" '
                         f'xmlns:r="{rel}"><sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="r1"/></sheets>'
                         '</workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
                         f'"http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" '
                         f'Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{main}">'
                         f'<sheetData>{body}</sheetData></worksheet>')


def _epub(title: str, chapters: list[tuple[str, str]], target: Path) -> None:
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:'
                         'names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" '
                         'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                        for i in range(len(chapters)))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        archive.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                         'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                         f'<dc:identifier id="id">testbed-{escape(title)}</dc:identifier><dc:title>{escape(title)}'
                         f'</dc:title><dc:language>en</dc:language></metadata><manifest>{items}</manifest>'
                         f'<spine>{spine}</spine></package>')
        for i, (heading, text) in enumerate(chapters):
            archive.writestr(f"OEBPS/c{i}.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                             f'<head><title>{escape(heading)}</title></head><body><h1>{escape(heading)}</h1>'
                             f'<p>{escape(text)}</p></body></html>')


def _odp(lines: list[str], target: Path) -> None:
    """A minimal OpenDocument presentation: one slide, a line a paragraph."""
    paragraphs = "".join(f"<text:p>{escape(line)}</text:p>" for line in lines)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.presentation", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", '<?xml version="1.0"?><manifest:manifest xmlns:manifest="urn:oasis:'
                         'names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry manifest:full-path="/" '
                         'manifest:media-type="application/vnd.oasis.opendocument.presentation"/><manifest:file-entry '
                         'manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
        archive.writestr("content.xml", '<?xml version="1.0"?><office:document-content xmlns:office="urn:oasis:names:'
                         'tc:opendocument:xmlns:office:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
                         'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.3"><office:body>'
                         '<office:presentation><draw:page draw:name="1"><draw:frame><draw:text-box>'
                         f'{paragraphs}</draw:text-box></draw:frame></draw:page></office:presentation></office:body>'
                         '</office:document-content>')


def make(root: Path, text_pdf, docx) -> None:
    """Everything under root/hard. `text_pdf` and `docx` are the testbed's own writers."""
    hard = root / "hard"
    for sub in ("notes", "pdf", "mail", "photos", "sheets", "scripts", "books", "slides", "work"):
        (hard / sub).mkdir(parents=True, exist_ok=True)
    for name, text in {**RECIPES, **NAMED}.items():
        (hard / "notes" / name).write_text(text, encoding="utf-8")
    for name, (text, when) in STANDUP.items():
        (hard / "notes" / name).write_text(text, encoding="utf-8")
        _date(hard / "notes" / name, when)
    docx(hard / "work/Cover letter - Lumen Labs.docx", [COVER_LETTER])
    text_pdf(OLD_LEASE, hard / "pdf/old flat lease 2019.pdf")
    for name, (sender, subject, body, when) in EMAILS.items():
        sent = datetime.strptime(when, "%Y-%m-%d %H:%M").strftime("%a, %d %b %Y %H:%M:00 +0200")
        (hard / "mail" / name).write_text(f"From: {sender}\nTo: alex@example.org\nDate: {sent}\nSubject: {subject}\n"
                                          f"Content-Type: text/plain; charset=utf-8\n\n{body}\n", encoding="utf-8")
        _date(hard / "mail" / name, when)

    photos = root / "photos"
    for source, name, taken in [("IMG_1007.jpg", "IMG_5001.jpg", "2026-07-20 15:30"),
                                ("IMG_1007.jpg", "IMG_5002.jpg", "2025-07-12 11:05"),
                                ("DSC_2002.jpg", "IMG_5003.jpg", "2026-01-18 10:20"),
                                ("DSC_2002.jpg", "IMG_5004.jpg", "2024-12-28 13:45")]:
        if (photos / source).exists():
            _photo_on(photos / source, hard / "photos" / name, taken)
    _photographed([("Sprint 14 goals", 84), ("- migrate billing to Stripe", 64), ("- fix the login timeout", 64),
                   ("- demo on Friday", 64)], hard / "photos/IMG_6001.jpg", (238, 240, 236), (25, 60, 160),
                  (150, 140, 128), 2.5, 1)
    _photographed([("CAFE LUNA", 80), ("Flat white      3.40", 56), ("Croissant       2.90", 56),
                   ("Orange juice    5.50", 56), ("TOTAL          11.80", 64)], hard / "photos/IMG_6002.jpg",
                  (250, 250, 246), (30, 30, 30), (92, 64, 44), -4.0, 2)
    _photographed([("Guest WiFi", 96), ("Network: LunaGuest", 70), ("Password: olive-tree-42", 70)],
                  hard / "photos/IMG_6003.jpg", (40, 44, 48), (240, 240, 240), (200, 196, 188), 1.5, 3)
    _photographed([("LOST CAT", 110), ("Grey tabby, answers to Miso", 62), ("Last seen on Harbour Road", 62),
                   ("Please call 555 0199", 62)], hard / "photos/IMG_6004.jpg", (252, 236, 120), (20, 20, 20),
                  (120, 120, 118), -2.0, 4)

    _xlsx(BUDGET, "Monthly 2026", hard / "sheets/household budget.xlsx")
    _date(hard / "sheets/household budget.xlsx", "2026-10-02 20:15")
    (hard / "sheets/contacts.csv").write_text(CONTACTS, encoding="utf-8")
    for name, text in SCRIPTS.items():
        (hard / "scripts" / name).write_text(text, encoding="utf-8")
    _epub("The Lighthouse Keeper", LIGHTHOUSE, hard / "books/the lighthouse keeper.epub")
    _odp(OFFSITE, hard / "slides/offsite agenda.odp")
