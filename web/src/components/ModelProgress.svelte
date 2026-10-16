<script lang="ts">
  import type { ModelJob } from "../lib/api";
  import { duration } from "../lib/app.svelte";
  import { preparationLabel } from "../lib/model-preparation";
  let { job, oncancel }: { job: ModelJob; oncancel?: () => void } = $props();
</script>

<div class="model-progress" role="status" aria-live="polite">
  <b>{preparationLabel(job)}</b>
  {#if job.error}<p class="error">{job.error}</p>{/if}
  {#if job.state === "running"}
    {#if job.stage === "model" || job.stage === "runtime"}
      <span class="data">{(job.done / 1e6).toFixed(0)} of {(job.total / 1e6).toFixed(0)} MB{job.rate ? ` · ${(job.rate / 1e6).toFixed(1)} MB/s` : ""}{job.left ? ` · ${duration(job.left)} left` : ""}</span>
      <progress max={Math.max(job.total, 1)} value={job.done} aria-label="Model preparation progress"></progress>
    {:else}<p>Your original files are untouched.</p>{/if}
    {#if job.canCancel && oncancel}<button class="cancel" onclick={oncancel}>Cancel preparation</button>{/if}
  {/if}
</div>

<style>
  .model-progress { display: grid; gap: var(--s-2); padding: var(--s-3) var(--s-4); border: 1px solid var(--hairline);
                    border-radius: var(--r-field); background: var(--floor-raised); }
  b { font-size: var(--text-sm); }
  p { margin: 0; font-size: var(--text-sm); color: var(--ink-2); overflow-wrap: anywhere; }
  p.error { color: var(--warn); }
  .data { color: var(--ink-2); font-size: var(--text-xs); }
  progress { width: 100%; height: 6px; accent-color: var(--found); }
  .cancel { justify-self: start; padding: var(--s-1) 0; font-size: var(--text-sm); color: var(--ink-2); }
</style>
