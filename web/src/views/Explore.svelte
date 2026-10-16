<script lang="ts">
  // Explore: the whole storehouse at once, aisles of files that are alike, named by what they hold. Walk it, step
  // into an aisle, pick a case to see it on the viewing table; or describe something and watch it come forward
  // where it stands.

  import { onMount, onDestroy, untrack } from "svelte";
  import { api, type Answer, type Kind, type SearchFilter, type SearchOptions } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { search } from "../lib/search.svelte";
  import Icon from "../components/Icon.svelte";
  import MapFloor, { type MapAisle, type MapPiece } from "../components/MapFloor.svelte";
  import ViewingTable from "../components/ViewingTable.svelte";
  import { searchable } from "../lib/selection-state";
  import { hasClearMatch } from "../lib/search-confidence";
  import { activeFilters } from "../lib/search-filters";
  import SearchFilters from "../components/SearchFilters.svelte";
  import { withoutScope } from "../lib/search-scope";
  import { focusedHits, focusedSummary } from "../lib/search-presentation";
  import { rankedStep, filterExplore, filterExploreAisles } from "../lib/explore-search";
  import { folderFloor } from "../lib/explore-folders";

  let aisles = $state<MapAisle[]>([]);
  let pieces = $state<MapPiece[]>([]);
  let total = $state(0);
  let loading = $state(true);
  const grouping = $derived(dowse.params.get("group") === "folders" ? "folders" : "aisles");
  function setGrouping(next: "aisles" | "folders"): void {
    const params = new URLSearchParams(dowse.params);
    params.delete("aisle");
    if (next === "folders") params.set("group", next); else params.delete("group");
    dowse.replace(`/explore${params.size ? `?${params}` : ""}`);
  }
  let query = $state("");
  let kinds = $state<Kind[]>([]);
  let within = $state<string[]>([]);
  let date = $state<SearchOptions["date"]>(null);
  let ignoredFilters = $state<SearchOptions["ignoredFilters"]>([]);
  let answer = $state<Answer | null>(null);
  let filterQuery = "";
  const filters = $derived(activeFilters(query, answer, kinds, within, { date, ignoredFilters }));
  function removeFilter(id: SearchFilter["id"], path?: string): void {
    if (id === "folder") within = path ? withoutScope(within, path) : [];
    else {
      if (id === "kind") kinds = [];
      if (id === "date") date = null;
      if (!ignoredFilters.includes(id)) ignoredFilters = [...ignoredFilters, id];
    }
  }
  function setKinds(next: Kind[]): void {
    kinds = next;
    if (!ignoredFilters.includes("kind")) ignoredFilters = [...ignoredFilters, "kind"];
  }
  let found = $state<Map<string, number> | null>(null);
  let scores = $state<Map<string, number> | null>(null);
  let clear = $state(false);
  let showCandidates = $state(false);
  let focusContext = "";
  let searching = $state(false);
  let chosen = $state<MapPiece | null>(null);
  let floor = $state<MapFloor | null>(null);
  let timer = 0;
  let controller: AbortController | null = null;
  const collection = $derived(grouping === "folders"
    ? folderFloor(pieces, dowse.state?.selection.folders ?? [], dowse.state?.selection.files ?? []) : { pieces, aisles });
  const searchablePieces = $derived(collection.pieces.filter(p => searchable(p.path, dowse.state?.selection)));
  // Unchanged restrictions must not rebuild the floor/textures on every query keystroke.
  const floorFilterKey = $derived(JSON.stringify(filters));
  const floorFilters = $derived(JSON.parse(floorFilterKey) as SearchFilter[]);
  const visiblePieces = $derived(filterExplore(searchablePieces, floorFilters));
  const visibleAisles = $derived(!floorFilters.length && visiblePieces.length === pieces.length
    ? collection.aisles : filterExploreAisles(collection.aisles, visiblePieces));
  const visibleTotal = $derived(!floorFilters.length && visiblePieces.length === pieces.length ? total : visiblePieces.length);
  const floorSummary = $derived.by(() => {
    if (loading) return "Laying out your files…";
    const count = `${visibleTotal.toLocaleString()}${filters.length ? ` of ${total.toLocaleString()}` : ""}`;
    const groups = visibleAisles.length;
    const noun = grouping === "folders" ? "folder" : "aisle";
    const detail = grouping === "folders" ? ". Originals stay where they are."
      : `${visibleAisles.some(a => a.custom) ? ", yours first" : ""}, grouped by what's in them.`;
    return `${count} files in ${groups} ${noun}${groups === 1 ? "" : "s"}${detail}`;
  });
  const paths = $derived(new Set(visiblePieces.map(p => p.path)));
  const chosenGroup = $derived(visibleAisles.find(a => a.index === visiblePieces.find(p => p.path === chosen?.path)?.aisle)?.label ?? chosen?.folder ?? "");
  const candidates = $derived((answer?.hits ?? []).filter(h => paths.has(h.path)));
  const matches = $derived(new Set(candidates.filter(h => h.strong).map(h => h.path)));
  const results = $derived(query.trim() ? focusedHits(candidates, clear, showCandidates) : candidates);
  const resultIndex = $derived(results.findIndex(h => h.path === chosen?.path));

  function stepResult(step: -1 | 1): void {
    if (step < 0 && resultIndex <= 0) return;
    const path = rankedStep(results.map(h => h.path), chosen?.path ?? null, step);
    const piece = visiblePieces.find(p => p.path === path);
    if (piece) { chosen = piece; floor?.focusResult(piece.path); }
  }
  function toggleCandidates(): void {
    showCandidates = !showCandidates;
    if (chosen && !results.some(h => h.path === chosen?.path)) chosen = null;
  }
  function navigate(event: KeyboardEvent): void {
    if (event.ctrlKey || event.metaKey || event.shiftKey || searching || !results.length) return;
    const mapFocused = (event.target as HTMLElement).matches?.('.floor');
    const step = event.key === 'ArrowDown' || (mapFocused && event.key === 'ArrowRight') ? 1
      : event.key === 'ArrowUp' || (mapFocused && event.key === 'ArrowLeft') ? -1 : 0;
    if (step && (event.altKey || mapFocused)) { event.preventDefault(); stepResult(step as -1 | 1); }
  }

  async function load(): Promise<void> {
    try {
      const map = await api.explore();
      aisles = map.aisles; pieces = map.pieces; total = map.total;
    } finally { loading = false; }
    const wanted = dowse.params.get("aisle");   // from the rail: straight to that aisle
    if (wanted !== null) setTimeout(() => {
      if (grouping === "aisles" && dowse.params.get("aisle") === wanted) floor?.goTo(Number(wanted));
    }, 300);
  }

  $effect(() => {
    const wanted = dowse.params.get("aisle");
    untrack(() => { if (wanted !== null && grouping === "aisles" && floor) floor.goTo(Number(wanted)); });
  });

  onMount(load);
  $effect(() => { if (chosen && !paths.has(chosen.path)) chosen = null; });
  onDestroy(() => { clearTimeout(timer); controller?.abort(); });
  $effect(() => { void dowse.mapVersion; untrack(() => { if (!loading) load(); }); });   // a new map, not each step

  $effect(() => {
    void query; void kinds.join(","); void JSON.stringify(within); void date?.from; void date?.to; void ignoredFilters.join(",");
    void dowse.state?.model;
    untrack(() => {
      if (filterQuery !== query) { filterQuery = query; ignoredFilters = []; }
      const context = JSON.stringify([query, kinds, within, date, ignoredFilters]);
      if (context !== focusContext) showCandidates = false;
      focusContext = context;
      clearTimeout(timer);
      controller?.abort();
      const text = query.trim();
      // Filter-only browsing shows every eligible case, with no rank/confidence treatment or hit cap.
      if (!text) { answer = null; found = null; scores = null; clear = false; searching = false; return; }
      // Keep the current floor and camera still while typing; fit once the new answer arrives.
      searching = true;
      const request = new AbortController();
      controller = request;
      timer = window.setTimeout(async () => {
        try {
          const result = await api.search(text, kinds, within, request.signal, 40, { date, ignoredFilters });
          if (request.signal.aborted) return;
          answer = result;
          clear = hasClearMatch(result) && result.hits.some(h => paths.has(h.path) && h.strong);
        } catch (error) { if (!request.signal.aborted) { answer = null; found = null; scores = null; clear = false; dowse.error = String(error); } }
        finally { if (!request.signal.aborted) searching = false; }
      }, 180);
    });
  });

  $effect(() => {
    void results; void answer; void searching;
    untrack(() => {
      if (!answer || searching) return;
      found = new Map(results.map((h, i) => [h.path, i]));
      scores = new Map(results.map(h => [h.path, h.score]));
      if (chosen && !found.has(chosen.path)) chosen = null;
    });
  });

  const viewed = $derived(chosen ? answer?.hits.find((h) => h.path === chosen?.path) ?? {
    path: chosen.path, name: chosen.name, folder: chosen.folder, kind: chosen.kind, loc: null, place: null,
    excerpt: null, mtime: null, size: null, info: {}, segment: 0, visual: chosen.visual, moments: [],
  } : null);
</script>

<svelte:window onkeydown={navigate} />

<section class="explore" class:viewing={!!chosen}>
  <div class="stage">
    <header>
      <div class="title">
        <h1 class="sign">Explore</h1>
        <p>{floorSummary}</p>
      </div>
      <label class="find">
        <Icon name="search" size={18} />
        <input bind:value={query} placeholder="Find something on the floor" spellcheck="false" aria-label="Search Explore"
               onkeydown={(e) => e.key === "Escape" && (query = "")} />
        {#if searching}<span class="data" role="status">Searching…</span>{:else if found}<span class="data" class:closest={!clear} role="status">{found.size ? `${found.size} ${!query.trim() ? "files" : clear ? found.size === 1 ? "match" : "matches" : "closest"} here` : "No results here"}</span>{/if}
      </label>
      <button class="overview" onclick={() => floor?.overview()} title="See the whole floor"><Icon name="layers" size={18} />Whole floor</button>
      {#if found?.size}<button class="overview" onclick={() => floor?.fitResults()} title="Fit all displayed results in their groups">Fit results</button>{/if}
      <button class="overview manage-aisles" onclick={() => dowse.go("/aisles")} title="Make your own aisles"><Icon name="aisles" size={18} />Your aisles</button>
      <div class="browse-controls">
        <fieldset class="grouping">
          <legend>Group by</legend>
          <div class="options">
            <label><input type="radio" name="explore-grouping" value="aisles" checked={grouping === "aisles"} onchange={() => setGrouping("aisles")} /><Icon name="aisles" size={16} />Aisles</label>
            <label><input type="radio" name="explore-grouping" value="folders" checked={grouping === "folders"} onchange={() => setGrouping("folders")} /><Icon name="folder" size={16} />Folders</label>
          </div>
        </fieldset>
        <div class="filters">
          <SearchFilters {filters} bind:date onremove={removeFilter} onkinds={setKinds} chooseKinds folderLabel="Filter folders"
            onsetscope={(path) => (within = path)} />
        </div>
      </div>
      {#if answer && !searching}
        <div class="result-navigation" aria-label="Ranked Explore results">
          <span class="result-summary">{query.trim() ? focusedSummary(clear, matches.size, results.length, candidates.length) : `${results.length} filtered files`}</span>
          {#if results.length}
            <button disabled={resultIndex <= 0} onclick={() => stepResult(-1)} aria-label="Previous ranked result" title="Previous result (Alt+↑)">← Previous</button>
            <output aria-live="polite">{resultIndex < 0 ? "Best first" : `Result ${resultIndex + 1} of ${results.length}`}</output>
            <button disabled={resultIndex === results.length - 1} onclick={() => stepResult(1)} aria-label="Next ranked result" title="Next result (Alt+↓)">{resultIndex < 0 ? "First result" : "Next"} →</button>
          {/if}
          {#if showCandidates || candidates.length > results.length}
            <button class="more-candidates" aria-pressed={showCandidates} onclick={toggleCandidates}>{showCandidates ? clear ? "Clear matches only" : "Closest few only" : "Show other candidates"}</button>
          {/if}
          {#if chosen && resultIndex >= 0}<span class="result-place">{chosen.name} · {chosenGroup}</span>{/if}
        </div>
      {/if}
    </header>
    {#if visiblePieces.length}
      <MapFloor bind:this={floor} pieces={visiblePieces} aisles={visibleAisles} {grouping} {found} {scores} {clear} {matches} filtered={!query.trim()}
        selected={chosen?.path ?? null} onselect={(p) => (chosen = p)} />
    {/if}
    {#if !loading && !visiblePieces.length}
      <div class="empty"><h2 class="sign">{filters.length ? "No files in these filters" : "Nothing on the floor yet"}</h2>
        <p>{filters.length ? "Choose different folders, dates or file types, or remove a filter above." : dowse.state?.selection.inactive.length ? "Your files may be inactive. Activate them in the Library to bring them back." : "Files appear here as Dowse reads them."}</p>
        {#if !filters.length && dowse.state?.selection.inactive.length}<button onclick={() => dowse.go("/library/inactive")}>Manage inactive items</button>{/if}
      </div>
    {/if}
  </div>
  {#if chosen}
    <div class="side">
      <button class="close" onclick={() => (chosen = null)} aria-label="Close the viewing table"><Icon name="dismiss" size={18} /></button>
      <ViewingTable item={viewed} {query} terms={answer?.terms ?? []}
                    confidence={query.trim() && !searching && chosen && found?.has(chosen.path) ? clear && matches.has(chosen.path) ? "match" : "closest" : null}
                    onlike={(loc, place) => { if (viewed?.kind) { search.likeFile({ path: viewed.path, name: viewed.name, kind: viewed.kind as Kind }, loc, place); dowse.go(search.route()); } }} />
    </div>
  {/if}
</section>

<style>
  .explore { display: grid; grid-template-columns: minmax(0, 1fr); height: 100%; }
  .explore.viewing { grid-template-columns: minmax(0, 1fr) 400px; }
  .stage { position: relative; min-width: 0; min-height: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); }
  header {
    min-width: 0; padding: var(--s-4); display: flex; flex-wrap: wrap; align-items: flex-start; gap: var(--s-2) var(--s-3);
  }
  header > * { pointer-events: auto; }
  .browse-controls { min-width: 0; flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); }
  .grouping { flex: none; border: 0; margin: 0; padding: 0; color: var(--ink-2); font-size: var(--text-sm); }
  .grouping legend { float: left; padding: 0; margin-right: var(--s-2); line-height: 34px; }
  .options { display: flex; padding: 2px; border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-sunk); }
  .options label { position: relative; display: flex; align-items: center; gap: 6px; padding: 5px 9px; border-radius: var(--r-field); cursor: pointer; }
  .options label:has(input:checked) { color: var(--ink); background: var(--floor-raised); box-shadow: var(--lift-1); }
  .options label:has(input:focus-visible) { outline: 2px solid var(--focus); outline-offset: 1px; }
  .options input { position: absolute; opacity: 0; width: 1px; height: 1px; }
  .filters { min-width: 0; flex: 1; padding: var(--s-2); border-radius: var(--r-field);
    background: color-mix(in srgb, var(--floor) 90%, transparent); width: fit-content; }
  .title { min-width: 0; margin-right: auto; padding: var(--s-2) var(--s-4) var(--s-3); border-radius: var(--r-field);
           background: color-mix(in srgb, var(--floor) 82%, transparent); }
  h1 { margin: 0; font-size: var(--text-2xl); line-height: 1.1; }
  .title p { margin: 2px 0 0; color: var(--ink-2); font-size: var(--text-sm); }
  .find {
    display: flex; align-items: center; gap: var(--s-2); width: 360px; max-width: 100%; min-width: 0; height: 42px; padding: 0 var(--s-3);
    border-radius: var(--r-field); border: 1px solid var(--hairline); background: var(--floor-raised);
    box-shadow: var(--lift-2); color: var(--ink-3);
  }
  .find:focus-within { border-color: var(--found); }
  .find input { flex: 1; min-width: 0; border: 0; background: transparent; outline: none; font-size: var(--text-md); color: var(--ink); user-select: text; }
  .find .data { color: var(--found-ink); }
  .find .data.closest { color: var(--ink-2); }
  .overview { display: inline-flex; align-items: center; gap: 6px; height: 42px; padding: 0 var(--s-3); border-radius: var(--r-field);
              background: var(--floor-raised); border: 1px solid var(--hairline); box-shadow: var(--lift-1); font-size: var(--text-sm); }
  .overview:hover { background: var(--floor-sunk); }
  .result-navigation { min-width: 0; flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2);
    color: var(--ink-2); font-size: var(--text-xs); }
  .result-navigation button { padding: 5px 8px; border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); }
  .result-navigation button:hover:not(:disabled) { background: var(--floor-sunk); }
  .result-navigation output { font-variant-numeric: tabular-nums; }
  .result-summary { margin-right: var(--s-2); }
  .result-place { min-width: 0; max-width: 100%; flex-basis: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { min-height: 0; display: grid; place-content: center; justify-items: center; gap: var(--s-2); padding: var(--s-4); text-align: center; }
  .empty h2 { margin: 0; font-size: var(--text-xl); }
  .empty p { margin: 0; color: var(--ink-2); }
  .empty button { padding: 7px 12px; border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); margin-top: var(--s-3); }
  .side { position: relative; min-height: 0; }
  .close { position: absolute; right: var(--s-3); top: var(--s-3); z-index: 2; display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; color: var(--ink-2); }
  .close:hover { background: var(--floor-sunk); color: var(--ink); }
  @media (max-width: 1180px) {
    .explore.viewing { grid-template-columns: minmax(0, 1fr) 340px; }
    .explore.viewing header { gap: var(--s-2); padding: var(--s-3); }
    .explore.viewing .title { padding: 0; }
    .explore.viewing h1 { font-size: var(--text-xl); }
    .explore.viewing .title p { display: none; }
    .explore.viewing .title { order: 0; }
    .explore.viewing .overview { order: 1; height: 34px; padding: 0 var(--s-2); }
    .explore.viewing .manage-aisles { display: none; }
    .explore.viewing .find { order: 2; flex-basis: 100%; width: 100%; }
    .explore.viewing .browse-controls { order: 3; gap: var(--s-2); }
    .explore.viewing .filters { flex-basis: 100%; padding: 0; }
    .explore.viewing .result-navigation { order: 4; }
    .explore.viewing .result-summary { display: none; }
  }
</style>
