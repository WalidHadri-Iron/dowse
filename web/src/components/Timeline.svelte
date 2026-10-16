<script lang="ts">
  // A recording's length with the moments that matched marked on it, where it's playing, and the moments to jump
  // between. Click anywhere on it to go there.

  import { clock } from "../lib/app.svelte";

  let { duration, moments, now, onseek, compact = false }: {
    duration: number;                                   // seconds (0: not known yet)
    moments: { loc: number; locEnd: number | null }[];
    now: number;
    onseek: (seconds: number) => void;
    compact?: boolean;
  } = $props();

  let track = $state<HTMLDivElement | null>(null);
  const share = (t: number) => (duration > 0 ? Math.min(Math.max(t / duration, 0), 1) * 100 : 0);
  const current = $derived(moments.findIndex((m) => now >= m.loc - 0.5 && now <= (m.locEnd ?? m.loc + 5)));

  function at(event: MouseEvent): void {
    if (!track || duration <= 0) return;
    const box = track.getBoundingClientRect();
    onseek(((event.clientX - box.left) / box.width) * duration);
  }

  export function step(by: number): void {
    if (!moments.length) return;
    const ahead = by > 0 ? moments.find((m) => m.loc > now + 0.5) : [...moments].reverse().find((m) => m.loc < now - 1.5);
    onseek((ahead ?? (by > 0 ? moments[0] : moments[moments.length - 1])).loc);
  }
</script>

<div class="timeline" class:compact>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="track" bind:this={track} onclick={at} role="slider" tabindex="-1" aria-label="Where in the recording"
       aria-valuemin="0" aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(now)}>
    {#each moments as moment, index}
      <span class="match" class:on={index === current} style:left="{share(moment.loc)}%"
            style:width="{Math.max(share(moment.locEnd ?? moment.loc + 5) - share(moment.loc), 0.8)}%"></span>
    {/each}
    <span class="head" style:left="{share(now)}%"></span>
  </div>
  {#if moments.length}
    <div class="moments" aria-label="Moments that match">
      <button class="step" onclick={() => step(-1)} title="Previous moment ([)" aria-label="Previous moment">‹</button>
      {#each moments as moment, index}
        <button class="data" class:on={index === current} onclick={() => onseek(moment.loc)}>
          {clock(moment.loc)}{#if moment.locEnd && !compact}–{clock(moment.locEnd)}{/if}
        </button>
      {/each}
      <button class="step" onclick={() => step(1)} title="Next moment (])" aria-label="Next moment">›</button>
    </div>
  {/if}
</div>

<style>
  .timeline { display: grid; gap: var(--s-2); width: 100%; }
  .track {
    position: relative; height: 10px; border-radius: 5px; background: var(--floor-sunk); cursor: pointer;
    box-shadow: inset 0 0 0 1px var(--hairline);
  }
  .match { position: absolute; top: 0; bottom: 0; border-radius: 5px; background: var(--found); opacity: 0.55; }
  .match.on { opacity: 1; }
  .head { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--ink); border-radius: 1px; }
  .moments { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .moments button { padding: 4px 9px; border-radius: var(--r-steel); background: var(--tag); color: var(--tag-ink); font-size: var(--text-xs); }
  .moments button:hover { background: var(--found-wash); }
  .moments button.on { box-shadow: inset 0 -2px 0 var(--found); }
  .moments .step { padding: 2px 8px; font-size: var(--text-md, 15px); background: transparent; color: var(--ink-2); }
  .compact .track { height: 8px; }
</style>
