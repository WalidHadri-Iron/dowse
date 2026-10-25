<script lang="ts">
  // A file dragged over the window: let go of it to find the files most like it (a photo a friend sent, a scanned
  // letter, a recording). It's read for this only, never added. Only files: text dragged within the page is left alone.

  import { dowse } from "../lib/app.svelte";
  import { fileManager, hasFileMenu } from "../lib/system";
  import Icon from "./Icon.svelte";

  let { ondrop, compact = false }: { ondrop: (file: File) => void; compact?: boolean } = $props();
  let over = $state(false);
  let depth = 0;   // dragenter/dragleave fire for every element crossed: over the window while above zero

  function carriesFiles(event: DragEvent): boolean {
    return !!event.dataTransfer && [...event.dataTransfer.types].includes("Files");
  }
  function enter(event: DragEvent): void {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    depth += 1;
    over = true;
  }
  function hover(event: DragEvent): void {
    if (!carriesFiles(event)) return;
    event.preventDefault();   // else the window navigates to the file
    event.dataTransfer!.dropEffect = "copy";
  }
  function leave(event: DragEvent): void {
    if (!carriesFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) over = false;
  }
  function drop(event: DragEvent): void {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    depth = 0;
    over = false;
    const entry = event.dataTransfer?.items[0]?.webkitGetAsEntry?.();
    const file = event.dataTransfer?.files[0];
    if (entry?.isDirectory || !file) {
      dowse.error = "Drop a file to find what's like it." +
        (hasFileMenu() ? ` To search in a folder, right-click it in ${fileManager()}, then Dowse.` : "");
      return;
    }
    ondrop(file);
  }
</script>

<svelte:window ondragenter={enter} ondragover={hover} ondragleave={leave} ondrop={drop} />

{#if over}
  <div class="drop" class:compact role="presentation">
    <div class="sign-board">
      <Icon name="similar" size={compact ? 28 : 36} />
      <h2 class="sign">Drop to find files like it</h2>
      <p>A photo, a document, a PDF, a recording or a video. Dowse reads it to compare, and doesn't add it.</p>
    </div>
  </div>
{/if}

<style>
  .drop {
    position: fixed;
    inset: 0;
    z-index: 70;
    display: grid;
    place-items: center;
    padding: var(--s-6);
    background: color-mix(in srgb, var(--floor) 82%, transparent);
    backdrop-filter: blur(4px);
    pointer-events: none;   /* the drop lands on the window below */
  }
  .sign-board {
    display: grid;
    justify-items: center;
    gap: var(--s-2);
    max-width: 440px;
    padding: var(--s-6) var(--s-6) var(--s-5);
    border: 2px dashed color-mix(in srgb, var(--found) 70%, transparent);
    border-radius: var(--r-field);
    background: var(--floor-raised);
    box-shadow: var(--lift-3);
    color: var(--found-ink);
    text-align: center;
  }
  h2 { margin: 0; font-size: var(--text-xl); color: var(--ink); }
  p { margin: 0; color: var(--ink-2); font-size: var(--text-sm); line-height: 1.5; }
  .compact .sign-board { padding: var(--s-5); }
  .compact h2 { font-size: var(--text-lg); }
</style>
