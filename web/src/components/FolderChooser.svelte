<script lang="ts">
  import { onMount, onDestroy, untrack, tick } from "svelte";
  import { api, type SearchFolder } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { pathKey } from "../lib/selection-state";
  import Icon from "./Icon.svelte";

  let { current = [], onchoose, onclose }: {
    current?: string[];
    onchoose: (paths: string[]) => void;
    onclose: () => void;
  } = $props();
  let dialog: HTMLDialogElement;
  let input: HTMLInputElement;
  let folders = $state<SearchFolder[]>([]);
  let selectedPaths = $state<string[]>([]);
  let filter = $state(""), error = $state(""), loading = $state(true);
  let controller: AbortController | null = null;
  const visible = $derived(folders.filter((f) => `${f.name} ${f.path}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase())));

  async function load(): Promise<void> {
    controller?.abort();
    const request = new AbortController(); controller = request;
    loading = true; error = "";
    try {
      const answer = await api.searchFolders(request.signal);
      if (!request.signal.aborted) {
        folders = answer.folders;
        loading = false;
        await tick();
        if (!request.signal.aborted) dialog?.querySelector('.folder[data-selected="true"]')?.scrollIntoView({ block: "nearest" });
      }
    } catch (cause) {
      if (!request.signal.aborted) error = String((cause as Error).message ?? cause);
    } finally { if (!request.signal.aborted) loading = false; }
  }
  $effect(() => { void dowse.indexVersion; untrack(load); });
  onMount(() => { selectedPaths = [...current]; dialog.showModal(); input.focus(); });
  onDestroy(() => { controller?.abort(); dialog?.close(); });
  function toggle(path: string): void {
    selectedPaths = selectedPaths.some((p) => pathKey(p) === pathKey(path))
      ? selectedPaths.filter((p) => pathKey(p) !== pathKey(path)) : [...selectedPaths, path];
  }
  function apply(): void { onchoose([...selectedPaths]); onclose(); }
  function keys(event: KeyboardEvent): void {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onclose(); }
  }
</script>

<dialog class="folder-chooser" bind:this={dialog} aria-labelledby="folder-chooser-title" aria-describedby="folder-chooser-help"
        onkeydown={keys} oncancel={(event) => { event.preventDefault(); onclose(); }}
        onclick={(event) => {
          if (event.target === dialog) {
            const r = dialog.getBoundingClientRect();
            if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) onclose();
          }
        }}>
  <header>
    <h2 class="sign" id="folder-chooser-title">Search in folders</h2>
    <button class="close" onclick={onclose} aria-label="Close folder chooser"><Icon name="dismiss" size={20} /></button>
  </header>
  <p id="folder-chooser-help">Choose one or more folders already in Dowse. Search includes any selected folder and its subfolders.</p>
  <label class="find"><Icon name="search" size={18} />
    <input bind:this={input} bind:value={filter} aria-label="Find a folder" placeholder="Find a folder by name or path" spellcheck="false" />
  </label>
  <button class="all" class:selected={!selectedPaths.length} aria-pressed={!selectedPaths.length} onclick={() => (selectedPaths = [])}>
    <Icon name="folder" size={18} /><span>All folders</span>{#if !selectedPaths.length}<Icon name="check" size={16} />{/if}
  </button>
  <div class="folders" aria-busy={loading}>
    {#if loading}<p class="status" role="status">Loading folders…</p>
    {:else if error}<p class="status" role="alert">Couldn’t load folders: {error}</p><button class="retry" onclick={load}>Try again</button>
    {:else if !folders.length}<p class="status">No folders to search yet. Add files or folders in the Library.</p>
    {:else if !visible.length}<p class="status">No folders match “{filter}”.</p>
    {:else}
      {#each visible as folder (folder.path)}
        {@const selected = selectedPaths.some((p) => pathKey(p) === pathKey(folder.path))}
        <label class="folder" class:selected class:inactive={!folder.active} data-selected={selected}
                data-path={folder.path} title={folder.path}>
          <input type="checkbox" checked={selected} disabled={!folder.active && !selected}
                 aria-label="Search in {folder.name}: {folder.path}" onchange={() => toggle(folder.path)} />
          <Icon name="folder" size={20} />
          <span class="description"><span class="name">{folder.name}</span><span class="path selectable">{folder.path}</span></span>
          <span class="count">{folder.active ? `${folder.count} searchable ${folder.count === 1 ? "file" : "files"}` : "Inactive"}</span>
        </label>
      {/each}
    {/if}
  </div>
  <footer>
    <div class="actions">
      <span class="summary" role="status">{selectedPaths.length ? `${selectedPaths.length} ${selectedPaths.length === 1 ? "folder" : "folders"} selected` : "All folders"}</span>
      <button class="cancel" onclick={onclose}>Cancel</button>
      <button class="apply" onclick={apply}>Apply</button>
    </div>
    <p class="hint">This only filters your search. Manage sources in the Library.</p>
  </footer>
</dialog>

<style>
  dialog { width: min(580px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: var(--s-5);
    border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); color: var(--ink);
    box-shadow: var(--lift-3); overflow: hidden; }
  dialog[open] { display: grid; grid-template-rows: auto auto auto auto minmax(0, 1fr) auto; }
  dialog::backdrop { background: color-mix(in srgb, var(--steel) 55%, transparent); }
  header { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
  h2 { margin: 0; font-size: var(--text-xl); }
  .close { display: grid; place-items: center; width: 30px; height: 30px; border-radius: var(--r-field); flex: none; }
  .close:hover { background: var(--floor-sunk); }
  p { margin: var(--s-2) 0 var(--s-4); font-size: var(--text-sm); color: var(--ink-2); }
  .find { display: flex; align-items: center; gap: var(--s-2); border: 1px solid var(--hairline);
    border-radius: var(--r-field); padding: var(--s-2) var(--s-3); color: var(--ink-3); background: var(--floor); }
  .find:focus-within { border-color: var(--found); }
  .find input { flex: 1; min-width: 0; background: transparent; border: 0; outline: 0; user-select: text; }
  .folder input { flex: none; width: 16px; height: 16px; margin: 0; accent-color: var(--found); }
  .all, .folder { display: flex; align-items: center; gap: var(--s-3); width: 100%; padding: var(--s-3);
    text-align: left; border-radius: var(--r-field); font-size: var(--text-sm); }
  .all { margin: var(--s-2) 0; }
  .all span { flex: 1; }
  .folder { cursor: pointer; }
  .all:hover, .folder:not(.inactive):hover { background: var(--floor-sunk); }
  .selected { background: var(--found-wash); color: var(--found-ink); }
  .folders { min-height: 0; max-height: min(44dvh, 340px); overflow: auto; border-block: 1px solid var(--hairline); }
  .description { display: grid; gap: 2px; flex: 1; min-width: 0; }
  .name { font-weight: 600; }
  .path { font-size: var(--text-xs); color: var(--ink-3); overflow-wrap: anywhere; }
  .count { color: var(--ink-2); font-size: var(--text-xs); flex: none; }
  .folder :global(svg) { flex: none; }
  .folder.inactive { opacity: 0.65; }
  .status { padding: var(--s-2); }
  .retry { margin: 0 var(--s-2) var(--s-3); text-decoration: underline; }
  footer { margin-top: var(--s-3); color: var(--ink-3); font-size: var(--text-xs); }
  .actions { display: flex; gap: var(--s-2); align-items: center; }
  .summary { margin-right: auto; }
  .apply, .cancel { padding: 7px 14px; border-radius: var(--r-field); font-size: var(--text-sm); }
  .apply { background: var(--steel); color: var(--steel-ink); }
  .cancel { color: var(--ink-2); }
  .cancel:hover { background: var(--floor-sunk); }
  .hint { margin: var(--s-2) 0 0; color: var(--ink-3); font-size: var(--text-xs); }
  @media (max-width: 680px) { dialog { padding: var(--s-4); } .folder { flex-wrap: wrap; gap: var(--s-2); } }
</style>
