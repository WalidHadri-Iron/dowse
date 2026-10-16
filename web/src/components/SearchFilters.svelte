<script lang="ts">
  import type { Kind, SearchFilter, SearchOptions } from "../lib/api";
  import { KIND_PLURAL } from "../lib/app.svelte";
  import Icon from "./Icon.svelte";
  import FolderChooser from "./FolderChooser.svelte";

  let { filters = [], date = $bindable<SearchOptions["date"]>(null), onremove, onsetscope,
        onkinds, chooseKinds = false, compact = false, folderLabel = "Folders" }: {
    filters?: SearchFilter[];
    date?: SearchOptions["date"];
    onremove: (id: SearchFilter["id"], path?: string) => void;
    onsetscope?: (paths: string[]) => void;
    onkinds?: (kinds: Kind[]) => void;
    chooseKinds?: boolean;
    compact?: boolean;
    folderLabel?: string;
  } = $props();
  let editing = $state(false), from = $state(""), to = $state(""), error = $state("");
  let choosingFolder = $state(false);
  const within = $derived(filters.filter((f) => f.id === "folder").map((f) => f.label!).filter(Boolean));
  const kinds: Kind[] = ["image", "screenshot", "pdf", "doc", "audio", "video"];
  function label(filter: SearchFilter): string {
    if (filter.id === "kind") return (filter.kinds ?? []).map((k) => KIND_PLURAL[k]).join(" + ");
    if (filter.id === "folder") return `In ${filter.label?.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "folder"}`;
    return filter.label ?? "Date";
  }
  function editDate(): void {
    const current = date ?? filters.find((f) => f.id === "date");
    from = current?.from ?? ""; to = current?.to ?? ""; error = ""; editing = !editing;
  }
  function apply(event: SubmitEvent): void {
    event.preventDefault();
    if ((!from && !to) || (from && to && from > to)) {
      error = "Choose a date, with From no later than To.";
      return;
    }
    date = { from, to }; editing = false; error = "";
  }
  function closeOnEscape(event: KeyboardEvent): void {
    if (editing && event.key === "Escape") { editing = false; event.stopPropagation(); event.preventDefault(); }
  }
</script>

<div class="search-filters" class:compact aria-label="Search filters">
  <div class="chips">
    {#each filters as filter (filter.id === "folder" ? `folder:${filter.label}` : filter.id)}
      <button class="chip" data-filter={filter.id} title={filter.id === "folder" ? filter.label : `Remove ${label(filter)} filter`}
              aria-label="Remove {label(filter)} filter" onclick={() => onremove(filter.id, filter.id === "folder" ? filter.label : undefined)}>
        <span class="label">{label(filter)}</span>
        {#if filter.source === "query"}<span class="inferred">from query</span>{/if}
        <Icon name="dismiss" size={14} />
      </button>
    {/each}
    {#if onsetscope}
      <button class="add" onclick={() => { editing = false; choosingFolder = true; }} aria-haspopup="dialog">
        <Icon name="folder" size={15} />{within.length ? "Change folders" : folderLabel}
      </button>
    {/if}
    <button class="add" onclick={editDate} onkeydown={closeOnEscape} aria-expanded={editing}>
      <Icon name="clock" size={15} />Date{#if date}&nbsp;· edit{/if}
    </button>
    {#if chooseKinds && onkinds}
      <label class="type">Type
        <select aria-label="File type" value={filters.find((f) => f.id === "kind")?.kinds?.[0] ?? ""}
                onchange={(e) => onkinds?.(e.currentTarget.value ? [e.currentTarget.value as Kind] : [])}>
          <option value="">All files</option>
          {#each kinds as kind}<option value={kind}>{KIND_PLURAL[kind]}</option>{/each}
        </select>
      </label>
    {/if}
  </div>
  {#if editing}
    <form class="date-editor" onsubmit={apply} aria-label="Date filter">
      <label>From <input type="date" bind:value={from} aria-label="From date" max={to || undefined} onkeydown={closeOnEscape} /></label>
      <label>To <input type="date" bind:value={to} aria-label="To date" min={from || undefined} onkeydown={closeOnEscape} /></label>
      <button class="apply" type="submit" onkeydown={closeOnEscape}>Apply</button>
      <button class="add" type="button" onkeydown={closeOnEscape} onclick={() => (editing = false)}>Cancel</button>
      <p>Photo taken date, otherwise last modified. Either date can be left empty.</p>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
    </form>
  {/if}
</div>
{#if choosingFolder && onsetscope}
  <FolderChooser current={within} onchoose={onsetscope} onclose={() => (choosingFolder = false)} />
{/if}

<style>
  .search-filters { position: relative; display: grid; gap: var(--s-2); min-width: 0; font-size: var(--text-sm); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; }
  .chip, .add { display: inline-flex; align-items: center; gap: 6px; min-height: 30px; padding: 4px 10px;
    border-radius: var(--r-field); border: 1px solid var(--hairline); }
  .chip { max-width: 100%; background: var(--found-wash); color: var(--found-ink); }
  .chip .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip :global(svg) { flex: none; }
  .chip:hover { border-color: var(--found); }
  .inferred { font-size: var(--text-xs); color: var(--ink-2); white-space: nowrap; }
  .add { color: var(--ink-2); background: var(--floor-raised); }
  .add:hover { background: var(--floor-sunk); color: var(--ink); }
  .date-editor { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); padding: var(--s-3);
    border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); }
  .compact .date-editor { position: absolute; z-index: 20; top: calc(100% + var(--s-2)); left: 0; width: 100%; box-shadow: var(--lift-3); }
  .date-editor label, .type { display: inline-flex; gap: 6px; align-items: center; color: var(--ink-2); }
  input, select { min-width: 0; border: 1px solid var(--hairline); border-radius: var(--r-field); padding: 5px 7px;
    color: var(--ink); background: var(--floor); font: inherit; color-scheme: inherit; }
  .apply { padding: 6px 12px; border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); }
  p { flex-basis: 100%; margin: 0; font-size: var(--text-xs); color: var(--ink-3); }
  p.error { color: var(--other-ink, var(--ink)); }
</style>
