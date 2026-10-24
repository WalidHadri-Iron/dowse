<script lang="ts">
  // Quick search is the compact mode of the same window and the same live search as the workspace.
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { primary, shortcut } from "../lib/keys";
  import { dowse } from "../lib/app.svelte";
  import { search } from "../lib/search.svelte";
  import { resultSummary } from "../lib/search-confidence";
  import { windowMode } from "../lib/window.svelte";
  import Counter from "../components/Counter.svelte";
  import ResultList from "../components/ResultList.svelte";
  import ViewingTable from "../components/ViewingTable.svelte";
  import Icon from "../components/Icon.svelte";
  import Brand from "../components/Brand.svelte";
  import BrowsePager from "../components/BrowsePager.svelte";
  import ResultFocus from "../components/ResultFocus.svelte";
  import LikeDrop from "../components/LikeDrop.svelte";
  import PastQueries from "../components/PastQueries.svelte";

  let input = $state<HTMLInputElement | null>(null);

  function hide(): void { api.window("hidePopup").catch((error) => (dowse.error = error.message)); }

  onMount(() => {
    const shown = () => { input?.focus(); input?.select(); api.warm().catch(() => {}); search.loadHistory(); };
    addEventListener("dowse:shown", shown);
    input?.focus();
    return () => {
      removeEventListener("dowse:shown", shown);
    };
  });

  function open(index: number, reveal = false): void {
    const hit = search.hits[index];
    if (!hit) return;
    (reveal ? api.reveal(hit.path, search.query) : api.open(hit.path, hit.loc, search.query))
      .catch((error) => (dowse.error = error.message));
  }

  function keys(event: KeyboardEvent): void {
    const target = event.target as HTMLElement;
    if (event.key === "Escape") {
      event.preventDefault();
      if (search.query) search.query = "";
      else if (search.like) search.dropLike();
      else if (search.within.length) search.within = [];
      else hide();
      return;
    }
    if (target !== input && target !== document.body && !target.closest(".results")) return;
    if (event.key === "ArrowDown") { search.move(1); event.preventDefault(); }
    else if (event.key === "ArrowUp") { search.move(-1); event.preventDefault(); }
    else if (event.key === "PageDown") { search.move(6); event.preventDefault(); }
    else if (event.key === "PageUp") { search.move(-6); event.preventDefault(); }
    else if (event.key === "Enter" && search.current) {
      event.preventDefault(); open(search.selected, primary(event));
    } else if (event.key.toLowerCase() === "c" && primary(event) && search.current
               && !window.getSelection()?.toString() && input?.selectionStart === input?.selectionEnd) {
      api.copy(search.current.path, event.shiftKey ? "path" : "file", search.query); event.preventDefault();
    }
  }

  const status = $derived(dowse.state?.status);
  const clear = $derived(!search.searching || search.clear);
  const note = $derived(search.searching
    ? search.busy ? search.like?.file ? "Reading it…" : "Searching…" : search.answer ? `${resultSummary({strong: search.matchCount}, search.clear ? search.matchCount : search.hits.length)}${search.answer.when ? ` from ${search.answer.when}` : ""}${search.answer.meaning ? "" : " · words only"}` : "Searching…"
    : search.browseBusy ? "Loading files…" : search.browseError ? "Couldn’t load files" : `${search.browseTotal.toLocaleString()} files${search.filters.length ? " in these filters" : " · all searchable files"}`);
</script>

<svelte:window onkeydown={keys} />

<section class="popup" aria-label="Quick search">
  <header>
    <div class="identity"><Brand height={26} /><span class="mode-label">Quick search</span></div>
    <div class="controls">
      <button class="expand" onclick={() => windowMode.change(false)} disabled={windowMode.switching}
              title="Expand this window to the workspace ({shortcut('O')})">
        <Icon name="expand" size={16} />Workspace <kbd>{shortcut("O")}</kbd>
      </button>
    </div>
  </header>

  <div class="counter">
    <Counter bind:query={search.query} bind:kinds={search.kinds} bind:input within={search.within} compact
             {note}
             filters={search.filters} bind:date={search.date} onremovefilter={(id, path) => search.removeFilter(id, path)} onsetkinds={(kinds) => search.setKinds(kinds)}
             onsetscope={(path) => (search.within = path)}
             like={search.like} onclearlike={() => search.dropLike()} suggestion={search.suggestion}
             ontakesuggestion={() => search.takeSuggestion()} onchooseexample={() => search.chooseExample()} />
    <PastQueries compact />
    <ResultFocus />
  </div>

  <div class="body" class:empty={!search.hits.length} aria-busy={search.busy}>
    <div class="list">
      <div class="collection">
      {#if search.hits.length}
        <ResultList hits={search.hits} selected={search.selected} terms={search.answer?.terms ?? []} compact
                    searching={search.searching} {clear} openedCount={search.openedCount} lastOpened={search.lastOpened}
                    onselect={(i) => (search.selected = i)} onopen={open} />
      {:else}
        <div class="nothing">
          <Icon name="search" size={28} />
          <h2 class="sign">{search.searching && search.answer && !search.busy ? search.like ? `Nothing like ${search.like.name} here` : "Nothing matches yet" : !search.searching ? search.browseBusy ? "Loading your collection…" : search.browseError ? "Couldn’t load your files" : "No searchable files here" : "Find a file in a few words"}</h2>
          <p>{search.searching ? search.busy ? search.like?.file ? "Reading it to compare…" : "Searching your files…" : "Try another description or search all file types."
            : search.browseError || (search.filters.length ? "Try removing a folder, date, or file-type filter." : status?.state === "reading" ? "Your files appear here as Dowse reads them." : "Add files in the Library to start browsing." )}</p>
        </div>
      {/if}
      </div>
      {#if !search.searching}
        <BrowsePager total={search.browseTotal} offset={search.browseOffset} shown={search.hits.length}
          more={search.browseMore} busy={search.browseBusy} error={search.browseError}
          onpage={(direction) => search.browsePage(direction)} onretry={() => search.loadBrowse()} />
      {/if}
    </div>
    {#if search.hits.length}
      <ViewingTable item={search.current} terms={search.answer?.terms ?? []} query={search.query} compact
                    confidence={!search.busy && search.searching && search.answer?.query === search.query ? clear && search.current?.strong ? "match" : "closest" : null}
                    onopen={() => open(search.selected)}
                    onlike={(loc, place) => { if (search.current) search.likeFile(search.current, loc, place); }} />
    {/if}
  </div>

  <footer>
    <span class="status" title={status?.text}><span class="dot" class:busy={status?.state === "reading"}></span>{status?.text}</span>
    <span class="keys"><span><kbd>↑ ↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> clear / close</span></span>
  </footer>
</section>

<LikeDrop compact ondrop={(file) => search.likeDropped(file)} />

<style>
  .popup { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; height: 100%; background: var(--floor); }
  header { display: flex; justify-content: space-between; align-items: center; gap: var(--s-3);
           padding: var(--s-3) var(--s-4) 0; }
  .identity, .controls { display: flex; align-items: center; gap: var(--s-2); }
  .mode-label { border-left: 1px solid var(--hairline); padding-left: var(--s-3);
    margin-left: var(--s-1); color: var(--ink-2); font-size: var(--text-sm); white-space: nowrap; }
  .controls button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px;
                     border-radius: var(--r-field); font-size: var(--text-sm); }
  .expand { background: var(--steel); color: var(--steel-ink); }
  .expand:hover { background: var(--steel-raised); }
  .expand kbd { background: var(--steel-raised); color: var(--steel-ink-2); margin-left: var(--s-1); }
  .counter { padding: var(--s-3) var(--s-4); }
  .body { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); min-height: 0; border-top: 1px solid var(--hairline); }
  .body.empty { grid-template-columns: 1fr; }
  .list { min-width: 0; min-height: 0; display: grid; grid-template-rows: minmax(0, 1fr) auto; }
  .collection { position: relative; min-width: 0; min-height: 0; }
  .body :global(.table) { border-left: 1px solid var(--hairline); }
  .nothing { display: grid; place-content: center; justify-items: center; gap: var(--s-2); height: 100%;
             padding: var(--s-5); text-align: center; color: var(--ink-3); }
  .nothing h2 { margin: 0; font-size: var(--text-lg); color: var(--ink); }
  .nothing p { margin: 0; max-width: 46ch; font-size: var(--text-sm); }
  footer { display: flex; justify-content: space-between; align-items: center; gap: var(--s-3); min-width: 0;
           padding: var(--s-2) var(--s-4); border-top: 1px solid var(--hairline); font-size: var(--text-xs); color: var(--ink-3); }
  .status { display: flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--found); }
  .dot.busy { animation: breathe 1.6s var(--glide) infinite alternate; }
  @keyframes breathe { to { opacity: 0.3; } }
  .keys { display: flex; gap: var(--s-3); align-items: center; flex: none; }
  kbd { padding: 1px 5px; border-radius: var(--r-steel); background: var(--tag); color: var(--tag-ink);
        font: 400 10px / 1.4 var(--face-data); }
  @media (max-width: 700px) { .mode-label { display: none; } .keys span:first-child { display: none; } }
  @media (prefers-reduced-motion: reduce) { .dot.busy { animation: none; } }
</style>
