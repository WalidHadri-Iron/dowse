<script lang="ts">
  // A brief word about what just happened, with the one thing to do about it (Undo). Goes away on its own.
  import { onMount } from "svelte";
  import Icon from "./Icon.svelte";

  let { text, action, onaction, onclose, seconds = 8 }: {
    text: string; action?: string; onaction?: () => void; onclose: () => void; seconds?: number;
  } = $props();

  onMount(() => {
    const timer = setTimeout(onclose, seconds * 1000);
    return () => clearTimeout(timer);
  });
</script>

<div class="toast" role="status">
  <span>{text}</span>
  {#if action}<button class="act" onclick={onaction}>{action}</button>{/if}
  <button class="close" onclick={onclose} aria-label="Close"><Icon name="dismiss" size={16} /></button>
</div>

<style>
  .toast {
    position: fixed;
    width: max-content;
    max-width: calc(100% - 2 * var(--s-4));
    overflow-wrap: anywhere;
    left: 50%;
    bottom: var(--s-5);
    translate: -50% 0;
    display: flex;
    align-items: center;
    gap: var(--s-3);
    padding: 8px 8px 8px var(--s-4);
    background: var(--steel);
    color: var(--steel-ink);
    border-radius: var(--r-field);
    box-shadow: var(--lift-3);
    font-size: var(--text-sm);
    animation: rise var(--t-glide) var(--glide);
    z-index: 10;
  }
  .act { padding: 5px 12px; border-radius: 4px; background: var(--found); color: #072b27; font-weight: 600; }
  .close { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 4px; color: var(--steel-ink-2); }
  .close:hover { background: var(--steel-raised); color: var(--steel-ink); }
  @keyframes rise { from { opacity: 0; translate: -50% 12px; } }
</style>
