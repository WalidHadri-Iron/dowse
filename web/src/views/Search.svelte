<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { search, machineLine } from "../lib/search.svelte";
  import Counter from "../components/Counter.svelte";
  import Wall, { type Piece } from "../components/Wall.svelte";
  import ResultList from "../components/ResultList.svelte";
  import ViewingTable from "../components/ViewingTable.svelte";
  import { searchable } from "../lib/selection-state";
  import { resultSummary } from "../lib/search-confidence";
  import BrowsePager from "../components/BrowsePager.svelte";
  import ResultFocus from "../components/ResultFocus.svelte";
  import LikeDrop from "../components/LikeDrop.svelte";
  import PastQueries from "../components/PastQueries.svelte";

  let { active = true }: { active?: boolean } = $props();
  let layout = $state<"wall" | "list">((localStorage.getItem("dowse.layout") as "wall" | "list") ?? "wall");
  let input = $state<HTMLInputElement | null>(null);
  $effect(() => { if (active) input?.focus(); });

  $effect(() => { try { localStorage.setItem("dowse.layout", layout); } catch { /* private storage */ } });

  onMount(() => {
    const focus = () => { if (active) input?.focus(); };
    focus();
    addEventListener("dowse:shown", focus);
    return () => removeEventListener("dowse:shown", focus);
  });

  // Only engine-confirmed strong matches gather in front. Optional weaker candidates stay neutral in the rack.
  const clear = $derived(!search.searching || search.clear);
  const pieces: Piece[] = $derived.by(() => {
    const hits = search.hits;
    const searching = search.searching;
    const found: Piece[] = hits.map((hit) => ({
      key: hit.path, path: hit.path, kind: hit.kind, loc: hit.loc, name: hit.name, line: machineLine(hit),
      strong: searching ? clear && hit.strong : true, closest: searching && (!clear || !hit.strong), visual: hit.visual, excerpt: hit.excerpt,
      score: searching ? hit.score : undefined,
    }));
    if (!searching) return found;
    const seen = new Set(hits.map((h) => h.path));
    const rest: Piece[] = search.recent.filter((h) => !seen.has(h.path) && searchable(h.path, dowse.state?.selection)).map((hit) => ({
      key: hit.path, path: hit.path, kind: hit.kind, loc: null, name: hit.name, line: machineLine(hit),
      strong: false, visual: hit.visual, excerpt: hit.excerpt, back: true,
    }));
    return [...found, ...rest];
  });

  $effect(() => { if (active && search.searching && !search.browseLoaded && !search.browseBusy) untrack(() => search.loadRecent()); });

  const note = $derived(search.searching && search.answer
    ? search.busy ? search.like?.file ? "Reading it…" : "Searching…" : `${resultSummary({strong: search.matchCount}, search.clear ? search.matchCount : search.hits.length)}${search.answer.when ? ` from ${search.answer.when}` : ""} · ${search.answer.ms} ms${search.answer.meaning ? "" : " · words only"}`
    : search.searching ? search.like?.file ? "Reading it…" : "Searching…" : search.browseBusy ? "Loading files…" : search.browseError ? "Couldn’t load files"
    : `${search.browseTotal.toLocaleString()} files${search.filters.length ? " in these filters" : " · all searchable files"}`);

  function open(index: number): void {
    const hit = search.hits[index];
    if (hit) api.open(hit.path, hit.loc, search.query);
  }

  function keys(event: KeyboardEvent): void {
    if (!active) return;
    const target = event.target as HTMLElement;
    if (target !== input && target !== document.body && !target.closest(".results")
        && !target.matches(".wall")) return;
    const hit = search.current;
    if (event.key === "ArrowDown" || (layout === "wall" && event.key === "ArrowRight" && !search.query)) {
      search.move(1); event.preventDefault();
    } else if (event.key === "ArrowUp" || (layout === "wall" && event.key === "ArrowLeft" && !search.query)) {
      search.move(-1); event.preventDefault();
    } else if (event.key === "PageDown") { search.move(8); event.preventDefault(); }
    else if (event.key === "PageUp") { search.move(-8); event.preventDefault(); }
    else if (event.key === "Enter" && hit) {
      event.preventDefault();
      if (event.ctrlKey) api.reveal(hit.path, search.query); else api.open(hit.path, hit.loc, search.query);
    } else if (event.key.toLowerCase() === "c" && event.ctrlKey && hit && !window.getSelection()?.toString()
               && input?.selectionStart === input?.selectionEnd) {
      api.copy(hit.path, event.shiftKey ? "path" : "file", search.query); event.preventDefault();
    } else if (event.key === "Escape") {
      if (search.query) search.query = "";
      else if (search.like) search.dropLike();
      else if (search.within.length) search.within = [];
    }
  }
</script>

<svelte:window onkeydown={keys} />

<section class="search">
  <header>
    <Counter bind:query={search.query} bind:kinds={search.kinds} bind:layout bind:input within={search.within}
             {note} onsetscope={(path) => (search.within = path)}
             filters={search.filters} bind:date={search.date} onremovefilter={(id, path) => search.removeFilter(id, path)} onsetkinds={(kinds) => search.setKinds(kinds)}
             like={search.like} onclearlike={() => search.dropLike()} suggestion={search.suggestion}
             ontakesuggestion={() => search.takeSuggestion()} onchooseexample={() => search.chooseExample()} />
    <PastQueries />
    <ResultFocus />
  </header>

  <div class="floor" class:list={layout === "list"}>
    <div class="finds">
      <div class="collection">
      {#if search.pending && search.searching}
        <!-- Files read since this search came in changed its results: offered, so the gallery holds still meanwhile. -->
        <div class="fresh" role="status">
          <span>Results updated</span>
          <button class="act" onclick={() => search.showPending()}>Show</button>
        </div>
      {/if}
      {#if search.hits.length || (layout === "wall" && pieces.length)}
        {#if layout === "wall"}
          <Wall {pieces} collectionKeys={search.recent.map((hit) => hit.path)} selected={search.hits.length ? Math.min(search.selected, search.hits.length - 1) : -1} terms={search.answer?.terms ?? []}
                searching={search.searching} {clear} {active}
                onselect={(i) => (search.selected = i)} onopen={open} />
        {:else if active}
          <ResultList hits={search.hits} selected={search.selected} terms={search.answer?.terms ?? []}
                      searching={search.searching} {clear} openedCount={search.openedCount} lastOpened={search.lastOpened}
                      onselect={(i) => (search.selected = i)} onopen={open} />
        {/if}
      {:else if search.searching && search.answer && !search.busy}
        <div class="nothing">
          <h2 class="sign">{search.like ? `Nothing like ${search.like.name} here` : `Nothing matches “${search.query}”`}</h2>
          <p>{search.within.length ? "It may be in another folder." : dowse.state?.status.state === "reading"
            ? `Still reading your files (${dowse.state.status.done.toLocaleString()} of ${dowse.state.status.total.toLocaleString()}): more is on its way.`
            : "Try other words, or fewer of them."}</p>
          {#if search.within.length}<button onclick={() => (search.within = [])}>Search all folders</button>{/if}
          {#if dowse.state?.selection.inactive.length}<button onclick={() => dowse.go("/library/inactive")}>Check inactive items</button>{/if}
        </div>
      {:else if !search.searching && !search.recent.length}
        <div class="nothing">
          <h2 class="sign">{search.browseBusy ? "Loading your collection…" : search.browseError ? "Couldn’t load your files" : search.filters.length ? "No files in these filters" : "The racks are empty"}</h2>
          <p>{search.browseError || (search.browseBusy ? "Your files will appear here shortly." : search.filters.length ? "Try removing a folder, date, or file-type filter." : dowse.state?.selection.inactive.length ? "Your files may be inactive. Turn them back on in the Library; originals are still on disk." : dowse.state?.status.state === "reading" || dowse.state?.status.state === "starting"
            ? "Dowse is reading your files. They appear here as they're read." : "Add a folder in the Library to start.")}</p>
          {#if dowse.state?.selection.inactive.length}<button onclick={() => dowse.go("/library/inactive")}>Manage inactive items</button>{/if}
        </div>
      {/if}
      </div>
      {#if !search.searching}
        <BrowsePager total={search.browseTotal} offset={search.browseOffset} shown={search.hits.length}
          more={search.browseMore} busy={search.browseBusy} error={search.browseError}
          onpage={(direction) => search.browsePage(direction)} onretry={() => search.loadBrowse()} />
      {:else if layout === "wall"}
        <div class="collection-note">{pieces.length.toLocaleString()} files on the wall · search highlights results</div>
      {/if}
    </div>
    {#if active}<ViewingTable item={search.current} terms={search.answer?.terms ?? []} query={search.query}
                  confidence={!search.busy && search.searching && search.answer?.query === search.query ? clear && search.current?.strong ? "match" : "closest" : null}
                  onlike={(loc, place) => { if (search.current) search.likeFile(search.current, loc, place); }} />{/if}
  </div>

</section>

{#if active}<LikeDrop ondrop={(file) => search.likeDropped(file)} />{/if}

<style>
  .search { display: grid; grid-template-rows: auto minmax(0, 1fr); height: 100%; }
  header { padding: var(--s-5) var(--s-6) var(--s-3); }
  .floor { display: grid; grid-template-columns: minmax(0, 1fr) 400px; min-height: 0; }
  .finds { position: relative; min-width: 0; min-height: 0; display: grid; grid-template-rows: minmax(0, 1fr) auto; }
  .collection { position: relative; min-width: 0; min-height: 0; }
  .fresh { position: absolute; top: var(--s-3); left: 50%; translate: -50% 0; z-index: 5; display: flex; align-items: center;
    gap: var(--s-3); padding: 6px 6px 6px var(--s-4); background: var(--steel); color: var(--steel-ink);
    border-radius: var(--r-field); box-shadow: var(--lift-3); font-size: var(--text-sm); white-space: nowrap;
    animation: fresh-in var(--t-glide) var(--glide); }
  .fresh .act { padding: 5px 12px; border-radius: 4px; background: var(--found); color: var(--steel); font-weight: 600; }
  @keyframes fresh-in { from { opacity: 0; translate: -50% -8px; } }
  @media (prefers-reduced-motion: reduce) { .fresh { animation: none; } }
  .collection-note { display: flex; align-items: center; min-height: 40px; padding: 5px var(--s-4);
    border-top: 1px solid var(--hairline); background: var(--floor-raised); color: var(--ink-2);
    font-size: var(--text-sm); font-variant-numeric: tabular-nums; }

  /* The storehouse floor under the rack: a band of light where the vitrines stand */
  .floor:not(.list) .finds {
    background:
      radial-gradient(ellipse 70% 55% at 50% 52%, color-mix(in srgb, var(--floor-raised) 90%, transparent), transparent 75%),
      linear-gradient(transparent 82%, color-mix(in srgb, var(--floor-sunk) 70%, transparent));
  }

  .nothing { position: absolute; inset: 0; display: grid; place-content: center; justify-items: center; gap: var(--s-3);
             padding: var(--s-6); text-align: center; }
  .nothing h2 { margin: 0; font-size: var(--text-xl); }
  .nothing p { margin: 0; color: var(--ink-2); max-width: 52ch; }
  .nothing button { margin-top: var(--s-2); padding: 7px 14px; border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); }

  @media (max-width: 1180px) { .floor { grid-template-columns: minmax(0, 1fr) 340px; } }
</style>
