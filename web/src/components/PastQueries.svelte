<script lang="ts">
  // Earlier searches, when nothing is typed: the words that found something you opened, newest first. A click
  // searches them again.

  import { search } from "../lib/search.svelte";
  import Icon from "./Icon.svelte";

  let { compact = false, most = 5 }: { compact?: boolean; most?: number } = $props();
  const shown = $derived(search.searching ? [] : search.pastQueries.slice(0, most));
</script>

{#if shown.length}
  <div class="past" class:compact role="group" aria-label="Earlier searches">
    <span class="label"><Icon name="history" size={15} />Earlier</span>
    {#each shown as past (past.query)}
      <button onclick={() => (search.query = past.query)}
              title={past.file ? `Last time, you opened ${past.file.name}` : `Search “${past.query}” again`}>{past.query}</button>
    {/each}
  </div>
{/if}

<style>
  .past { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: var(--s-2); min-width: 0; }
  .label { display: inline-flex; align-items: center; gap: 5px; margin-right: 2px; color: var(--ink-3);
           font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; }
  button {
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 3px 10px;
    border-radius: 999px;
    border: 1px solid var(--hairline);
    background: var(--floor-raised);
    color: var(--ink-2);
    font-size: var(--text-sm);
  }
  button:hover { background: var(--floor-sunk); color: var(--ink); }
  .compact button { font-size: var(--text-xs); padding: 2px 9px; }
</style>
