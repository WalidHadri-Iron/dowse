<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api } from "./lib/api";
  import { primary } from "./lib/keys";
  import { dowse } from "./lib/app.svelte";
  import { trayName } from "./lib/system";
  import { search } from "./lib/search.svelte";
  import { windowMode } from "./lib/window.svelte";
  import Icon from "./components/Icon.svelte";
  import Brand from "./components/Brand.svelte";
  import Toast from "./components/Toast.svelte";
  import Rail from "./components/Rail.svelte";
  import Search from "./views/Search.svelte";
  import Explore from "./views/Explore.svelte";
  import Library from "./views/Library.svelte";
  import Aisles from "./views/Aisles.svelte";
  import Settings from "./views/Settings.svelte";
  import Welcome from "./views/Welcome.svelte";
  import Popup from "./views/Popup.svelte";

  let failed = $state<string | null>(null);

  onMount(() => {
    dowse.start().catch((error) => { failed = String(error.message ?? error); });
    const keys = (event: KeyboardEvent) => {
      if (primary(event) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "o"
          && dowse.state && !dowse.state.setupNeeded && dowse.path !== "/welcome") {
        event.preventDefault();
        windowMode.change(dowse.path !== "/popup");
      }
    };
    addEventListener("keydown", keys);
    return () => removeEventListener("keydown", keys);
  });

  $effect(() => {
    const path = dowse.path, params = dowse.params;
    if (path === "/search" || path === "/popup") untrack(() => search.fromRoute(params));
  });

  // The search lives above both views: switching modes does not start another request or lose its selection.
  $effect(() => {
    const ready = !!dowse.state;
    void search.query; void JSON.stringify(search.within); void search.kinds.join(",");
    void search.date?.from; void search.date?.to; void search.ignoredFilters.join(",");
    void search.like;   // a new "more like this" (its object: kept as the answer fills in its place)
    if (ready) untrack(() => {
      search.schedule();
      if (dowse.path === "/search" || dowse.path === "/popup") dowse.replace(search.route(dowse.path === "/popup"));
    });
  });
  $effect(() => { if (dowse.state?.model === "ready") untrack(() => search.modelReady()); });
  $effect(() => {
    if (dowse.indexVersion) untrack(() => search.indexChanged());   // quietly: never a reshuffle under the person's eyes
  });
  const searchView = $derived(dowse.path === "/search" || dowse.path === "/popup");
  $effect(() => {
    // A query changes the route, not the view: warm once on entry, not on every character/backspace.
    if (dowse.state && !dowse.state.setupNeeded && searchView) {
      untrack(() => api.warm().catch(() => {}));
    }
  });

  const view = $derived(dowse.path.split("/")[1] || "search");
  // Keep an already-open gallery across Quick search, with its render loop paused. Rebuilding its
  // WebGL context/shaders on each Ctrl+O (⌘O) made every round trip visibly stall.
  let keepSearch = $state(false);
  $effect(() => {
    if (view !== "popup") keepSearch = view === "search";
  });
</script>

{#if failed}
  <main class="unreachable">
    <Brand height={36} />
    <h1 class="sign">Dowse can't reach its engine</h1>
    <p>{failed}. Quit Dowse from {trayName()} and open it again.</p>
  </main>
{:else if !dowse.state}
  <div class="loading" aria-busy="true" role="status"><Brand height={36} /><span>Opening Dowse…</span></div>
{:else if view === "welcome" || dowse.state.setupNeeded}
  <Welcome />
{:else}
  {#if view === "popup"}<Popup />{/if}
  {#if view !== "popup" || keepSearch}
  <div class="window" hidden={view === "popup"} inert={view === "popup"}>
    <Rail current={view} />
    <main>
      {#if view === "explore"}
        <Explore />
      {:else if view === "library"}
        <Library />
      {:else if view === "aisles"}
        <Aisles />
      {:else if view === "settings"}
        <Settings />
      {:else}
        <Search active={view !== "popup"} />
      {/if}
    </main>
  </div>
  {/if}
{/if}

{#if dowse.error}
  <div class="error" role="alert">
    <Icon name="warning" size={18} /><span>{dowse.error}</span>
    <button onclick={() => (dowse.error = null)} aria-label="Dismiss error"><Icon name="dismiss" size={16} /></button>
  </div>
{/if}
{#if dowse.notice}
  {#key dowse.notice.id}
    <Toast text={dowse.notice.text} action={dowse.notice.undo ? "Undo" : undefined}
           onaction={() => dowse.undo()} onclose={() => (dowse.notice = null)} />
  {/key}
{/if}

<style>
  .window { display: grid; grid-template-columns: 236px 1fr; height: 100%; }
  .window[hidden] { display: none; }
  main { min-width: 0; min-height: 0; position: relative; }
  .loading { display: grid; place-content: center; justify-items: center; gap: var(--s-4);
    height: 100%; background: var(--floor); color: var(--ink-2); font-size: var(--text-sm); }
  .unreachable { display: grid; place-content: center; height: 100%; padding: var(--s-7); gap: var(--s-3); }
  .unreachable h1 { margin: 0; font-size: var(--text-2xl); }
  .unreachable p { margin: 0; color: var(--ink-2); max-width: 60ch; }
  .error { position: fixed; left: var(--s-4); right: var(--s-4); bottom: var(--s-4); z-index: 20;
           display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4);
           border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); box-shadow: var(--lift-2); }
  .error span { flex: 1; }
  .error button { display: grid; place-items: center; padding: var(--s-1); }
</style>
