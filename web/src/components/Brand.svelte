<script lang="ts">
  // The same outlined wordmark in every panel. Follow the app's theme, not the OS preference;
  // the navigation rail always has a dark background and explicitly uses the inverse variant.
  import light from "../../../assets/brand/dowse-wordmark.svg?url";
  import dark from "../../../assets/brand/dowse-wordmark-dark.svg?url";

  let { height = 30, inverse = false }: { height?: number; inverse?: boolean } = $props();
</script>

<span class="brand-mark" class:inverse style:--brand-height="{height}px" role="img" aria-label="Dowse">
  <img class="light" src={light} alt="" width="584" height="160" />
  <img class="dark" src={dark} alt="" width="584" height="160" />
</span>

<style>
  .brand-mark { display: inline-flex; flex: none; height: var(--brand-height); line-height: 0; }
  img { display: block; height: 100%; width: auto; object-fit: contain; }
  .dark { display: none; }
  .inverse .light, :global([data-theme="dark"]) .light { display: none; }
  .inverse .dark, :global([data-theme="dark"]) .dark { display: block; }
</style>
