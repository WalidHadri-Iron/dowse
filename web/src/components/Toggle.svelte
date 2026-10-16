<script lang="ts">
  // Windows' toggle switch: the thumb slides on its rail, teal when on.
  let { on = false, label, disabled = false, onchange }: {
    on?: boolean; label: string; disabled?: boolean; onchange: (on: boolean) => void;
  } = $props();
</script>

<button class="toggle" class:on role="switch" aria-checked={on} aria-label={label} {disabled}
        onclick={() => onchange(!on)}>
  <span class="thumb"></span>
</button>

<style>
  .toggle {
    width: 42px; height: 22px; flex: none; border-radius: 11px; position: relative;
    border: 1.5px solid var(--ink-3); background: transparent;
    transition: background var(--t-rail) var(--rail), border-color var(--t-rail) var(--rail);
  }
  .toggle.on { background: var(--found); border-color: var(--found); }
  .thumb {
    position: absolute; top: 3.5px; left: 4px; width: 12px; height: 12px; border-radius: 50%; background: var(--ink-2);
    transition: transform var(--t-glide) var(--glide), width var(--t-rail) var(--rail), background var(--t-rail) var(--rail);
  }
  .on .thumb { transform: translateX(19px); background: #fff; }
  .toggle:hover:not(:disabled) .thumb { width: 14px; }
</style>
