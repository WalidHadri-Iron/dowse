// A search as you type, shared by the main window and the popup: debounced, the older query cancelled when a newer
// one is typed, words first while the model wakes and meaning as soon as it's up, the same file kept selected while
// the query sharpens. Or a search with no words: "more like this" (a file, a place in it, or a file dropped on the
// window). With nothing to search for, the files gone back to lately come first, and earlier searches are offered.

import { api, type Answer, type Hit, type Kind, type LikeTarget, type PastQuery, type SearchFilter, type SearchOptions } from "./api";
import { activeFilters } from "./search-filters";
import { dowse } from "./app.svelte";
import { searchable } from "./selection-state";
import { appendScopes, scopesFromRoute, withoutScope } from "./search-scope";
import { BROWSE_PAGE_SIZE } from "./browse";
import { hasClearMatch } from "./search-confidence";
import { focusedHits } from "./search-presentation";
import { completion, openedFirst } from "./history";

/** The most often the results on screen are refreshed while files are being read. */
const REFRESH_MS = 5000;

/** The same files, in the same order: showing them again would change nothing. */
function samePaths(a: Hit[], b: Hit[]): boolean {
  return a.length === b.length && a.every((hit, i) => hit.path === b[i].path && hit.loc === b[i].loc);
}

/** What "more like this" searches with: a file of the index (a place in it), or a dropped file and its picture. */
export interface Like extends LikeTarget { file?: File; preview?: string }

export class Search {
  query = $state("");
  like = $state<Like | null>(null);
  opened = $state<Hit[]>([]);              // gone back to lately (history): first when nothing is searched
  pastQueries = $state<PastQuery[]>([]);   // earlier searches: all of them when nothing's typed, else those it starts
  kinds = $state<Kind[]>([]);
  within = $state<string[]>([]);
  date = $state<SearchOptions["date"]>(null);
  ignoredFilters = $state<SearchOptions["ignoredFilters"]>([]);
  answer = $state<Answer | null>(null);
  recent = $state<Hit[]>([]);
  browseTotal = $state(0);
  browseOffset = $state(0);
  browseMore = $state(false);
  browseBusy = $state(false);
  browseError = $state("");
  browseLoaded = $state(false);
  selected = $state(0);
  busy = $state(false);
  showCandidates = $state(false);
  /** Fresher results for the search on screen, found while files were being read: offered, never swapped in under the
   * person's eyes (the gallery would reshuffle every few seconds while indexing). */
  pending = $state<Answer | null>(null);

  private controller: AbortController | null = null;
  private timer = 0;
  private historyTimer = 0;
  private recentVersion = 0;
  private browseController: AbortController | null = null;
  private historyController: AbortController | null = null;
  private filterQuery = "";
  private focusContext = "";
  private browseScope = "";
  private loadedBrowseScope = "";
  private browseTarget = 0;
  private refreshTimer = 0;
  private lastRefresh = 0;
  private refreshController: AbortController | null = null;

  constructor(private limit = 60) {}

  options: SearchOptions = $derived({ date: this.date, ignoredFilters: this.ignoredFilters });
  filters: SearchFilter[] = $derived(activeFilters(this.query, this.answer, this.kinds, this.within, this.options));

  removeFilter(id: SearchFilter["id"], path?: string): void {
    if (id === "folder") this.within = path ? withoutScope(this.within, path) : [];
    else {
      if (id === "kind") this.kinds = [];
      if (id === "date") this.date = null;
      if (!this.ignoredFilters.includes(id)) this.ignoredFilters = [...this.ignoredFilters, id];
    }
  }

  setKinds(kinds: Kind[]): void {
    this.kinds = kinds;
    // Selecting All is an explicit override, including a kind inferred from the current query.
    if (!this.ignoredFilters.includes("kind")) this.ignoredFilters = [...this.ignoredFilters, "kind"];
  }

  /** A search is on: words, or "more like this". */
  searching: boolean = $derived(!!this.query.trim() || !!this.like);

  /** Lately opened files lead an unfiltered collection's first page (each once). */
  openedFirst: Hit[] = $derived.by(() => {
    if (this.searching || this.browseOffset || this.filters.length || !this.opened.length) return [];
    return this.opened;
  });

  candidates: Hit[] = $derived.by(() => {
    const hits = this.searching ? (this.answer?.hits ?? []) : openedFirst(this.openedFirst, this.recent);
    return hits.filter((hit) => searchable(hit.path, dowse.state?.selection));
  });

  /** How many of `hits` lead as lately opened. */
  openedCount: number = $derived.by(() => {
    const first = new Set(this.openedFirst.map((hit) => hit.path));
    let count = 0;
    for (const hit of this.hits) { if (!first.has(hit.path)) break; count++; }
    return count;
  });

  /** What an earlier search adds to the typed words ("elec" → "tricity bill"), to take with Tab. */
  suggestion: string = $derived(this.like ? "" : completion(this.query, this.pastQueries));

  /** The file opened last time these same words were searched. */
  lastOpened: string | null = $derived(this.query.trim() ? this.pastQueries.find((q) => q.exact)?.file?.path ?? null : null);

  takeSuggestion(): boolean {
    const rest = this.suggestion;
    if (rest) this.query += rest;
    return !!rest;
  }

  /** More like this file (`hit`), or this place in it. */
  likeFile(hit: { path: string; name: string; kind: Kind }, loc: number | null = null, place: string | null = null): void {
    this.dropLike();
    this.like = { path: hit.path, name: hit.name, kind: hit.kind, loc, place, indexed: true };
    this.query = "";
  }

  /** More like a file on disk, by its path (Dowse's own or not: the app reads it where it is). */
  likePath(path: string): void {
    this.dropLike();
    this.like = { path, name: path.split(/[\\/]/).pop() ?? path, kind: null, loc: null, place: null, indexed: false };
    this.query = "";
  }

  /** Windows' file picker, for the file to find what's like; without the app's window (a browser in development),
   * the browser's own, which sends the file whole as a drop does. */
  async chooseExample(): Promise<void> {
    try {
      const { paths } = await api.pick("files");
      if (paths[0]) this.likePath(paths[0]);
      return;
    } catch { /* no native window here */ }
    const input = document.createElement("input");
    input.type = "file";
    input.onchange = () => { const file = input.files?.[0]; if (file) this.likeDropped(file); };
    input.click();
  }

  /** More like a file dropped on the window. */
  likeDropped(file: File): void {
    this.dropLike();
    const picture = /^image\//.test(file.type) ? URL.createObjectURL(file) : undefined;
    this.like = { path: null, name: file.name, kind: null, loc: null, place: null, indexed: false, file, preview: picture };
    this.query = "";
  }

  /** Back from "more like this". */
  dropLike(): void {
    if (this.like?.preview) URL.revokeObjectURL(this.like.preview);
    this.like = null;
  }

  clear: boolean = $derived(hasClearMatch(this.answer) && this.candidates.some(hit => hit.strong));
  matchCount: number = $derived(this.clear ? this.candidates.filter(hit => hit.strong).length : 0);
  hits: Hit[] = $derived.by(() => {
    const hits = this.candidates;
    return this.searching ? focusedHits(hits, this.clear, this.showCandidates) : hits;
  });

  toggleCandidates(): void {
    const keep = this.current?.path;
    this.showCandidates = !this.showCandidates;
    const index = this.hits.findIndex(hit => hit.path === keep);
    this.selected = index >= 0 ? index : 0;
  }

  current: Hit | null = $derived.by(() => {
    const hits = this.hits;
    return hits[Math.min(this.selected, hits.length - 1)] ?? null;
  });

  /** Called whenever the query, kinds or folder change. */
  schedule(delay = 140): void {
    if (this.filterQuery !== this.query) {
      this.filterQuery = this.query;
      if (this.ignoredFilters.length) this.ignoredFilters = [];
    }
    if (this.query.trim() && this.like) this.dropLike();   // words typed: a search of their own
    const context = JSON.stringify([this.query, this.kinds, this.within, this.options, this.like?.path, this.like?.loc, this.like?.name]);
    if (context === this.focusContext) return;
    this.showCandidates = false;
    this.focusContext = context;
    clearTimeout(this.timer);
    clearTimeout(this.historyTimer);
    this.historyController?.abort();
    this.controller?.abort();   // invalidate the old query as soon as typing changes, including the debounce gap
    this.controller = null;     // its finally cannot clear the new request's pending state
    this.dropPending();
    // Typing does not invalidate the background collection; only its actual filters do.
    const scope = JSON.stringify([this.within, this.kinds, this.date]);
    if (this.browseScope !== scope) { this.browseController?.abort(); this.browseBusy = false; }
    this.busy = this.searching;
    if (!this.searching) {
      this.answer = null;
      this.loadHistory();
      this.loadRecent();
      return;
    }
    this.historyTimer = window.setTimeout(() => this.loadHistory(), delay);
    this.timer = window.setTimeout(() => this.run(), this.like ? 0 : delay);
  }

  async run(): Promise<void> {
    clearTimeout(this.timer);
    if (!this.searching) { this.schedule(0); return; }
    this.controller?.abort();
    this.dropPending();
    const controller = new AbortController();
    this.controller = controller;
    const keep = this.current?.path;
    const like = this.like;
    this.busy = true;
    try {
      const answer = !like ? await api.search(this.query, this.kinds, this.within, controller.signal, this.limit, this.options)
        : like.file ? await api.similarFile(like.file, this.kinds, this.within, this.date, controller.signal)
        : await api.similar(like.path!, like.loc, this.kinds, this.within, this.date, controller.signal, this.limit);
      if (controller.signal.aborted) return;
      if (like && this.like === like && answer.like) {
        // What the app found it to be: its kind and place, or for a dropped copy, the original. Set in place: a new
        // object would be a new search.
        like.kind = answer.like.kind; like.place = answer.like.place; like.indexed = answer.like.indexed;
        if (answer.like.path) like.path = answer.like.path;
        if (answer.like.indexed) like.file = undefined;
      }
      this.answer = answer;
      const index = this.hits.findIndex((hit) => hit.path === keep);
      this.selected = index >= 0 ? index : 0;
    } catch (error) {
      if (!controller.signal.aborted && (error as Error).name !== "AbortError") {
        this.answer = null;
        dowse.error = like ? `Couldn’t search like ${like.name}: ${(error as Error).message}` : String(error);
        if (like && this.like === like) this.dropLike();
      }
    } finally {
      if (this.controller === controller) this.busy = false;
    }
  }

  /** What was indexed changed (files read, changed, chosen…). At most every REFRESH_MS: a search on screen is fetched
   * again quietly and only offered if it differs; the collection is reloaded only if its files differ. */
  indexChanged(): void {
    clearTimeout(this.refreshTimer);
    const wait = Math.max(0, this.lastRefresh + REFRESH_MS - Date.now());
    this.refreshTimer = window.setTimeout(() => {
      this.lastRefresh = Date.now();
      if (this.searching) void this.refresh();
      else void this.loadBrowse(this.browseOffset, null, true);
    }, wait);
  }

  private async refresh(): Promise<void> {
    if (this.busy || this.like?.file) return;   // a search is under way (it will be fresh), or a dropped file (read once)
    if (!this.answer) { void this.run(); return; }
    this.refreshController?.abort();
    const request = new AbortController(); this.refreshController = request;
    const shown = this.answer;
    const like = this.like;
    try {
      const answer = !like ? await api.search(this.query, this.kinds, this.within, request.signal, this.limit, this.options, true)
        : await api.similar(like.path!, like.loc, this.kinds, this.within, this.date, request.signal, this.limit, true);
      if (request.signal.aborted || this.answer !== shown || this.like !== like) return;   // another search since
      if (samePaths(answer.hits, shown.hits)) { this.pending = null; return; }            // nothing would move
      if (!shown.hits.length) { this.answer = answer; return; }                          // nothing to keep in place
      this.pending = answer;
    } catch { /* a refresh is a convenience: what's shown stays */ }
    finally { if (this.refreshController === request) this.refreshController = null; }
  }

  /** The offered fresher results, shown (the person asked for them). */
  showPending(): void {
    const answer = this.pending;
    if (!answer) return;
    const keep = this.current?.path;
    this.pending = null;
    this.answer = answer;
    const index = this.hits.findIndex((hit) => hit.path === keep);
    this.selected = index >= 0 ? index : 0;
  }

  private dropPending(): void {
    this.refreshController?.abort();
    this.refreshController = null;
    this.pending = null;
  }

  async loadRecent(): Promise<void> {
    const scope = JSON.stringify([this.within, this.kinds, this.date]);
    if (this.browseBusy && this.browseScope === scope && this.browseTarget === 0) return;
    if (this.browseLoaded && this.loadedBrowseScope === scope && this.browseOffset === 0 && !this.browseError) return;
    return this.loadBrowse(0);
  }

  /** Lately opened files and earlier searches (those the typed words start, when there are some). */
  async loadHistory(): Promise<void> {
    clearTimeout(this.historyTimer);
    this.historyController?.abort();
    const request = new AbortController(); this.historyController = request;
    try {
      const answer = await api.history(this.like ? "" : this.query, request.signal);
      if (request.signal.aborted) return;
      this.opened = answer.recent;
      this.pastQueries = answer.queries;
    } catch { /* history is a convenience: without it, search is the same */ }
  }

  /** History was cleared or switched off. */
  forgetHistory(): void { this.opened = []; this.pastQueries = []; }

  /** `background`: a refresh after indexing, which changes nothing on screen unless the page's files changed. */
  async loadBrowse(offset = this.browseOffset, edge: "first" | "last" | null = null, background = false): Promise<void> {
    if (background && this.browseBusy) return;   // a load is under way: it is fresh
    const version = ++this.recentVersion;
    this.browseController?.abort();
    const request = new AbortController(); this.browseController = request;
    const within = [...this.within], kinds = [...this.kinds], date = this.date;
    const scope = JSON.stringify([within, kinds, date]);
    this.browseScope = scope; this.browseTarget = offset;
    const keep = this.current?.path;
    this.browseBusy = true; this.browseError = "";
    try {
      const answer = await api.browse(within, kinds, date, offset, BROWSE_PAGE_SIZE, request.signal, background);
      if (version !== this.recentVersion || request.signal.aborted) return;
      if (!background || !samePaths(answer.hits, this.recent)) this.recent = answer.hits;
      this.browseTotal = answer.total; this.browseOffset = answer.offset; this.browseMore = answer.hasMore;
      this.browseLoaded = true;
      this.loadedBrowseScope = scope;
      if (!this.query.trim()) {
        const index = this.recent.findIndex((hit) => hit.path === keep);
        this.selected = edge === "last" ? Math.max(0, this.recent.length - 1) : edge === "first" ? 0 : index >= 0 ? index : 0;
      }
    } catch (error) {
      if (version === this.recentVersion && !request.signal.aborted) {
        this.recent = []; this.browseTotal = 0; this.browseMore = false;
        this.browseError = String((error as Error).message ?? error);
        this.browseLoaded = true;
      }
    } finally { if (this.browseController === request) this.browseBusy = false; }
  }

  browsePage(direction: -1 | 1, edge: "first" | "last" = "first"): void {
    if (this.searching || this.browseBusy || (direction > 0 && !this.browseMore) || (direction < 0 && !this.browseOffset)) return;
    void this.loadBrowse(Math.max(0, this.browseOffset + direction * BROWSE_PAGE_SIZE), edge);
  }

  /** The model just woke: a words-only answer gets its meaning now. */
  modelReady(): void {
    if (this.query.trim() && !this.like && this.answer && !this.answer.meaning) this.run();
  }

  move(step: number): void {
    const count = this.hits.length;
    if (!this.searching && count && (this.selected + step >= count || this.selected + step < 0)) {
      this.browsePage(step > 0 ? 1 : -1, step > 0 ? "first" : "last");
    }
    if (count) this.selected = Math.max(0, Math.min(count - 1, this.selected + step));
  }

  route(compact = false): string {
    const params = new URLSearchParams();
    if (this.query || this.kinds.length || this.date || this.ignoredFilters.length) params.set("q", this.query);
    appendScopes(params, this.within);
    if (this.kinds.length) params.set("kinds", this.kinds.join(","));
    if (this.date?.from) params.set("from", this.date.from);
    if (this.date?.to) params.set("to", this.date.to);
    if (this.ignoredFilters.length) params.set("ignore", this.ignoredFilters.join(","));
    if (this.like) {   // (a dropped file the index doesn't hold has no path: an empty "like" is the one in hand)
      params.set("like", this.like.file ? "" : this.like.path ?? "");
      if (this.like.loc !== null) params.set("at", String(this.like.loc));
    }
    return `${compact ? "/popup" : "/search"}${params.size ? "?" + params : ""}`;
  }

  fromRoute(params: URLSearchParams): void {
    // A bare Search/Quick search navigation keeps the ongoing search. Explicit links supply new words or a folder, or
    // a file to find what's like (Explorer's Find similar: `like`, and `at` a place in it).
    const likePath = params.get("like");
    if (likePath) {
      const at = params.has("at") ? Number(params.get("at")) : null;
      if (likePath !== this.like?.path || at !== this.like?.loc) {
        this.dropLike();
        const name = likePath.split(/[\\/]/).pop() ?? likePath;
        this.like = { path: likePath, name, kind: null, loc: at, place: null, indexed: false };
        this.query = "";
      }
    } else if (!params.has("like") && this.like && (params.get("q")?.trim() || (params.has("in") && !params.has("q")))) {
      this.dropLike();
    }
    if (params.has("q")) this.query = params.get("q") ?? "";
    if (params.has("in") || params.has("q")) {
      const paths = scopesFromRoute(params);
      if (JSON.stringify(this.within) !== JSON.stringify(paths)) this.within = paths;
    }
    if (params.has("q")) {
      const known: Kind[] = ["image", "screenshot", "pdf", "doc", "audio", "video"];
      const kinds = (params.get("kinds") ?? "").split(",").filter((k): k is Kind => known.includes(k as Kind));
      if (this.kinds.join(",") !== kinds.join(",")) this.kinds = kinds;
      const date = params.has("from") || params.has("to")
        ? { from: params.get("from") ?? "", to: params.get("to") ?? "" } : null;
      if (this.date?.from !== date?.from || this.date?.to !== date?.to) this.date = date;
      const ignored = (params.get("ignore") ?? "").split(",").filter((id): id is "date" | "kind" => id === "date" || id === "kind");
      if (this.ignoredFilters.join(",") !== ignored.join(",")) this.ignoredFilters = ignored;
      this.filterQuery = this.query;
    }
  }
}

// Both modes use this same instance, so expanding retains filters, results, and the selected file.
export const search = new Search();

/** An accession tag's machine line, short enough for every tag: "PDF · p. 12 · Contracts". */
export function machineLine(hit: Hit): string {
  const kind = { image: "Photo", screenshot: "Screenshot", pdf: "PDF", doc: "Doc", audio: "Sound",
                 video: "Video" }[hit.kind];
  const place = hit.place?.replace(/^Page /, "p. ").replace(/^Passage /, "¶ ") ?? null;
  const folder = hit.folder.split(" › ").pop();
  return [kind, place, folder].filter(Boolean).join(" · ");
}
