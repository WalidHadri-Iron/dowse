<script lang="ts">
  // A few exclusive choices, side by side (Windows' segmented control).
  let { options, value, label, disabled = false, onchange }: {
    options: { value: string; label: string }[]; value: string; label: string; disabled?: boolean;
    onchange: (value: string) => void;
  } = $props();
</script>

<div class="segmented" role="radiogroup" aria-label={label}>
  {#each options as option (option.value)}
    <button role="radio" aria-checked={value === option.value} class:on={value === option.value} {disabled}
            onclick={() => onchange(option.value)}>{option.label}</button>
  {/each}
</div>

<style>
  .segmented { display: inline-flex; padding: 2px; border-radius: var(--r-field); background: var(--floor-sunk); flex: none; }
  button { padding: 6px 14px; border-radius: 4px; font-size: var(--text-sm); color: var(--ink-2); }
  button.on { background: var(--floor-raised); color: var(--ink); box-shadow: var(--lift-1); font-weight: 600; }
</style>
