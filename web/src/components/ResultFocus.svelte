<script lang="ts">
  import { search } from "../lib/search.svelte";
  import { focusedSummary } from "../lib/search-presentation";
</script>

{#if search.searching && search.answer && !search.busy}
  <div class="result-focus">
    <span class="summary">{focusedSummary(search.clear, search.matchCount, search.hits.length, search.candidates.length)}</span>
    {#if search.showCandidates || search.candidates.length > search.hits.length}
      <button class="more" aria-pressed={search.showCandidates} onclick={() => search.toggleCandidates()}>
        {search.showCandidates ? search.clear ? "Clear matches only" : "Closest few only"
          : `Show ${search.candidates.length - search.hits.length} other ${search.candidates.length - search.hits.length === 1 ? "candidate" : "candidates"}`}
      </button>
    {/if}
    <details>
      <summary>How results are chosen</summary>
      <p>Search retrieves files that stand out by meaning or share query words. Clear matches also pass the confidence check and stay close enough to the best match. We show those first; weaker candidates are available separately. If nothing clearly matches, only the closest few are shown. Scores are relative ranking values, not confidence percentages.</p>
    </details>
  </div>
{/if}

<style>
  .result-focus { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2) var(--s-3);
    margin-top: var(--s-2); color: var(--ink-2); font-size: var(--text-xs); }
  .summary { font-variant-numeric: tabular-nums; }
  button { padding: 4px 8px; border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); }
  button:hover { background: var(--floor-sunk); }
  details { flex: 1; min-width: 160px; }
  summary { cursor: pointer; width: fit-content; }
  p { margin: var(--s-2) 0 0; max-width: 76ch; line-height: 1.5; color: var(--ink-2); }
  details[open] { flex-basis: 100%; }
</style>
