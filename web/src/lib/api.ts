// The app's local server: JSON routes, pictures, media, and a stream of events.
import { scopePaths } from "./search-scope";

export type Kind = "image" | "screenshot" | "pdf" | "doc" | "audio" | "video";

export interface SearchFilter {
  id: "folder" | "kind" | "date";
  source: "query" | "manual";
  label?: string;
  kinds?: Kind[];
  from?: string;
  to?: string;
}
export interface SearchOptions {
  ignoredFilters: ("date" | "kind")[];
  date: { from: string; to: string } | null;
}
export interface SearchFolder {
  path: string;
  name: string;
  root: string;
  isRoot: boolean;
  count: number;
  active: boolean;
}
export interface Evidence {
  source: "filename" | "folder" | "title" | "text" | "ocr" | "semantic" | "date";
  words: string[];
  place?: string | null;
  loc?: number | null;
  locEnd?: number | null;
  modality?: "visual" | "audio" | "text";
  dateSource?: "taken" | "modified";
  date?: string;
}

export interface Hit {
  path: string;
  name: string;
  folder: string;
  kind: Kind;
  rank: number;
  strong: boolean;
  score: number;
  loc: number | null;
  locEnd: number | null;
  place: string | null;
  excerpt: string | null;
  mtime: number;
  size: number;
  info: Record<string, unknown>;
  segment: number;
  visual: boolean;
  moments: { loc: number; locEnd: number | null; place: string | null }[];
  evidence?: Evidence[];
}

/** What "more like this" is like: a file of the index (or a place in it), or a file dropped on the window. */
export interface LikeTarget {
  path: string | null;   // null: a dropped file the index doesn't hold
  name: string;
  kind: Kind | null;
  loc: number | null;
  place: string | null;
  indexed: boolean;
}

/** An earlier search that starts the way the new one does, with the file opened from it last time. */
export interface PastQuery { query: string; exact: boolean; at: number; file: Hit | null }

export interface Answer {
  query: string;
  meaning: boolean;
  ms: number;
  strong: number;
  terms: string[];
  hits: Hit[];
  when: string | null;   // the time the query named, results kept to it: "summer 2026"
  filters?: SearchFilter[];
  like?: LikeTarget;
}
export interface BrowsePage { hits: Hit[]; total: number; offset: number; limit: number; hasMore: boolean }

export interface AisleFile { path: string; name: string; folder: string; kind: Kind | null; visual: boolean;
  pinned?: boolean }
export interface AisleDefinition { id?: string; name: string; about: string; pinned: string[]; excluded: string[];
  kinds: Kind[] }
export interface MyAisle extends AisleDefinition { id: string; count: number; samples: string[] }
export interface SuggestedAisle { id: string; label: string; about: string; count: number; samples: string[] }

export interface Status {
  state: "starting" | "reading" | "idle" | "paused" | "waiting" | "off" | "error";
  text: string;
  done: number;
  total: number;
}

export interface Selection {
  folders: string[];
  files: string[];
  removed: string[];
  inactive: string[];
  forgotten: string[];
}

export interface Entry {
  path: string;
  name: string;
  folder: string;
  parent: string;
  kind: Kind | null;
  size: number | null;
  mtime: number | null;
  status: "done" | "partial" | "pending" | "skipped" | "failed" | "refused" | "missing" | "removed" | "inactive";
  statusText: string;
  detail: string;
  named: boolean;
  isFolder: boolean;
  active: boolean;
  inactiveBy: string | null;
  visual: boolean;
}

export interface AppState {
  version: string;
  setupNeeded: boolean;
  searchModel: SearchModel;
  device: { choice: string | null; onGpu: boolean; gpu: string | null };
  status: Status;
  model: "asleep" | "loading" | "ready" | "unavailable";
  selection: Selection;
  theme: { dark: boolean; accent: string };
  settings: {
    theme: "system" | "light" | "dark";
    hotkey: string;
    startup: boolean;
    pluggedInOnly: boolean;
    paused: boolean;
    explorerMenu: boolean;
    encrypt: boolean;
    encryptionSupported: boolean;
    history: boolean;
    screenshots: boolean;          // new screenshots added to Dowsed files as Windows saves them
    screenshotFolders: string[];   // where Windows (and OneDrive) save them
    skip_kinds: Kind[];
    skip_extensions: string[];
  };
}

export interface SearchModel {
  id: string;
  name: string;
  description: string;
  bytes: number;
  installed: boolean;
  multimodal: boolean;
  recommended: boolean;
}

export interface ModelJob {
  target: string;
  kind: "setup" | "model" | "processor";
  state: "running" | "done" | "failed" | "cancelled";
  stage: string;
  done: number;
  total: number;
  rate: number;
  left: number | null;
  canCancel: boolean;
  error: string | null;
}

/** `background`: a refresh the page made by itself, not something the person did; the app doesn't count it as Dowse
 * being in use (which makes reading files wait). */
async function call<T>(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal, background = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  if (background) headers["X-Dowse-Background"] = "1";
  const response = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    let why = `${response.status}`;
    try { why = (await response.json()).error ?? why; } catch { /* plain text */ }
    throw new Error(why);
  }
  return response.json() as Promise<T>;
}

export const api = {
  /** Trades the window's one-time token for the session cookie. */
  async session(): Promise<void> {
    const token = new URLSearchParams(location.search).get("t");
    if (token) {
      await call("GET", `/api/session?t=${encodeURIComponent(token)}`);
      history.replaceState(null, "", location.pathname + location.hash);   // the token leaves the address bar
    }
  },
  state: () => call<AppState>("GET", "/api/state"),
  searchFolders: (signal?: AbortSignal) => call<{ folders: SearchFolder[] }>("GET", "/api/search/folders", undefined, signal),
  search: (q: string, kinds: Kind[], within: string | string[] | null, signal?: AbortSignal, limit = 60, options?: SearchOptions,
           background = false) =>
    call<Answer>("POST", "/api/search", { q, kinds, within, limit, ...options }, signal, background),
  warm: () => call("POST", "/api/warm", {}),
  /** More like a file of the index (at a place in it: a page, a passage, seconds), or a file on disk. */
  similar: (path: string, loc: number | null, kinds: Kind[], within: string[], date: SearchOptions["date"],
            signal?: AbortSignal, limit = 60, background = false) =>
    call<Answer>("POST", "/api/similar", { path, loc, kinds, within: within.length ? scopePaths(within) : null, date, limit },
                 signal, background),
  /** More like a file dropped on the window: sent whole (a copy of a file of the index stands for that file). */
  async similarFile(file: File, kinds: Kind[], within: string[], date: SearchOptions["date"], signal?: AbortSignal): Promise<Answer> {
    const params = new URLSearchParams({ name: file.name });
    if (kinds.length) params.set("kinds", kinds.join(","));
    if (within.length) params.set("folders", JSON.stringify(scopePaths(within)));
    if (date?.from) params.set("from", date.from);
    if (date?.to) params.set("to", date.to);
    const response = await fetch(`/api/similar/file?${params}`, {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin", signal,
    });
    if (!response.ok) {
      let why = `${response.status}`;
      try { why = (await response.json()).error ?? why; } catch { /* plain text */ }
      throw new Error(why);
    }
    return response.json() as Promise<Answer>;
  },
  /** Files gone back to lately, and earlier searches that start as `q` does. */
  history: (q = "", signal?: AbortSignal) =>
    call<{ recent: Hit[]; queries: PastQuery[] }>("GET", `/api/history?q=${encodeURIComponent(q)}`, undefined, signal),
  clearHistory: () => call("POST", "/api/history/clear", {}),
  browse: (within: string[], kinds: Kind[], date: SearchOptions["date"], offset = 0, limit = 80, signal?: AbortSignal,
           background = false) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (within.length) params.set("folders", JSON.stringify(scopePaths(within)));
    if (kinds.length) params.set("kinds", kinds.join(","));
    if (date?.from) params.set("from", date.from);
    if (date?.to) params.set("to", date.to);
    return call<BrowsePage>("GET", `/api/browse?${params}`, undefined, signal, background);
  },
  recent: (within: string | string[] | null, limit = 40, kinds: Kind[] = [], date: SearchOptions["date"] = null) => {
    const params = new URLSearchParams({ limit: String(limit) });
    const paths = scopePaths(within);
    if (paths.length === 1) params.set("in", paths[0]);
    else if (paths.length) params.set("folders", JSON.stringify(paths));
    if (kinds.length) params.set("kinds", kinds.join(","));
    if (date?.from) params.set("from", date.from);
    if (date?.to) params.set("to", date.to);
    return call<{ hits: Hit[] }>("GET", `/api/recent?${params}`);
  },
  passage: (segment: number, q: string, path = "", loc: number | null = null, signal?: AbortSignal) =>
    call<{ text: string | null; loc: number | null; count: number | null }>("GET",
      `/api/passage?segment=${segment}&q=${encodeURIComponent(q)}&path=${encodeURIComponent(path)}` +
      (loc !== null ? `&loc=${loc}` : ""), undefined, signal),
  explore: () => call<{ aisles: import("../components/MapFloor.svelte").MapAisle[];
    pieces: import("../components/MapFloor.svelte").MapPiece[]; total: number }>("GET", "/api/explore"),
  aisles: () => call<{ aisles: MyAisle[]; suggestions: SuggestedAisle[]; auto: boolean }>("GET", "/api/aisles"),
  aislesAuto: (on: boolean) => call("POST", "/api/aisles/auto", { on }),
  aisleMembers: (id: string) =>
    call<{ members: AisleFile[]; near: AisleFile[]; excluded: AisleFile[] }>("GET", `/api/aisles/members?id=${encodeURIComponent(id)}`),
  aislePreview: (draft: AisleDefinition, signal?: AbortSignal) =>
    call<{ count: number; members: AisleFile[]; near: AisleFile[] }>("POST", "/api/aisles/preview", draft, signal),
  saveAisle: (draft: AisleDefinition) => call<MyAisle>("POST", "/api/aisles/save", draft),
  deleteAisle: (id: string) => call("POST", "/api/aisles/delete", { id }),
  dismissAisle: (label: string) => call("POST", "/api/aisles/dismiss", { label }),
  indexBytes: () => call<{ bytes: number }>("GET", "/api/facts?quick=1"),
  facts: () => call<{ files: number; activeFiles: number; vectors: number; bytes: number; index: string; logs: string }>("GET", "/api/facts"),
  openFolder: (which: "index" | "logs") => call("POST", "/api/folder", { which }),
  // `q`: what was searched for it, remembered with it (history)
  open: (path: string, loc: number | null, q?: string) => call("POST", "/api/open", { path, loc, q }),
  reveal: (path: string, q?: string) => call("POST", "/api/reveal", { path, q }),
  copy: (path: string, as: "file" | "path", q?: string) => call("POST", "/api/copy", { path, as, q }),
  library: (view: string) =>
    call<{ view: string; entries: Entry[]; counts: Record<string, number> }>(
      "GET", `/api/library?view=${encodeURIComponent(view)}`),
  choose: (op: "add" | "remove" | "restore" | "readAgain" | "activate" | "deactivate", payload: Record<string, unknown>) =>
    call<{ before?: Selection; changed?: boolean; refused?: Record<string, string>; blocked?: Record<string, string> }>("POST", "/api/choose",
      { op, ...payload }),
  pick: (what: "files" | "folder") => call<{ paths: string[] }>("POST", "/api/pick", { what }),
  settings: (changes: Record<string, unknown>) =>
    call<{ ok: boolean; why?: string; state?: AppState }>("POST", "/api/settings", changes),
  models: () => call<{ selected: string; models: SearchModel[]; job: ModelJob | null; hasIndex: boolean }>("GET", "/api/models"),
  changeModel: (model: string) => call<{ ok: boolean; job: ModelJob | null }>("POST", "/api/models/change", { model, confirmRebuild: true }),
  cancelModel: () => call<{ ok: boolean }>("POST", "/api/models/cancel", {}),
  window: (action: "main" | "popup" | "hidePopup" | "quit", route?: string) =>
    call<{ native: boolean }>("POST", "/api/window", { action, route }),
  setup: () => call<{ folders: { name: string; path: string; counts: Record<string, number> | null }[];
    progress: SetupProgress | null; gpu: string | null }>("GET", "/api/setup"),
  setupStart: (model?: string, confirmRebuild = false) =>
    call<{ ok: boolean; job: ModelJob | null }>("POST", "/api/setup/start", { model, confirmRebuild }),
  setupCount: (path: string) => call<{ counts: Record<string, number> | null }>("POST", "/api/setup/count", { path }),
  setupEstimate: (counts: Record<string, number>, model?: string) =>
    call<{ seconds: number; measured: boolean }>("POST", "/api/setup/estimate", { counts, model }),
  setupFinish: (folders: string[], startup: boolean) => call("POST", "/api/setup/finish", { folders, startup }),
};

export interface SetupProgress {
  stage: "runtime" | "model" | "validate" | "speed" | "activate" | "done";
  done: number;
  total: number;
  rate: number;
  left: number | null;
}

/** A picture of the part that matched: the photo, the marked PDF page, the video frame. */
export function pictureUrl(path: string, loc: number | null, px: number, terms: string[] = [], whole = false): string {
  const query = new URLSearchParams({ path, px: String(px) });
  if (loc !== null && loc !== undefined) query.set("loc", String(loc));
  if (terms.length) query.set("terms", terms.join(","));
  if (whole) query.set("whole", "1");   // a PDF page whole, not cropped to its marks
  return `/api/picture?${query}`;
}

export function mediaUrl(path: string): string {
  return `/api/media?path=${encodeURIComponent(path)}`;
}

/** Named events from the app: status, model, indexChanged, selection, theme, setup… */
export function listen(handlers: Record<string, (data: any) => void>): () => void {
  let source: EventSource | null = null;
  let closed = false;
  const connect = () => {
    source = new EventSource("/api/events");
    for (const [name, handler] of Object.entries(handlers)) {
      source.addEventListener(name, (event) => handler(JSON.parse((event as MessageEvent).data)));
    }
    source.onerror = () => {
      source?.close();
      if (!closed) setTimeout(connect, 1500);   // the app restarting: come back when it's up
    };
  };
  connect();
  return () => { closed = true; source?.close(); };
}
