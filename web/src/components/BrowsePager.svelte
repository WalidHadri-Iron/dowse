<script lang="ts">
  import { browseRange } from "../lib/browse";
  let { total, offset, shown, more, busy, error = "", onpage, onretry }: {
    total: number; offset: number; shown: number; more: boolean; busy: boolean; error?: string;
    onpage: (direction: -1 | 1) => void; onretry: () => void;
  } = $props();
</script>

<nav class="browse-pager" aria-label="Browse all files">
  <span class="summary" role="status">{busy ? "Loading files…" : error ? "Couldn’t load files" : browseRange(offset, shown, total)}</span>
  {#if error}<button onclick={onretry}>Try again</button>
  {:else if total > shown || offset > 0}
    <button disabled={busy || offset === 0} onclick={() => onpage(-1)} aria-label="Previous files">Previous</button>
    <button disabled={busy || !more} onclick={() => onpage(1)} aria-label="Next files">Next</button>
  {/if}
</nav>

<style>
  nav { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); min-height: 40px;
    padding: 5px var(--s-4); border-top: 1px solid var(--hairline); background: var(--floor-raised); font-size: var(--text-sm); }
  .summary { flex: 1; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  button { padding: 4px 9px; border-radius: var(--r-field); border: 1px solid var(--hairline); }
  button:hover:not(:disabled) { background: var(--floor-sunk); }
</style>
