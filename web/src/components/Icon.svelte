<script lang="ts">
  import { ICONS, type IconName } from "../lib/icons";

  let { name, size = 20, label = "" }: { name: IconName; size?: number; label?: string } = $props();
</script>

<span class="icon" style:width="{size}px" style:height="{size}px" role={label ? "img" : undefined}
      aria-label={label || undefined} aria-hidden={label ? undefined : "true"}>
  {@html ICONS[name]}
</span>

<style>
  .icon { display: inline-grid; place-items: center; flex: none; line-height: 0; }
  .icon :global(svg) { width: 100%; height: 100%; fill: currentColor; }
</style>
