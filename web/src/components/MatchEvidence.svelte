<script lang="ts">
  import type { Evidence } from "../lib/api";
  import { evidenceLines } from "../lib/search-filters";
  let { evidence = [], confidence }: { evidence?: Evidence[]; confidence: "match" | "closest" | null } = $props();
  const lines = $derived(evidenceLines(evidence));
</script>

{#if confidence && evidence.length}
  <section class="match-evidence" aria-label="Search evidence">
    <h3>{confidence === "closest" ? "Why this candidate?" : "Why this result?"}</h3>
    {#if confidence === "closest"}<p class="caution">Closest candidate, not a clear match.</p>{/if}
    <ul>{#each lines as line}<li>{line}</li>{/each}</ul>
  </section>
{/if}

<style>
  .match-evidence { margin: var(--s-3) 0 0; font-size: var(--text-sm); color: var(--ink-2); }
  h3 { margin: 0 0 5px; color: var(--ink); font-size: inherit; font-weight: 600; }
  ul { padding-left: 18px; margin: 0; display: grid; gap: 4px; }
  .caution { margin: 0 0 6px; color: var(--ink-3); }
</style>
