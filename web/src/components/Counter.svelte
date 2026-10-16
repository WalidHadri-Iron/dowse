<script lang="ts">
  // The search counter: where you describe what you remember. The query in the storehouse's own voice, the kinds as
  // drawer labels, the folder searched in, and how the finds are laid out (the wall, or a list). Or, instead of
  // words, the file whose likes are searched for; and as words are typed, an earlier search they start, to take with
  // Tab.

  import { pictureUrl, type Kind, type SearchFilter, type SearchOptions } from "../lib/api";
  import type { Like } from "../lib/search.svelte";
  import { KIND_PLURAL } from "../lib/app.svelte";
  import Icon from "./Icon.svelte";
  import SearchFilters from "./SearchFilters.svelte";

  let {
    query = $bindable(""), kinds = $bindable<Kind[]>([]), within, layout = $bindable<"wall" | "list">("wall"),
    note = "", compact = false, onsetscope, input = $bindable<HTMLInputElement | null>(null),
    onkeydown,
    filters = [], date = $bindable<SearchOptions["date"]>(null), onremovefilter, onsetkinds,
    like = null, onclearlike, suggestion = "", ontakesuggestion, onchooseexample,
  }: {
    query?: string;
    kinds?: Kind[];
    within: string[];
    layout?: "wall" | "list";
    note?: string;
    compact?: boolean;
    onsetscope?: (paths: string[]) => void;
    input?: HTMLInputElement | null;
    onkeydown?: (event: KeyboardEvent) => void;
    filters?: SearchFilter[];
    date?: SearchOptions["date"];
    onremovefilter: (id: SearchFilter["id"], path?: string) => void;
    onsetkinds: (kinds: Kind[]) => void;
    like?: Like | null;
    onclearlike?: () => void;
    suggestion?: string;             // what an earlier search adds to the typed words
    ontakesuggestion?: () => void;
    onchooseexample?: () => void;    // pick a file to find what's like it
  } = $props();

  const likeThumb = $derived(like?.preview ?? (like?.path && like.indexed && like.kind && ["image", "screenshot", "pdf", "video"].includes(like.kind)
    ? pictureUrl(like.path, like.loc, 96) : null));

  function keydown(event: KeyboardEvent): void {
    const atEnd = input?.selectionStart === query.length && input?.selectionEnd === query.length;
    if (suggestion && atEnd && (event.key === "Tab" && !event.shiftKey || event.key === "ArrowRight") && ontakesuggestion) {
      event.preventDefault();
      ontakesuggestion();
    } else if (like && event.key === "Backspace" && !query && onclearlike) {
      event.preventDefault();
      onclearlike();
    }
    onkeydown?.(event);
  }

  const KINDS: Kind[] = ["image", "screenshot", "pdf", "doc", "audio", "video"];
  const scopeName = $derived(within.length > 1 ? `${within.length} folders`
    : within[0]?.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? null);
  const effectiveKinds = $derived(filters.find((f) => f.id === "kind")?.kinds ?? kinds);

  function toggle(kind: Kind): void {
    onsetkinds(effectiveKinds.includes(kind) ? effectiveKinds.filter((k) => k !== kind) : [kind]);
  }
</script>

<div class="counter" class:compact>
  <label class="field">
    <Icon name={like ? "similar" : "search"} size={compact ? 20 : 24} />
    {#if like}
      <span class="like" title={like.path ?? like.name}>
        {#if likeThumb}<img src={likeThumb} alt="" />{/if}
        <span class="like-words"><small>Like</small><b>{like.name}</b>{#if like.place}<small class="data">{like.place}</small>{/if}</span>
        <button class="like-off" onclick={() => { onclearlike?.(); input?.focus(); }} aria-label="Stop searching like {like.name}" title="Back to words (Backspace)">
          <Icon name="dismiss" size={14} />
        </button>
      </span>
    {/if}
    <span class="typing">
      {#if suggestion}<span class="ghost" aria-hidden="true"><span class="typed">{query}</span>{suggestion}<kbd>Tab</kbd></span>{/if}
      <input bind:this={input} bind:value={query} onkeydown={keydown} spellcheck="false" autocomplete="off"
             placeholder={like ? "Or describe something else" : scopeName ? `Search in ${scopeName}` : "Describe what you remember"}
             aria-label="Search your files" aria-autocomplete="inline" />
    </span>
    {#if query}
      <button class="clear" onclick={() => { query = ""; input?.focus(); }} aria-label="Clear">
        <Icon name="dismiss" size={16} />
      </button>
    {/if}
    {#if onchooseexample}
      <button class="example" onclick={onchooseexample} title="Find files like a file you choose (or drop one on the window)">
        <Icon name="add" size={16} /><span>Like a file…</span>
      </button>
    {/if}
  </label>

  <div class="row">
    <div class="drawers" role="group" aria-label="Kinds of files">
      <button class:on={effectiveKinds.length === 0} onclick={() => onsetkinds([])} aria-pressed={effectiveKinds.length === 0}>All</button>
      {#each KINDS as kind (kind)}
        <button class:on={effectiveKinds.includes(kind)} onclick={() => toggle(kind)} aria-pressed={effectiveKinds.includes(kind)}>
          {KIND_PLURAL[kind]}
        </button>
      {/each}
    </div>

    <div class="right">
      {#if note}<span class="note">{note}</span>{/if}
      {#if !compact}
        <div class="layout" role="radiogroup" aria-label="Layout">
          <button role="radio" aria-checked={layout === "wall"} class:on={layout === "wall"}
                  onclick={() => (layout = "wall")} title="Glass gallery" aria-label="Glass gallery"><Icon name="wall" size={18} /></button>
          <button role="radio" aria-checked={layout === "list"} class:on={layout === "list"}
                  onclick={() => (layout = "list")} title="List view" aria-label="List view"><Icon name="list" size={18} /></button>
        </div>
      {/if}
    </div>
  </div>
  <SearchFilters {filters} bind:date onremove={onremovefilter} {onsetscope} {compact} />
</div>

<style>
  .counter { display: grid; gap: var(--s-3); }

  .field {
    display: flex;
    align-items: center;
    gap: var(--s-3);
    height: 64px;
    padding: 0 var(--s-4) 0 var(--s-5);
    background: var(--floor-raised);
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    box-shadow: var(--lift-1), inset 0 1px 0 rgba(255, 255, 255, 0.5);
    color: var(--ink-3);
    transition: border-color var(--t-rail) var(--rail), box-shadow var(--t-rail) var(--rail);
  }
  .field:focus-within { border-color: var(--found); box-shadow: var(--lift-2), 0 0 0 3px var(--found-wash); }
  .compact .field { height: 52px; padding-left: var(--s-4); }

  .typing { position: relative; flex: 1; min-width: 0; display: flex; align-items: center; }
  /* An earlier search, after what's typed: the same face and size as the input, the typed part invisible */
  .ghost {
    position: absolute;
    inset: 0 auto 0 0;
    display: flex;
    align-items: center;
    white-space: pre;
    overflow: hidden;
    max-width: 100%;
    pointer-events: none;
    color: var(--ink-3);
    font: 600 var(--text-2xl) / 1 var(--face-sign);
    font-variation-settings: "wdth" 100;
    letter-spacing: -0.02em;
  }
  .compact .ghost { font-size: var(--text-xl); }
  .ghost .typed { visibility: hidden; }
  .ghost kbd { margin-left: var(--s-2); padding: 1px 5px; border-radius: var(--r-steel); background: var(--tag);
               color: var(--tag-ink); font: 400 10px / 1.4 var(--face-data); letter-spacing: 0; }

  .like { display: inline-flex; align-items: center; gap: var(--s-2); flex: none; max-width: 46%; height: 40px;
          padding: 0 4px 0 4px; border-radius: var(--r-field); background: var(--found-wash);
          box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--found) 40%, transparent); color: var(--ink); }
  .compact .like { height: 36px; }
  .like img { width: 32px; height: 32px; border-radius: 4px; object-fit: cover; flex: none; background: var(--paper); }
  .compact .like img { width: 28px; height: 28px; }
  .like-words { display: flex; align-items: baseline; gap: 6px; min-width: 0; padding-left: 4px; }
  .like-words small { color: var(--ink-2); font-size: var(--text-xs); flex: none; }
  .like-words b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--text-sm); }
  .like-off { display: grid; place-items: center; width: 26px; height: 26px; flex: none; border-radius: 50%; color: var(--ink-2); }
  .like-off:hover { background: var(--floor-sunk); color: var(--ink); }

  input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 600 var(--text-2xl) / 1 var(--face-sign);
    font-variation-settings: "wdth" 100;
    letter-spacing: -0.02em;
    user-select: text;
    outline: none;   /* the field shows focus: its border and ring */
  }
  .compact input { font-size: var(--text-xl); }
  input::placeholder { color: var(--ink-3); font-weight: 400; }

  .clear { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; color: var(--ink-3); }
  .example { display: inline-flex; align-items: center; gap: 6px; flex: none; height: 34px; padding: 0 12px;
             border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor);
             color: var(--ink-2); font: 500 var(--text-sm) / 1 var(--face-ui); white-space: nowrap; }
  .example:hover { background: var(--floor-sunk); color: var(--ink); border-color: color-mix(in srgb, var(--found) 45%, var(--hairline)); }
  @container (max-width: 560px) { .example span { display: none; } .example { padding: 0 9px; } }
  .clear:hover { background: var(--floor-sunk); color: var(--ink); }

  .row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2) var(--s-4); min-width: 0;
         flex-wrap: wrap; }
  .drawers { display: flex; gap: 2px; }   /* the drawers stay one row; a narrow window puts the rest under them */
  .drawers button {
    padding: 5px 11px;
    border-radius: var(--r-steel);
    font: 500 var(--text-sm) / 1.2 var(--face-ui);
    color: var(--ink-2);
    border: 1px solid transparent;
    transition: background var(--t-rail) var(--rail), color var(--t-rail) var(--rail);
  }
  .drawers button:hover { background: var(--floor-sunk); color: var(--ink); }
  .drawers button.on { background: var(--steel); color: var(--steel-ink); }

  .right { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: var(--s-3);
           flex: none; max-width: 100%; margin-left: auto; }
  .note { color: var(--ink-3); font-size: var(--text-sm); font-variant-numeric: tabular-nums; }
  .counter { container-type: inline-size; }
  @container (max-width: 760px) {
    .drawers button { padding: 5px 7px; }
  }

  .layout { display: flex; padding: 2px; border-radius: var(--r-field); background: var(--floor-sunk); }
  .layout button { display: grid; place-items: center; width: 34px; height: 28px; border-radius: 4px; color: var(--ink-3); }
  .layout button.on { background: var(--floor-raised); color: var(--ink); box-shadow: var(--lift-1); }
</style>
