<script lang="ts" module>
  export interface Viewed {
    path: string;
    name: string;
    folder: string;
    kind: string | null;
    loc: number | null;
    place: string | null;
    excerpt: string | null;
    mtime: number | null;
    size: number | null;
    info: Record<string, unknown>;
    segment: number;
    visual: boolean;
    moments: { loc: number; locEnd: number | null; place: string | null }[];
    evidence?: import("../lib/api").Evidence[];
  }
</script>

<script lang="ts">
  // The viewing table: the file taken out of its vitrine, at the place it matched. A photo whole, the PDF page with
  // the words marked (its pages to turn, the ones that matched to jump to, or the page itself, readable, in place),
  // the video or recording playing from its moment with the moments that matched on its timeline, a document's
  // passage. Under it, its tag and what can be done with it (Similar: the files most like it). Shift+Enter (or a
  // click on the picture) takes it up close: the inspector.

  import { onMount } from "svelte";
  import { api, mediaUrl, pictureUrl } from "../lib/api";
  import { KIND_NAME, runs, size, when } from "../lib/app.svelte";
  import Icon from "./Icon.svelte";
  import Inspector from "./Inspector.svelte";
  import SearchActions from "./SearchActions.svelte";
  import Timeline from "./Timeline.svelte";
  import MatchEvidence from "./MatchEvidence.svelte";

  let { item, terms = [], query = "", compact = false, onopen, confidence = null, onlike }: {
    item: Viewed | null;
    terms?: string[];
    query?: string;
    compact?: boolean;
    onopen?: () => void;
    confidence?: "match" | "closest" | null;
    onlike?: (loc: number | null, place: string | null) => void;   // more like this file (or a place in it)
  } = $props();

  let reading = $state(false);         // a PDF shown as the page itself (Edge's own viewer)
  let passage = $state<string | null>(null);
  let player = $state<HTMLMediaElement | null>(null);
  let copied = $state("");
  let inspecting = $state(false);
  let page = $state(1);                // the PDF page shown
  let duration = $state(0), now = $state(0);

  let lastPage = $state<number | null>(null);   // found by trying, when the file's page count isn't known
  const pages = $derived(item ? Number(item.info?.pages ?? 0) || lastPage : null);
  // where it matched first, and the other places that match too
  const moments = $derived(item ? [...(item.loc !== null ? [{ loc: item.loc, locEnd: null, place: item.place }] : []), ...item.moments]
    .sort((a, b) => a.loc - b.loc) : []);
  const matchedPages = $derived(item?.kind === "pdf" ? [...new Set(moments.map((m) => Math.round(m.loc)))].sort((a, b) => a - b) : []);

  // Shift+Enter: up close (Enter opens it, Ctrl+Enter shows it in its folder)
  onMount(() => {
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Enter" && event.shiftKey && !event.ctrlKey && item && !inspecting) {
        event.preventDefault();
        event.stopPropagation();
        inspecting = true;
      }
    };
    addEventListener("keydown", keys, true);
    return () => removeEventListener("keydown", keys, true);
  });

  $effect(() => {
    const request = new AbortController();
    void item?.path;
    page = Math.max(1, Math.round(item?.loc ?? 1));
    lastPage = null;
    duration = Number(item?.info?.duration ?? 0);
    now = item?.loc ?? 0;
    reading = false;
    passage = null;
    copied = "";
    let timer = 0;
    if (item && (item.kind === "doc" || (item.kind === "pdf" && !item.visual))) {
      const asked = item.path;
      const segment = item.segment, text = query, path = item.segment ? "" : item.path;
      // Wait for a brief pause in typing/arrow navigation, then read only the latest selection.
      timer = window.setTimeout(() => {
        api.passage(segment, text, path, null, request.signal)
          .then((answer) => { if (!request.signal.aborted && item?.path === asked) passage = answer.text; })
          .catch(() => { /* Cancelled, or the file became unavailable. */ });
      }, 100);
    }
    return () => { clearTimeout(timer); request.abort(); };
  });

  $effect(() => {   // a recording or video starts at the moment that matched
    if (player && item?.loc !== null && item?.loc !== undefined) {
      const at = item.loc;
      const seek = () => { if (player) player.currentTime = at; };
      if (player.readyState >= 1) seek(); else player.addEventListener("loadedmetadata", seek, { once: true });
    }
  });

  function seek(loc: number): void {
    if (player) { player.currentTime = loc; player.play().catch(() => {}); }
  }

  async function copy(as: "file" | "path"): Promise<void> {
    if (!item) return;
    await api.copy(item.path, as, query);
    copied = as;
    setTimeout(() => { copied = ""; }, 1600);
  }

  const line = $derived(item ? [item.kind ? KIND_NAME[item.kind] : "Folder", item.place, item.folder,
                                 size(item.size)].filter(Boolean).join("  ·  ") : "");
</script>

{#if item}
  <section class="table" class:compact aria-label="Preview of {item.name}">
    <div class="stage">
      {#if item.kind === "video"}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={player} src={mediaUrl(item.path)} controls preload="metadata"
               poster={pictureUrl(item.path, item.loc, 1200)} ontimeupdate={() => (now = player?.currentTime ?? 0)}
               ondurationchange={() => (duration = player?.duration || duration)}></video>
      {:else if item.kind === "audio"}
        <div class="sound">
          <div class="wave" aria-hidden="true">
            {#each Array.from({ length: 48 }, (_, i) => i) as i}
              <span style:height="{20 + Math.abs(Math.sin(i * 1.7 + item.name.length)) * 70}%"></span>
            {/each}
          </div>
          <audio bind:this={player} src={mediaUrl(item.path)} controls preload="metadata"
                 ontimeupdate={() => (now = player?.currentTime ?? 0)}
                 ondurationchange={() => (duration = player?.duration || duration)}></audio>
        </div>
      {:else if item.kind === "pdf" && reading}
        <iframe title={item.name} src="{mediaUrl(item.path)}#page={page}&view=FitH"></iframe>
      {:else if item.visual}
        <button class="look" onclick={() => (inspecting = true)} title="Look closer (Shift+Enter)">
          <img src={pictureUrl(item.path, item.kind === "pdf" ? page : item.loc, compact ? 900 : 1400, item.kind === "pdf" ? terms : [])}
               alt={item.name} onerror={() => { if (item?.kind === "pdf" && page > 1) { page -= 1; lastPage = page; } }} />
        </button>
      {:else if passage}
        <article class="passage selectable">
          {#each runs(passage) as run}{#if run.marked}<mark>{run.text}</mark>{:else}{run.text}{/if}{/each}
        </article>
      {:else}
        <div class="empty"><Icon name="doc" size={40} /></div>
      {/if}
    </div>

    {#if item.kind === "audio" || item.kind === "video"}
      <Timeline {duration} {moments} {now} onseek={seek} {compact} />
    {:else if item.kind === "pdf"}
      <div class="pager">
        <button onclick={() => (page = Math.max(page - 1, 1))} disabled={page <= 1} aria-label="Previous page">‹</button>
        <span class="where data">Page {page}{#if pages}&nbsp;of {pages}{/if}</span>
        <button onclick={() => (page = pages ? Math.min(page + 1, pages) : page + 1)} disabled={pages !== null && page >= pages}
                aria-label="Next page">›</button>
        {#if matchedPages.length > 1 || (matchedPages.length === 1 && matchedPages[0] !== page)}
          <span class="matches">Matches</span>
          {#each matchedPages as p}
            <button class="chip data" class:on={p === page} onclick={() => (page = p)} title="Page {p} matches">{p}</button>
          {/each}
        {/if}
      </div>
    {/if}

    <div class="label">
      <h2 class="sign selectable" title={item.path}>{item.name}</h2>
      <p class="data line">{line}</p>
      {#if item.mtime}<p class="when">Modified {when(item.mtime, true)}</p>{/if}
      <MatchEvidence evidence={item.evidence} {confidence} />
      {#if item.excerpt && item.kind !== "doc"}
        <p class="excerpt selectable">
          {#each runs(item.excerpt) as run}{#if run.marked}<mark>{run.text}</mark>{:else}{run.text}{/if}{/each}
        </p>
      {/if}
    </div>

    <div class="actions">
      <button class="primary" onclick={() => onopen ? onopen() : api.open(item.path, item.loc, query)}>
        <Icon name="open" size={18} />Open{#if item.place && item.kind !== "doc"}&nbsp;at {item.place}{/if}
      </button>
      <button onclick={() => api.reveal(item.path, query)} title="Show in folder (Ctrl+Enter)"><Icon name="folderOpen" size={18} />Show</button>
      <button onclick={() => copy("file")} title="Copy the file (Ctrl+C)"><Icon name={copied === "file" ? "check" : "copy"} size={18} />{copied === "file" ? "Copied" : "Copy"}</button>
      {#if item.kind === "pdf"}
        <button onclick={() => (reading = !reading)} aria-pressed={reading}><Icon name="pdf" size={18} />{reading ? "Marked" : "Read here"}</button>
      {/if}
      <button onclick={() => (inspecting = true)} title="Look closer (Shift+Enter)"><Icon name="expand" size={18} />Up close</button>
      {#if onlike && item.kind}
        <button onclick={() => onlike(null, null)} title="Find the files most like this one"><Icon name="similar" size={18} />Similar</button>
      {/if}
    </div>
    <SearchActions path={item.path} name={item.name} {compact} />
  </section>
  {#if inspecting}
    <Inspector {item} {terms} {query} {confidence} onclose={() => (inspecting = false)}
               onlike={onlike ? (loc, place) => { inspecting = false; onlike(loc, place); } : undefined} />
  {/if}
{/if}

<style>
  .table {
    display: grid;
    grid-template-rows: max-content auto auto auto;   /* scroll the table rather than cropping the picture for a longer label */
    align-content: start;
    gap: var(--s-4);
    height: 100%;
    padding: var(--s-5);
    background: var(--table);
    border-left: 1px solid var(--hairline);
    min-height: 0;
    overflow-y: auto;
  }
  .compact { padding: var(--s-4); gap: var(--s-3); border-left: 0; }
  .compact .stage img, .compact .stage video { max-height: min(32vh, 200px); }

  .stage {
    display: grid;
    place-items: start center;   /* the picture from the top, at its own shape: no floating in the middle */
    min-height: 0;
    border-radius: var(--r-steel);
    overflow: hidden;
  }
  .stage img, .stage video {
    max-width: 100%;
    max-height: min(62vh, 100%);
    border-radius: 4px;
    box-shadow: var(--lift-3);
    background: var(--paper);
  }
  .stage iframe { width: 100%; height: 100%; border: 0; border-radius: 4px; box-shadow: var(--lift-2); background: var(--paper); }

  .sound { width: 100%; display: grid; gap: var(--s-4); }
  .wave { height: 120px; display: flex; align-items: center; gap: 3px; }
  .wave span { flex: 1; background: var(--found); border-radius: 2px; opacity: 0.75; }
  audio { width: 100%; }

  .passage {
    align-self: stretch;
    width: 100%;
    overflow-y: auto;
    padding: var(--s-5);
    background: var(--paper);
    color: #141821;
    border-radius: 4px;
    box-shadow: var(--lift-2);
    font: 400 var(--text-md) / 1.6 var(--face-ui);
    white-space: pre-wrap;
    max-width: 68ch;
  }
  .empty { color: var(--ink-3); }

  .look { display: grid; place-items: start center; max-width: 100%; padding: 0; border: 0; background: none; cursor: zoom-in; }
  .look:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; border-radius: 4px; }
  .pager { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .pager > button:not(.chip) { width: 30px; height: 28px; border-radius: var(--r-steel); font-size: 17px; color: var(--ink); }
  .pager > button:not(.chip):hover:not(:disabled) { background: var(--floor-sunk); }
  .pager > button:disabled { opacity: 0.35; }
  .where { min-width: 84px; text-align: center; font-size: var(--text-sm); }
  .matches { margin-left: var(--s-2); color: var(--ink-3); font-size: var(--text-xs); }
  .chip { min-width: 26px; padding: 2px 6px; border-radius: var(--r-steel); background: var(--tag); color: var(--tag-ink); font-size: var(--text-xs); }
  .chip:hover { background: var(--found-wash); }
  .chip.on { box-shadow: inset 0 -2px 0 var(--found); }

  .label { display: grid; gap: 4px; min-width: 0; }
  h2 {
    margin: 0;
    font-size: var(--text-xl);
    line-height: 1.15;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    user-select: text;
  }
  .compact h2 { font-size: var(--text-lg); }
  .line { margin: 0; color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .when { margin: 0; color: var(--ink-3); font-size: var(--text-sm); }
  .excerpt {
    margin: var(--s-2) 0 0;
    color: var(--ink-2);
    font-size: var(--text-sm);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .actions { display: flex; flex-wrap: wrap; gap: var(--s-2); }
  .actions button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 12px;
    border-radius: var(--r-field);
    border: 1px solid var(--hairline);
    background: var(--floor-raised);
    font-size: var(--text-sm);
    font-weight: 500;
    transition: background var(--t-rail) var(--rail);
  }
  .actions button:hover { background: var(--floor-sunk); }
  .actions .primary { background: var(--steel); color: var(--steel-ink); border-color: var(--steel); }
  .actions .primary:hover { background: var(--steel-raised); }
</style>
