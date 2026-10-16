# Using Dowse

Everything the app and the command line do. The [README](../README.md) is the short version.

## The app

`dowse-app` lives in the tray and keeps the index up to date. It follows Windows' light or dark theme.

- **One window, two modes.** **Quick search** is a compact list with the matching page, picture, passage or moment
  beside it. **Workspace** expands that same window into the glass gallery, with Search, Explore, Library and
  Settings on its left. The buttons or **Ctrl+O** switch both ways, keeping the words, filters, folder and selected
  file. Each mode remembers its size and position; both move, resize and use Windows' normal title bar.
- **Alt+Shift+Space** (another key in Settings) brings the existing window forward in its current mode; press it
  again while Dowse is focused to hide it. Quick search stays open when switching apps or opening a file;
  minimizing leaves it in the taskbar.
  Closing either mode returns to the tray. Tray › Workspace opens the workspace.
  - **Search** shows the finds as a curved wall of glass cases: as you type, the closest slide out of the rack
    toward you, the best one double size, the rest stay back in the shade; the *Closest…More* control sets how much
    comes forward. During a search, all results have green rims and sharp previews; other files have muted red
    rims and slightly foggy, softened previews. Clearing the query restores the neutral collection view.
    Scroll over the gallery to zoom toward the pointer (50–250%); drag to move, or Shift+scroll to
    move horizontally. The +/− and reset controls are at its lower right; +/− and 0 also work with the gallery
    focused. Or use a list. The viewing table on the right shows the file where it matched: the PDF page with
    your words marked (or the page itself, readable in place), the video or recording playing from its moment, the
    document's passage.
  - **More like this.** *Similar* on any file (in Search, Quick search, Explore or the Library) finds the files most
    like it, of every kind, each at its part most like it; up close, *Like this page*, *passage* or *moment* searches
    with just that part. Its vectors are already in the index, so it's instant and needs no model. Drop a file on the
    window (a photo someone sent, a scanned letter) to find what's like it: Dowse reads it the way it reads your files
    and doesn't add it; a copy of a file it already searches stands for that file. Explorer's right-click › Dowse ›
    Find similar does the same for any file.
  - **What you went back to.** With nothing typed, the files you opened, showed or copied lately come first, and your
    earlier searches are offered as chips. As you type, an earlier search that starts the same way shows after your
    words (Tab takes it), and the file you opened last time for the same words is marked. A search is remembered only
    once something is opened from it; it's kept on this PC beside the index, and Settings clears it or turns it off.
  - **Aisles** are shelves of your own, from any folder. Choose one and its files are there as pictures; take one
    out, or add one from *Might also belong*, and it's done at once (Undo puts it back). **New aisle** is a short
    form: a name, a sentence about what goes in it in your own words ("receipts, invoices and bills"), or one of the
    examples, with a live look at what it would hold; kinds of file follow your words ("photos of…") or are chosen
    under Change. Every file goes to the aisle it fits best, when it fits clearly. **Edit** changes the name and
    words. **Ideas from Dowse** are files it found alike, kept as an aisle with one click (or *Not interested*);
    with *Group the other files* off, what your aisles don't take is in "Everything else".
  - **Explore** lays out everything by what's in it: your aisles first, then aisles of files that are alike, whatever
    folder they're in, named by the model ("Animal · nature"). Walk it, step into an aisle, or type to bring matches forward where they
    stand. Searching uses the same green, sharp matches and muted red, slightly softened context as Search;
    the legend names both groups, and clearing restores the neutral floor. Zooming in upgrades visible previews
    to their screen resolution (up to 2048 pixels), including on
    high-density displays. The previous image stays visible while its sharper replacement loads; document-card
    text scales cleanly and PDF previews start at the top of the page. The rail lists the aisles too.
  - **Library**: every file Dowse searches, with a preview of any of them (see below).

| Key (popup and Search) | |
|---|---|
| type | search (words at once; meaning as soon as the model is up, ~2 s after it wakes) |
| ↑ ↓, Page Up/Down | move through results |
| Enter | open, at the page or moment when the app that opens it can be told (Edge, Acrobat, SumatraPDF, VLC, mpv, PotPlayer, MPC) |
| Ctrl+Enter | show in Explorer |
| Shift+Enter | up close, filling the window: zoom into a photo and pan it, turn a PDF's pages (the words marked, the pages that match), play a recording or video with its matching moments on the timeline ([ ] between them), read through a document; ↑ ↓ still move through the results, Esc closes |
| Ctrl+C / Ctrl+Shift+C | copy the file (paste it anywhere) / copy its path |
| Ctrl+O | switch this window between Quick search and Workspace, keeping the current search |
| Esc | clear the words, then the folder searched in; in Quick search, press again to hide |

The tray menu shows indexing progress and leads everywhere; it holds pause and quit. Errors go to `Logs\app.log`.

**What Dowse searches** is folders (searched whole), plus files added one by one from anywhere, minus what you
removed. The most specific choice wins: remove one file from a searched folder and it's out; add one file from a
removed folder and it's in. Inside folders, Dowse skips what isn't yours to search (code projects, `node_modules`,
hidden folders); a file you add yourself skips those rules, because you asked for it. Some files are never read
whoever asks (keys and password files, types Dowse can't read), and a OneDrive file that's only online waits until
it's downloaded. Change it from:

- **New screenshots** (Settings › Add new screenshots, off until you turn it on): each screenshot Windows saves
  (Win+PrtScn, Windows 11's Snipping Tool, or OneDrive's Print Screen option) joins your Dowsed files as soon as it's
  saved. Screenshots only copied to the clipboard make no file and aren't added; the clipboard is never read.
- **Explorer:** right-click files or folders › **Dowse** › Add to Dowse / Remove from Dowse (or, for a file, Find
  similar; on Windows 11, under Show more options), or **Send to › Dowse**. Select a hundred files at once: they arrive as one change, and one
  notification says what was added and what couldn't be.
- **The Library** (the main window, or the tray menu): every file Dowse searches, by folder, with how each is doing
  (searchable, waiting, couldn't be read and why). Pick one to see it on the viewing table, played or read in place.
  **Dowsed folders** is the folder manager (open one to see what's in it; the panel stays short however many you
  have); **Dowsed files** are the files added one by one; **All files** is everything, active and retained inactive
  files. Each item has
  a Search switch. Filter by name or activity, or use checkboxes to select many and Activate, Deactivate or Remove.
  **Inactive** lists the switches you turned off; **Removed** offers Add back. Removal asks for confirmation
  (Delete opens the same confirmation), with Undo available even after changing views. *Needs attention* lists
  what couldn't be read. Add files or folders, read again, or show an item in Explorer.
- **The command line:** `dowse add`, `dowse remove`, `dowse files` (below).

**Deactivate versus remove:** deactivation temporarily hides an item from search, Quick search and Explore, stops
indexing it after the current work finishes, and keeps its configuration and indexed data. Reactivating unchanged
files reuses their data; changed or new files are read in the background. Deactivating a folder covers everything
inside it, including files added individually. Child switches remain off when their parent is reactivated; a
file cannot turn itself on while a containing folder is inactive (the Library points to that folder).
Removal takes an item out of the collection and discards its indexed data; Add back reads it again. Neither
action deletes or changes original files. Settings has the same folder switches and safe removal confirmation;
every file preview has **Search options**, including in Quick search.

**Search in one folder:** "All folders" at the end of the filters keeps search to a folder you choose; so do
Explorer's "Search in this folder" (on a folder, or inside one) and the Library's.

**Light when idle.** At rest Dowse is the tray icon and ~85 MB: no model, no window. The windows live in a process
of their own (Edge WebView2 holds ~240 MB while any window exists), started when one is wanted (the first popup after
a long idle takes ~0.65 s, then it's instant) and gone after 10 minutes with none shown. The query model (~350 MB,
and graphics memory on a GPU) wakes when search is used and sleeps after 10 minutes without a search; until it's up
(~2 s), results come from words, then meaning joins them. Indexing starts its own model server when there are files to
read and stops it 90 s after the last (measured on 110 files: Dowse peaked at 860 MB, the server at 2.7 GB).

**First run** is four steps: a welcome (what it does, that nothing leaves the PC, which graphics card will do the
work), choosing a search model before any download, choosing folders (the usual ones suggested and counted,
"1,240 photos · 56 PDFs", with how long reading them
will take on this PC, from a few seconds' measurement of its speed once the model is here) while the model downloads with its progress, speed and time left, and the shortcut with
"Start with Windows".

**Settings** apply as they change: folders to search (and a way to the Library for the rest), file types to skip
(photos, screenshots, PDFs, documents, recordings, videos, or extensions like `txt`; a file you add yourself is
always searched), Explorer's menu on or off, encrypting the index (Windows' own encryption, tied to your sign-in;
on Windows Home, Device encryption or BitLocker does the whole drive instead), the shortcut (press the new one; a shortcut
another app holds is refused), start with Windows, graphics card or processor (switched live, downloading what's
needed), only index when plugged in (indexing always pauses on Battery Saver), pause, and the index's size,
rebuild, and folders.

### Search models

Choose during first-run setup, or later in **Settings → Search model**:

- **EmbeddingGemma 2 · Q8** (recommended, about 865 MB): semantic search across text, pictures, sound and video.
- **EmbeddingGemma 300M · Q4** (about 278 MB): [a smaller text-only model](https://huggingface.co/ggml-org/embeddinggemma-300M-qat-q4_0-GGUF).
  Documents and PDF text have semantic search; photos, recordings and videos are searchable by filename, and
  screenshots by their OCR words, not by their visual or audio content.

Changing models requires confirmation. Dowse downloads and verifies the selected weights and checks the runtime
before touching the index; preparation can be cancelled. It then rebuilds search data with the new model, keeping
names and existing keyword text searchable meanwhile. Original files are never changed. Failed or cancelled
preparation leaves the current model and index intact. Previously downloaded models are kept for switching back.
Only these supported, pinned models can be selected; arbitrary GGUF files are not accepted. CLI commands use the
same selected model as the app. Existing installations retain EmbeddingGemma 2 until explicitly changed.

## Command line

```powershell
uv sync
uv run dowse setup                 # selected model (default 865 MB, once) + runtime (--backend cpu|vulkan|cuda)
uv run dowse index D:\Photos C:\Users\me\Documents   # remembered; `dowse index` alone re-syncs them
uv run dowse search "the clause about pets in the lease"
uv run dowse search "K7Q2LM" -k screenshot
uv run dowse search "where they talk about sleep"     # → episode 12.mp3 @0:00–0:30
uv run dowse similar D:\Photos\IMG_1004.jpg          # query by example: a picture, a recording, a video, a document
uv run dowse watch                 # keep the index in step, live (Ctrl+C stops); `index` waits while it runs
uv run dowse add D:\Scans\lease.pdf  # files from anywhere, or folders (through the app when it's running)
uv run dowse remove D:\Photos\private   # out of the index; the files aren't touched
uv run dowse deactivate D:\Photos       # temporarily out of search; configuration and indexed data are kept
uv run dowse activate D:\Photos         # include it again; unchanged files reuse their data
uv run dowse files inactive             # explicitly paused files/folders; `files folders` lists search roots
uv run dowse files attention       # what Dowse searches, file by file: all, added, attention, removed, a folder
uv run dowse-app                   # the tray app: first run sets everything up; it indexes live itself
uv run dowse status -v
uv run dowse bench                 # how fast this PC embeds
uv run python scripts/make_testbed.py && uv run dowse index %LOCALAPPDATA%\Dowse\Testbed
uv run dowse eval evals/testbed.json
```

Everything lives in `%LOCALAPPDATA%\Dowse` (or `$DOWSE_HOME`): `Models`, `Runtime`, `Index\index.sqlite`, and
`Logs`. Dowse only reads your files; it never changes, moves or uploads them.
