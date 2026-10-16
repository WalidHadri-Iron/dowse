<script lang="ts">
  import type { SearchModel } from "../lib/api";
  import { size } from "../lib/app.svelte";

  let { models, value = $bindable(""), disabled = false, label = "Search model" }: {
    models: SearchModel[]; value?: string; disabled?: boolean; label?: string;
  } = $props();
</script>

<fieldset {disabled} class="model-picker">
  <legend>{label}</legend>
  {#each models as model (model.id)}
    <label class="model-choice" class:selected={value === model.id}>
      <input type="radio" name={label} value={model.id} bind:group={value} />
      <span class="details">
        <span class="name"><b>{model.name}</b>{#if model.recommended}<span class="recommended">Recommended</span>{/if}</span>
        <span class="description">{model.description}</span>
        <span class="data">{size(model.bytes)} · {model.multimodal ? "Text, image and audio embeddings" : "Text embeddings only"} · {model.installed ? "Downloaded" : "Download required"}</span>
      </span>
    </label>
  {/each}
</fieldset>

<style>
  .model-picker { padding: 0; margin: 0; border: 0; min-width: 0; display: grid; gap: var(--s-2); }
  legend { font-weight: 600; margin-bottom: var(--s-2); }
  .model-choice { display: flex; align-items: flex-start; gap: var(--s-3); padding: var(--s-3) var(--s-4);
                  border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); cursor: pointer; }
  .model-choice.selected { border-color: var(--found); background: color-mix(in srgb, var(--found-wash), var(--floor-raised) 80%); }
  input { flex-shrink: 0; margin-top: 3px; accent-color: var(--found); }
  .details { display: grid; gap: var(--s-1); min-width: 0; }
  .name { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); }
  b { font-size: var(--text-md); }
  .recommended { color: var(--found-ink); font-size: var(--text-xs); }
  .description { font-size: var(--text-sm); color: var(--ink-2); line-height: 1.45; }
  .data { color: var(--ink-3); font-size: 10.5px; line-height: 1.5; }
  fieldset:disabled .model-choice { cursor: default; }
</style>
