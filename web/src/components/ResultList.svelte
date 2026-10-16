<script lang="ts">
  // The finds as a list: dense, scannable rows for when the words matter more than the pictures.

  import { pictureUrl, type Hit } from "../lib/api";
  import { runs, when } from "../lib/app.svelte";
  import { machineLine } from "../lib/search.svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "./Icon.svelte";
  import { CLOSEST_LABEL } from "../lib/search-confidence";

  let { hits, selected = 0, terms = [], compact = false, searching = false, clear = true, onselect, onopen,
        openedCount = 0, lastOpened = null }: {
    hits: Hit[];
    selected?: number;
    terms?: string[];
    compact?: boolean;
    searching?: boolean;
    clear?: boolean;
    onselect: (index: number) => void;
    onopen: (index: number) => void;
    openedCount?: number;          // how many lead as gone back to lately (nothing searched)
    lastOpened?: string | null;    // the file opened last time these words were searched
  } = $props();

  let list: HTMLOListElement;
  let unseen = $state(new Set<string>());   // files with nothing to show (a black recording): their kind instead
  const ICON: Record<string, IconName> = { image: "image", screenshot: "screenshot", pdf: "pdf", doc: "doc",
                                           audio: "audio", video: "video" };

  $effect(() => {   // keep the selected row in view
    void selected; void hits;
    list?.querySelector(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  });

  const firstWeak = $derived(searching && clear ? hits.findIndex((hit) => !hit.strong) : -1);
</script>

<ol class="results" class:compact bind:this={list} role="listbox" aria-label="Results"
    data-relevance={searching ? clear ? "match" : "closest" : "neutral"}>
  {#each hits as hit, index (hit.path)}
    {#if index === 0 && searching && !clear}<li class="divider" role="presentation">{CLOSEST_LABEL}</li>
    {:else if index === firstWeak && index > 0}<li class="divider" role="presentation">Other candidates</li>
    {:else if !searching && openedCount && index === 0}<li class="divider" role="presentation">Opened recently</li>
    {:else if !searching && openedCount && index === openedCount}<li class="divider" role="presentation">All files</li>{/if}
    <li role="option" aria-selected={index === selected} data-index={index}
        data-relevance={searching ? clear && hit.strong ? "match" : "closest" : "neutral"}>
      <button class="row" class:on={index === selected} onclick={() => onselect(index)} ondblclick={() => onopen(index)}>
        <span class="thumb">
          {#if hit.visual && !unseen.has(hit.path)}
            <img src={pictureUrl(hit.path, hit.loc, 160, hit.kind === "pdf" ? terms : [])} alt="" loading="lazy"
                 onerror={() => (unseen = new Set(unseen).add(hit.path))} />
          {:else}
            <Icon name={ICON[hit.kind]} size={22} />
          {/if}
        </span>
        <span class="words">
          <span class="name">{hit.name}{#if lastOpened === hit.path}<span class="before" title="You opened this the last time you searched these words">Opened last time</span>{/if}</span>
          {#if hit.excerpt}
            <span class="excerpt">{#each runs(hit.excerpt) as run}{#if run.marked}<mark>{run.text}</mark>{:else}{run.text}{/if}{/each}</span>
          {:else}
            <span class="excerpt">{hit.folder}</span>
          {/if}
        </span>
        <span class="side">
          <span class="data">{hit.place ?? ""}</span>
          <span class="when">{when(hit.mtime)}</span>
        </span>
      </button>
    </li>
  {/each}
</ol>

<style>
  .results { list-style: none; margin: 0; padding: var(--s-2) var(--s-4) var(--s-6); overflow-y: auto; height: 100%; }
  .compact { padding: var(--s-1) var(--s-2) var(--s-3); }
  .divider {
    margin: var(--s-4) var(--s-3) var(--s-2);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-3);
  }
  .row {
    width: 100%;
    display: grid;
    grid-template-columns: 56px minmax(0, 1fr) auto;
    gap: var(--s-3);
    align-items: center;
    padding: 8px var(--s-3);
    border-radius: var(--r-field);
    text-align: left;
    transition: background var(--t-rail) var(--rail);
  }
  .compact .row { grid-template-columns: 46px minmax(0, 1fr) auto; padding: 6px var(--s-2); }
  .row:hover { background: var(--floor-sunk); }
  .row.on { background: var(--found-wash); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--found) 45%, transparent); }
  .results li[data-relevance="closest"] .row.on { background: color-mix(in srgb, var(--focus) 8%, var(--floor));
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--focus) 45%, transparent); }

  .thumb {
    width: 56px; height: 42px;
    border-radius: 5px;
    overflow: hidden;
    display: grid;
    place-items: center;
    background: var(--floor-sunk);
    color: var(--ink-2);
    box-shadow: inset 0 0 0 1px var(--glass-shade);
  }
  .compact .thumb { width: 46px; height: 36px; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }

  .words { display: grid; gap: 1px; min-width: 0; }
  .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .before { margin-left: var(--s-2); padding: 1px 6px; border-radius: var(--r-steel); background: var(--tag);
            color: var(--tag-ink); font-size: var(--text-xs); font-weight: 500; vertical-align: 1px; }
  .excerpt { color: var(--ink-2); font-size: var(--text-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .side { display: grid; justify-items: end; gap: 2px; color: var(--ink-3); }
  .when { font-size: var(--text-xs); }
</style>
