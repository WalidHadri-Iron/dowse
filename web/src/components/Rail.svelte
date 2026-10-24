<script lang="ts">
  import { api } from "../lib/api";
  import { shortcut } from "../lib/keys";
  import { dowse, size } from "../lib/app.svelte";
  import { windowMode } from "../lib/window.svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "./Icon.svelte";
  import Brand from "./Brand.svelte";

  let { current }: { current: string } = $props();

  const plates: { view: string; label: string; icon: IconName }[] = [
    { view: "search", label: "Search", icon: "search" },
    { view: "explore", label: "Explore", icon: "explore" },
    { view: "aisles", label: "Aisles", icon: "aisles" },
    { view: "library", label: "Library", icon: "library" },
    { view: "settings", label: "Settings", icon: "settings" },
  ];

  let counts = $state<Record<string, number>>({});
  let bytes = $state<number | null>(null);

  // Follow indexing, but not every step of it: a few seconds after the last change (while files are read, changes
  // come every second or two, and each look at the counts reads the whole index).
  let countsTimer = 0;
  $effect(() => {
    void dowse.indexVersion;
    clearTimeout(countsTimer);
    countsTimer = window.setTimeout(() => {
      api.library("named").then((answer) => { counts = answer.counts; }).catch(() => {});
      api.indexBytes().then((facts) => { bytes = facts.bytes; }).catch(() => {});
    }, counts && Object.keys(counts).length ? 3000 : 0);
    return () => clearTimeout(countsTimer);
  });

  const status = $derived(dowse.state?.status);
  const model = $derived(dowse.state?.model ?? "asleep");
  const within = $derived(dowse.params.get("in"));


  const STATUS_WORD: Record<string, string> = {
    starting: "starting", reading: "reading", idle: "up to date", paused: "paused", waiting: "busy elsewhere",
    off: "off", error: "stopped",
  };

  const modelText: Record<string, string> = {
    asleep: "asleep", loading: "waking", ready: "awake", unavailable: "words only",
  };
</script>

<nav aria-label="Dowse">
  <a class="brand" href="#/search" aria-label="Dowse search">
    <Brand height={30} inverse />
  </a>

  <ul class="plates">
    {#each plates as plate (plate.view)}
      <li>
        <a href="#/{plate.view}" class="plate sign" class:on={current === plate.view && !(plate.view === "search" && within)}
           aria-current={current === plate.view ? "page" : undefined}>
          <Icon name={plate.icon} size={20} />
          <span>{plate.label}</span>
          {#if plate.view === "library" && counts.all !== undefined}
            <span class="count data">{counts.all.toLocaleString()}</span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>

  <button class="quick" onclick={() => windowMode.change(true)} disabled={windowMode.switching}
          title="Switch this window to quick search ({shortcut('O')})">
    <Icon name="search" size={18} /><span>Quick search</span><kbd>{shortcut("O")}</kbd>
  </button>

  <dl class="instruments" aria-label="How Dowse is doing">
    <div title={status?.text}>
      <dt>Reading</dt>
      <dd class:busy={status?.state === "reading" || status?.state === "starting"}
          class:still={status?.state === "paused" || status?.state === "off" || status?.state === "waiting"}>
        {#if status?.state === "reading"}<span class="data">{status.done.toLocaleString()}/{status.total.toLocaleString()}</span>
        {:else}{STATUS_WORD[status?.state ?? "starting"]}{/if}
      </dd>
    </div>
    <div>
      <dt>Model</dt>
      <dd class:busy={model === "loading"} class:still={model === "asleep"}>{modelText[model]}</dd>
    </div>
    <div>
      <dt>Index</dt>
      <dd class:still={bytes === null}><span class="data">{bytes === null ? "…" : size(bytes)}</span></dd>
    </div>
  </dl>
</nav>

<style>
  nav {
    background: var(--steel);
    color: var(--steel-ink);
    display: flex;
    flex-direction: column;
    padding: var(--s-5) var(--s-3) var(--s-4);
    min-height: 0;
    /* The rack's end plate: a brushed steel face with a machined edge */
    background-image: linear-gradient(90deg, transparent calc(100% - 1px), var(--steel-line) 0);
  }

  .brand { display: flex; align-items: center; gap: var(--s-3); padding: 0 var(--s-3) var(--s-6); }
  .quick { display: flex; align-items: center; gap: var(--s-2); flex: none;
           margin-top: auto; padding: var(--s-3); border-radius: var(--r-field);
           color: var(--steel-ink-2); text-align: left; }
  .quick:hover { background: var(--steel-raised); color: var(--steel-ink); }
  .quick kbd { margin-left: auto; font: 400 10px var(--face-data); }

  ul { list-style: none; margin: 0; padding: 0; }
  .plates { display: grid; gap: 2px; }

  .plate {
    display: flex;
    align-items: center;
    gap: var(--s-3);
    padding: 11px var(--s-3);
    border-radius: var(--r-steel);
    color: var(--steel-ink-2);
    text-decoration: none;
    font-size: 1.125rem;
    transition: background var(--t-rail) var(--rail), color var(--t-rail) var(--rail);
  }
  .plate:hover { background: var(--steel-raised); color: var(--steel-ink); }
  .plate.on {
    background: var(--steel-raised);
    color: var(--steel-ink);
    box-shadow: inset 0 0 0 1px var(--steel-line), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  }
  .plate.on :global(.icon) { color: var(--found); }
  .count { margin-left: auto; color: var(--steel-ink-2); }

  .instruments {
    margin: var(--s-4) 0 0;
    padding: var(--s-3) var(--s-3) 0;
    border-top: 1px solid var(--steel-line);
    display: grid;
    gap: 6px;
  }
  .instruments div { display: flex; justify-content: space-between; align-items: baseline; }
  dt { font-size: var(--text-xs); color: var(--steel-ink-2); }
  dd { margin: 0; color: var(--steel-ink); display: flex; align-items: center; gap: 6px; font-size: var(--text-sm); }
  dd::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--found); }
  dd.busy::before { animation: breathe 1.6s var(--glide) infinite alternate; }
  dd.still::before { background: var(--steel-ink-2); }
  @keyframes breathe { to { opacity: 0.25; } }
</style>
