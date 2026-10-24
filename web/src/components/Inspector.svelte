<script lang="ts">
  // The file up close, filling the window: a photo to zoom into and pan, a PDF's pages to turn (the words marked on
  // every one, the pages that matched to jump to), a recording or video with its matching moments on the timeline, a
  // document's passages to read through. ↑ ↓ still move through the results (the inspector follows), Esc closes.
  // "Like this" finds what's like the page, passage or moment in view (a photo: the photo).

  import { onMount, onDestroy } from "svelte";
  import { api, mediaUrl, pictureUrl } from "../lib/api";
  import { primary, shortcut } from "../lib/keys";
  import { KIND_NAME, clock, runs, size } from "../lib/app.svelte";
  import Icon from "./Icon.svelte";
  import Zoomable from "./Zoomable.svelte";
  import Timeline from "./Timeline.svelte";
  import type { Viewed } from "./ViewingTable.svelte";
  import MatchEvidence from "./MatchEvidence.svelte";

  let { item, terms = [], query = "", onclose, confidence = null, onlike }: {
    item: Viewed;
    terms?: string[];
    query?: string;
    onclose: () => void;
    confidence?: "match" | "closest" | null;
    onlike?: (loc: number | null, place: string | null) => void;   // more like the place in view
  } = $props();

  // Pictures the window can show as they are; the rest (HEIC, TIFF…) through Dowse's own rendering
  const SHOWN = /\.(jpe?g|png|gif|webp|bmp|avif)$/i;

  let zoomer = $state<Zoomable | null>(null);
  let zoom = $state(1);
  let page = $state(1);
  let reading = $state(false);
  let passage = $state<{ text: string | null; loc: number | null; count: number | null } | null>(null);
  let passageRequest: AbortController | null = null;
  onDestroy(() => passageRequest?.abort());
  let player = $state<HTMLMediaElement | null>(null);
  let timeline = $state<Timeline | null>(null);
  let duration = $state(0), now = $state(0);

  let lastPage = $state<number | null>(null);   // found by trying, when the file's page count isn't known
  const pages = $derived(Number(item.info?.pages ?? 0) || lastPage);
  // where it matched first, and the other places that match too
  const moments = $derived([...(item.loc !== null ? [{ loc: item.loc, locEnd: null, place: item.place }] : []), ...item.moments]
    .sort((a, b) => a.loc - b.loc));
  const matchedPages = $derived(item.kind === "pdf" ? [...new Set(moments.map((m) => Math.round(m.loc)))].sort((a, b) => a - b) : []);
  const picture = $derived(item.kind === "image" || item.kind === "screenshot"
    ? (SHOWN.test(item.path) ? mediaUrl(item.path) : pictureUrl(item.path, null, 2400)) : null);

  // A new file: its own place
  $effect(() => {
    passageRequest?.abort();
    void item.path;
    page = Math.max(1, Math.round(item.loc ?? 1));
    lastPage = null;
    reading = false;
    duration = Number(item.info?.duration ?? 0);
    now = item.loc ?? 0;
    passage = null;
    if (item.kind === "doc") readPassage(item.loc !== null ? Math.round(item.loc) : null);
  });

  $effect(() => {   // a recording or video starts at the moment that matched
    if (!player) return;
    const at = item.loc ?? 0;
    const start = () => { if (player) { duration = player.duration || duration; player.currentTime = at; } };
    if (player.readyState >= 1) start(); else player.addEventListener("loadedmetadata", start, { once: true });
  });

  async function readPassage(loc: number | null): Promise<void> {
    passageRequest?.abort();
    const request = new AbortController(); passageRequest = request;
    const asked = item.path;
    try {
      const answer = await api.passage(0, query, item.path, loc, request.signal);
      if (!request.signal.aborted && asked === item.path) passage = answer;
    } catch { /* A newer passage/selection wins; missing files must not reject a keyboard handler. */ }
  }

  function turn(by: number): void {
    if (item.kind === "pdf") {
      page = Math.min(Math.max(page + by, 1), pages ?? page + Math.max(by, 0));
    } else if (item.kind === "doc" && passage?.loc !== null && passage?.loc !== undefined) {
      const next = passage.loc + by;
      if (next >= 0 && (passage.count === null || next < passage.count)) readPassage(next);
    } else if (player) {
      seek(player.currentTime + by * 5);
    }
  }

  function seek(seconds: number): void {
    if (!player) return;
    player.currentTime = Math.max(0, Math.min(seconds, duration || seconds));
    player.play().catch(() => {});
  }

  // Keys first, before the results list sees them (it keeps ↑ ↓ and typing)
  onMount(() => {
    const keys = (event: KeyboardEvent) => {
      const zoomable = !!picture || (item.kind === "pdf" && !reading);
      if (event.key === "Escape") { onclose(); }
      else if (event.key === "ArrowRight" || event.key === "PageDown") turn(1);
      else if (event.key === "ArrowLeft" || event.key === "PageUp") turn(-1);
      else if ((event.key === "]" || event.key === "[") && player) timeline?.step(event.key === "]" ? 1 : -1);
      else if (primary(event) && (event.key === "=" || event.key === "+") && zoomable) zoomer?.zoomBy(1.4);
      else if (primary(event) && event.key === "-" && zoomable) zoomer?.zoomBy(1 / 1.4);
      else if (primary(event) && event.key === "0" && zoomable) zoomer?.reset();
      else return;
      event.preventDefault();
      event.stopPropagation();
    };
    addEventListener("keydown", keys, true);
    return () => removeEventListener("keydown", keys, true);
  });

  const line = $derived([item.kind ? KIND_NAME[item.kind] : null, item.folder, size(item.size)].filter(Boolean).join("  ·  "));
  const openAt = $derived(item.kind === "pdf" ? page : item.kind === "audio" || item.kind === "video" ? now : item.loc);
  // the place in view, for "like this": a page, a passage, a moment (a picture is itself)
  const here = $derived.by((): { loc: number | null; place: string | null; label: string } => {
    if (item.kind === "pdf") return { loc: page, place: `Page ${page}`, label: "Like this page" };
    if (item.kind === "doc" && passage?.loc !== null && passage?.loc !== undefined)
      return { loc: passage.loc, place: `Passage ${passage.loc + 1}`, label: "Like this passage" };
    if (item.kind === "audio" || item.kind === "video") return { loc: Math.floor(now), place: clock(Math.floor(now)), label: "Like this moment" };
    return { loc: null, place: null, label: "More like this" };
  });
</script>

<div class="inspector" role="dialog" aria-modal="true" aria-label="{item.name}, up close">
  <header>
    <div class="title">
      <h2 class="sign selectable" title={item.path}>{item.name}</h2>
      <p class="data">{line}</p>
    </div>
    {#if picture || (item.kind === "pdf" && !reading)}
      <div class="zoom" role="group" aria-label="Zoom">
        <button onclick={() => zoomer?.zoomBy(1 / 1.4)} title="Zoom out ({shortcut('-')})" aria-label="Zoom out">−</button>
        <button class="data" onclick={() => zoomer?.reset()} title="The whole picture ({shortcut('0')})">{Math.round(zoom * 100)}%</button>
        <button onclick={() => zoomer?.zoomBy(1.4)} title="Zoom in ({shortcut('=')})" aria-label="Zoom in">+</button>
      </div>
    {/if}
    <div class="actions">
      <button class="primary" onclick={() => api.open(item.path, openAt, query)}>
        <Icon name="open" size={18} />Open{#if item.kind === "pdf"}&nbsp;at page {page}{:else if (item.kind === "audio" || item.kind === "video") && now > 0}&nbsp;at {clock(now)}{/if}
      </button>
      <button onclick={() => api.reveal(item.path, query)}><Icon name="folderOpen" size={18} />Show</button>
      {#if onlike}
        <button onclick={() => onlike(here.loc, here.place)} title="Find files like {here.place ? here.place.toLowerCase() : 'this one'}">
          <Icon name="similar" size={18} />{here.label}
        </button>
      {/if}
      <button class="close" onclick={onclose} title="Close (Esc)" aria-label="Close"><Icon name="dismiss" size={20} /></button>
    </div>
  </header>

  <div class="stage">
    {#if picture}
      <Zoomable bind:this={zoomer} src={picture} alt={item.name} onstate={(z) => (zoom = z)} />
    {:else if item.kind === "pdf" && reading}
      <iframe title={item.name} src="{mediaUrl(item.path)}#page={page}&view=FitH"></iframe>
    {:else if item.kind === "pdf"}
      <Zoomable bind:this={zoomer} src={pictureUrl(item.path, page, 2000, terms, true)} alt="{item.name}, page {page}"
                onstate={(z) => (zoom = z)} onerror={() => { if (page > 1) { page -= 1; lastPage = page; } }} />
    {:else if item.kind === "video"}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={player} src={mediaUrl(item.path)} controls preload="metadata"
             ontimeupdate={() => (now = player?.currentTime ?? 0)} ondurationchange={() => (duration = player?.duration || duration)}></video>
    {:else if item.kind === "audio"}
      <div class="sound">
        <Icon name="audio" size={48} />
        <audio bind:this={player} src={mediaUrl(item.path)} controls preload="metadata"
               ontimeupdate={() => (now = player?.currentTime ?? 0)} ondurationchange={() => (duration = player?.duration || duration)}></audio>
      </div>
    {:else if passage?.text}
      <article class="passage selectable">
        {#each runs(passage.text) as run}{#if run.marked}<mark>{run.text}</mark>{:else}{run.text}{/if}{/each}
      </article>
    {:else}
      <div class="empty"><Icon name="doc" size={56} /><p>Nothing to show of this file here. Open it to see it.</p></div>
    {/if}
  </div>

  <footer>
    <MatchEvidence evidence={item.evidence} {confidence} />
    {#if item.kind === "pdf"}
      <div class="pager">
        <button onclick={() => turn(-1)} disabled={page <= 1} aria-label="Previous page">‹</button>
        <span class="where data">Page {page}{#if pages}&nbsp;of {pages}{/if}</span>
        <button onclick={() => turn(1)} disabled={pages !== null && page >= pages} aria-label="Next page">›</button>
        {#if matchedPages.length}
          <span class="found-label">Matches on</span>
          {#each matchedPages as p}
            <button class="chip data" class:on={p === page} onclick={() => (page = p)} title="Page {p} matches">{p}</button>
          {/each}
        {/if}
        <span class="spacer"></span>
        <button class="chip" onclick={() => (reading = !reading)} aria-pressed={reading}>
          <Icon name="pdf" size={16} />{reading ? "Marked pages" : "Read in place"}
        </button>
      </div>
    {:else if item.kind === "doc" && passage?.loc !== null && passage?.loc !== undefined}
      <div class="pager">
        <button onclick={() => turn(-1)} disabled={passage.loc <= 0} aria-label="Previous passage">‹</button>
        <span class="where data">Passage {passage.loc + 1}{#if passage.count}&nbsp;of {passage.count}{/if}</span>
        <button onclick={() => turn(1)} disabled={passage.count !== null && passage.loc + 1 >= passage.count} aria-label="Next passage">›</button>
        {#if moments.length > 1}
          <span class="found-label">Matches in</span>
          {#each moments as m}
            <button class="chip data" class:on={Math.round(m.loc) === passage.loc} onclick={() => readPassage(Math.round(m.loc))}>¶ {Math.round(m.loc) + 1}</button>
          {/each}
        {/if}
      </div>
    {:else if item.kind === "audio" || item.kind === "video"}
      <Timeline bind:this={timeline} {duration} moments={moments} {now} onseek={seek} />
    {/if}
    <p class="keys">
      {#if item.kind === "pdf"}← → pages{:else if item.kind === "doc"}← → passages{:else if item.kind === "audio" || item.kind === "video"}[ ] moments · ← → 5 seconds{:else}wheel or {shortcut("=")} zoom · drag to look around · double-click{/if}
      · ↑ ↓ other results · Esc close
    </p>
  </footer>
</div>

<style>
  .inspector {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    background: color-mix(in srgb, var(--floor) 96%, transparent);
    backdrop-filter: blur(6px);
  }
  header {
    display: flex; align-items: center; gap: var(--s-4); padding: var(--s-3) var(--s-5);
    border-bottom: 1px solid var(--hairline); background: var(--floor-raised);
  }
  .title { flex: 1; min-width: 0; }
  h2 { margin: 0; font-size: var(--text-xl); line-height: 1.15; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: text; }
  .title p { margin: 2px 0 0; color: var(--ink-2); font-size: var(--text-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .zoom { display: flex; align-items: center; padding: 2px; border-radius: var(--r-field); background: var(--floor-sunk); }
  .zoom button { min-width: 34px; height: 30px; padding: 0 8px; border-radius: 4px; color: var(--ink); font-size: var(--text-md, 15px); }
  .zoom button:hover { background: var(--floor-raised); }
  .zoom .data { font-size: var(--text-xs); min-width: 52px; }
  .actions { display: flex; align-items: center; gap: var(--s-2); }
  .actions button {
    display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 12px; border-radius: var(--r-field);
    border: 1px solid var(--hairline); background: var(--floor-raised); font-size: var(--text-sm); font-weight: 500;
  }
  .actions button:hover { background: var(--floor-sunk); }
  .actions .primary { background: var(--steel); color: var(--steel-ink); border-color: var(--steel); }
  .actions .primary:hover { background: var(--steel-raised); }
  .actions .close { width: 36px; padding: 0; justify-content: center; border-color: transparent; background: transparent; }

  .stage { min-height: 0; padding: var(--s-4); display: grid; place-items: center; }
  .stage video { max-width: 100%; max-height: 100%; border-radius: 4px; box-shadow: var(--lift-3); background: var(--steel); }
  .stage iframe { width: min(100%, 1100px); height: 100%; border: 0; border-radius: 4px; box-shadow: var(--lift-2); background: var(--paper); }
  .sound { display: grid; justify-items: center; gap: var(--s-5); width: min(100%, 720px); color: var(--found-ink); }
  .sound audio { width: 100%; }
  .passage {
    align-self: stretch; overflow-y: auto; width: min(100%, 72ch); padding: var(--s-6) var(--s-6);
    background: var(--paper); color: #141821; border-radius: 4px; box-shadow: var(--lift-2);
    font: 400 17px / 1.7 var(--face-ui); white-space: pre-wrap;
  }
  .empty { display: grid; justify-items: center; gap: var(--s-3); color: var(--ink-3); }

  footer { display: grid; gap: var(--s-2); padding: var(--s-3) var(--s-5) var(--s-4); border-top: 1px solid var(--hairline); background: var(--floor-raised); }
  .pager { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .pager > button:not(.chip) { width: 32px; height: 30px; border-radius: var(--r-steel); font-size: 18px; color: var(--ink); }
  .pager > button:not(.chip):hover:not(:disabled) { background: var(--floor-sunk); }
  .pager > button:disabled { opacity: 0.35; }
  .where { min-width: 96px; text-align: center; font-size: var(--text-sm); }
  .found-label { margin-left: var(--s-3); color: var(--ink-3); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; }
  .chip { display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px; border-radius: var(--r-steel); background: var(--tag); color: var(--tag-ink); font-size: var(--text-xs); }
  .chip:hover { background: var(--found-wash); }
  .chip.on { box-shadow: inset 0 -2px 0 var(--found); }
  .spacer { flex: 1; }
  .keys { margin: 0; color: var(--ink-3); font-size: var(--text-xs); }
</style>
