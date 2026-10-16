// What every view reads: the app's state, kept live by its events, and where the user is (the hash).

import { api, listen, type AppState, type Status, type Selection } from "./api";

class Dowse {
  state = $state<AppState | null>(null);
  route = $state(location.hash.slice(1) || "/search");
  indexVersion = $state(0);   // bumps when indexing changed what's searchable
  mapVersion = $state(0);     // Explore's map changed: the floor, the rail's aisles and Aisles follow this
  error = $state<string | null>(null);
  notice = $state<{ id: number; text: string; undo?: Selection } | null>(null);
  private noticeId = 0;

  tell(text: string, undo?: Selection): void { this.notice = { id: ++this.noticeId, text, undo }; }

  async undo(): Promise<void> {
    const notice = this.notice;
    if (!notice?.undo) return;
    try { await api.choose("restore", { selection: notice.undo }); await this.refresh();
          if (this.notice?.id === notice.id) this.notice = null; }
    catch (error) { this.error = String(error); }
  }

  async start(): Promise<void> {
    const routeChanged = () => { this.route = location.hash.slice(1) || "/search"; };
    addEventListener("hashchange", routeChanged);
    routeChanged();   // native commands may navigate while authentication and state are still loading
    await api.session();
    this.state = await api.state();
    routeChanged();
    this.applyTheme();
    listen({
      status: (status: Status) => { if (this.state) this.state.status = status; },
      model: ({ state }) => { if (this.state) this.state.model = state; },
      selection: (selection) => { if (this.state) this.state.selection = selection; this.indexVersion++; this.mapVersion++; },
      indexChanged: () => { this.indexVersion++; },
      aislesChanged: () => { this.indexVersion++; this.mapVersion++; },
      mapChanged: () => { this.mapVersion++; },   // Explore's map made again (after files were read)
      screenshots: ({ paths }: { paths: string[] }) => {
        const name = paths[0]?.split(/[\\/]/).pop() ?? "";
        this.tell(paths.length === 1 ? `${name} added to Dowse` : `${paths.length} screenshots added to Dowse`);
      },
      theme: (theme) => { if (this.state) { this.state.theme = theme; this.applyTheme(); } },
      modelChanged: () => { this.indexVersion++; this.refresh().catch((error) => { this.error = String(error); }); },
    });
  }

  async refresh(): Promise<void> {
    this.state = await api.state();
  }

  applyTheme(): void {
    document.documentElement.dataset.theme = this.state?.theme.dark ? "dark" : "light";
  }

  go(route: string): void {
    this.route = route;
    location.hash = route;
  }

  replace(route: string): void {
    this.route = route;
    history.replaceState(null, "", location.pathname + location.search + "#" + route);
  }

  get path(): string {
    return this.route.split("?")[0];
  }

  get params(): URLSearchParams {
    return new URLSearchParams(this.route.split("?")[1] ?? "");
  }
}

export const dowse = new Dowse();

// How things read

export const KIND_NAME: Record<string, string> = {
  image: "Photo", screenshot: "Screenshot", pdf: "PDF", doc: "Document", audio: "Recording", video: "Video",
};

export const KIND_PLURAL: Record<string, string> = {
  image: "Photos", screenshot: "Screenshots", pdf: "PDFs", doc: "Documents", audio: "Recordings", video: "Videos",
};

export function size(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "";
  let value = bytes;
  for (const unit of ["bytes", "KB", "MB", "GB"]) {
    if (value < 1024 || unit === "GB") return unit === "bytes" ? `${value} bytes` : `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${bytes} bytes`;
}

export function when(timestamp: number | null, precise = false): string {
  if (!timestamp) return "";
  const then = new Date(timestamp * 1000);
  const now = new Date();
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(now) - day(then)) / 86_400_000);
  const at = precise ? ` at ${then.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}` : "";
  if (days < 1) return "Today" + at;
  if (days < 2) return "Yesterday" + at;
  if (days < 7) return then.toLocaleDateString(undefined, { weekday: "long" }) + at;
  if (then.getFullYear() === now.getFullYear()) return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** An excerpt with «marked» words as runs. */
export function runs(excerpt: string): { text: string; marked: boolean }[] {
  const out: { text: string; marked: boolean }[] = [];
  let marked = false;
  for (const piece of excerpt.replaceAll("»", "«").split("«")) {
    if (piece) out.push({ text: piece, marked });
    marked = !marked;
  }
  return out;
}

export function duration(seconds: number): string {
  if (seconds < 60) return "under a minute";
  if (seconds < 3600) { const m = Math.round(seconds / 60); return `about ${m} minute${m === 1 ? "" : "s"}`; }
  const h = Math.round(seconds / 360) / 10;
  return `about ${h} hour${h === 1 ? "" : "s"}`;
}
