<script lang="ts">
  import { api } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { inactiveBy, pathKey } from "../lib/selection-state";
  import Toggle from "./Toggle.svelte";
  import Icon from "./Icon.svelte";

  let { path, name, isFolder = false, compact = false }: {
    path: string; name: string; isFolder?: boolean; compact?: boolean;
  } = $props();
  let busy = $state(false), confirm = $state(false);
  const blocked = $derived(inactiveBy(path, dowse.state?.selection.inactive ?? []));
  const inherited = $derived(!!blocked && pathKey(blocked) !== pathKey(path));
  $effect(() => { void path; confirm = false; });

  async function change(op: "activate" | "deactivate" | "remove"): Promise<void> {
    busy = true;
    const targetPath = path, targetName = name;
    try {
      const answer = await api.choose(op, { paths: [targetPath] });
      if (answer.changed) dowse.tell(op === "remove" ? `Removed ${targetName} from Dowse. Originals stay on disk.`
        : `${targetName} is ${op === "activate" ? "active" : "inactive"} for search.`, answer.before);
      await dowse.refresh();
      confirm = false;
    } catch (error) { dowse.error = String(error); }
    finally { busy = false; }
  }
</script>

<details class="search-options" open={!compact}>
  <summary>Search options</summary>
  <div class="membership">
    <div class="switch-row">
      <span><b>{blocked ? "Inactive" : "Active"} for search</b><small>{isFolder ? "Includes this folder's contents." : blocked ? "Turn on to include this file again." : "Turn off to hide this file from search."}</small></span>
      <Toggle on={!blocked} label="Search {name}" disabled={busy || inherited} onchange={(on) => change(on ? "activate" : "deactivate")} />
    </div>
    {#if inherited}
      <p>The containing folder is inactive. <button class="link" onclick={() => dowse.go(`/library/${encodeURIComponent(blocked!)}`)}>Manage that folder</button></p>
    {/if}
    {#if confirm}
      <div class="confirm" role="group" aria-label="Confirm removal">
        <b>Remove {name} from Dowse?</b>
        <p>{isFolder ? "This folder and its files" : "This file"} will leave the search index. Nothing is deleted from disk. You can add it back in the Library.</p>
        <div class="buttons"><button class="yes" disabled={busy} onclick={() => change("remove")}>Remove from Dowse</button><button disabled={busy} onclick={() => (confirm = false)}>Cancel</button></div>
      </div>
    {:else}
      <button class="remove" disabled={busy} onclick={() => (confirm = true)}><Icon name="remove" size={16} />Remove from Dowse…</button>
    {/if}
  </div>
</details>

<style>
  .search-options { border-top: 1px solid var(--hairline); padding-top: var(--s-3); font-size: var(--text-sm); }
  summary { cursor: pointer; color: var(--ink-2); font-weight: 600; }
  .membership { display: grid; gap: var(--s-2); padding-top: var(--s-3); }
  .switch-row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); }
  .switch-row span { display: grid; gap: 3px; }
  small, p { color: var(--ink-2); font-size: var(--text-xs); line-height: 1.5; }
  p { margin: 0; }
  .remove { display: inline-flex; align-items: center; gap: 6px; justify-self: start; padding: 6px 0; color: var(--ink-2); }
  .link { text-decoration: underline; color: var(--found-ink); }
  .confirm { display: grid; gap: var(--s-2); padding: var(--s-3); background: var(--floor-raised); border: 1px solid var(--hairline); border-radius: var(--r-field); }
  .buttons { display: flex; flex-wrap: wrap; gap: var(--s-2); }
  .buttons button { padding: 6px 10px; border-radius: var(--r-field); }
  .yes { background: var(--steel); color: var(--steel-ink); }
</style>
