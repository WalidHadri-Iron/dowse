<script lang="ts">
  // The first run: what Dowse does and that nothing leaves the PC; the folders to search, counted, with how long
  // reading them takes on this PC; the shortcut. The chosen model downloads after the model selection step.

  import { onMount } from "svelte";
  import { api, listen, type SetupProgress, type SearchModel, type ModelJob } from "../lib/api";
  import { KIND_PLURAL, dowse, duration } from "../lib/app.svelte";
  import { keyName } from "../lib/keys";
  import { startupName, system, trayName } from "../lib/system";
  import Icon from "../components/Icon.svelte";
  import Toggle from "../components/Toggle.svelte";
  import ModelPicker from "../components/ModelPicker.svelte";
  import Brand from "../components/Brand.svelte";

  interface Place { name: string; path: string; counts: Record<string, number> | null; chosen: boolean }

  let step = $state(0);
  let places = $state<Place[]>([]);
  let progress = $state<SetupProgress | null>(null);
  let failed = $state<string | null>(null);
  let ready = $state(!dowse.state?.setupNeeded);
  let gpu = $state<string | null>(null);
  let startup = $state(true);
  let estimate = $state<{ seconds: number; measured: boolean } | null>(null);
  let models = $state<SearchModel[]>([]);
  let model = $state(dowse.state?.searchModel.id ?? "");
  let modelJob = $state<ModelJob | null>(null);
  let preparing = $state(false);
  let hasIndex = $state(false);
  let confirmRebuild = $state(false);
  const needsConfirmation = $derived(hasIndex && model !== dowse.state?.searchModel.id);

  async function prepare(): Promise<void> {
    if (modelJob?.state === "running") { step = 2; return; }
    preparing = true; failed = null; ready = false;
    try { modelJob = (await api.setupStart(model, confirmRebuild)).job; step = 2; }
    catch (error) { failed = String((error as Error).message ?? error); }
    finally { preparing = false; }
  }

  async function cancelPreparation(): Promise<void> {
    try { await api.cancelModel(); }
    catch (error) { failed = String((error as Error).message ?? error); }
  }

  onMount(() => {
    // Becoming ready must not unmount onboarding before folders and shortcut choices are finished.
    if (dowse.state?.setupNeeded && dowse.path !== "/welcome") dowse.replace("/welcome");
    api.setup().then((answer) => {
      gpu = answer.gpu;
      progress = answer.progress;
      places = answer.folders.map((f, i) => ({ ...f, chosen: i < 2 || f.name === "Pictures" || f.name === "Documents" }));
    });
    api.models().then((answer) => {
      models = answer.models; hasIndex = answer.hasIndex; modelJob = answer.job;
      model = answer.job?.state === "running" && answer.job.kind === "setup" ? answer.job.target : answer.selected;
      if (answer.job?.state === "running") ready = false;
    }).catch((error) => { failed = String(error); });
    return listen({
      setup: (p: SetupProgress) => { progress = p; failed = null; },
      setupDone: () => { ready = true; dowse.refresh(); },
      setupFailed: ({ why }) => { failed = why; },
      modelChange: (job: ModelJob) => { modelJob = job; },
      counted: ({ folder, counts }) => { places = places.map((p) => (p.path === folder ? { ...p, counts } : p)); },
    });
  });

  const chosen = $derived(places.filter((p) => p.chosen));
  const counting = $derived(chosen.some((p) => p.counts === null));
  const total = $derived.by(() => {
    const out: Record<string, number> = {};
    for (const place of chosen) for (const [kind, n] of Object.entries(place.counts ?? {})) out[kind] = (out[kind] ?? 0) + n;
    return out;
  });

  $effect(() => {
    void total; void ready; void model;
    if (!counting && Object.keys(total).length) api.setupEstimate(total, model || undefined).then((answer) => { estimate = answer; });
    else estimate = null;
  });

  function describe(counts: Record<string, number> | null): string {
    if (!counts) return "Counting…";
    const parts = Object.entries(counts).filter(([, n]) => n).sort((a, b) => b[1] - a[1])
      .map(([kind, n]) => `${n.toLocaleString()} ${n === 1 ? KIND_PLURAL[kind].replace(/s$/, "") : KIND_PLURAL[kind]}`.toLowerCase());
    return parts.slice(0, 4).join(" · ") || "Nothing to search";
  }

  async function another(): Promise<void> {
    const { paths } = await api.pick("folder");
    if (!paths[0] || places.some((p) => p.path === paths[0])) return;
    const name = paths[0].replace(/[\\/]+$/, "").split(/[\\/]/).pop() || paths[0];
    places = [...places, { name, path: paths[0], counts: null, chosen: true }];
    const answer = await api.setupCount(paths[0]);
    if (answer.counts) places = places.map((p) => (p.path === paths[0] ? { ...p, counts: answer.counts } : p));
  }

  async function finish(): Promise<void> {
    await api.setupFinish(chosen.map((p) => p.path), startup);
    await dowse.refresh();
    dowse.go("/search");
  }

  // What the storehouse holds, as an example on the first screen: each kind of file, tagged with where it matched.
  const SHOWCASE = [
    { kind: "image", icon: "image" as const, name: "IMG_2041.jpg", line: "Photo · Pictures › 2024" },
    { kind: "pdf", icon: "pdf" as const, name: "lease.pdf", line: "PDF · p. 12 · Documents" },
    { kind: "audio", icon: "audio" as const, name: "episode 12.mp3", line: "Recording · 0:42" },
    { kind: "video", icon: "video" as const, name: "trip.mp4", line: "Video · 1:15 · Videos" },
    { kind: "doc", icon: "doc" as const, name: "meeting notes.docx", line: "Document · passage 4" },
    { kind: "screenshot", icon: "screenshot" as const, name: "Screenshot 3.png", line: "Screenshot · error E-4021" },
  ];

  const STAGE: Record<string, string> = {
    runtime: "Getting the search engine for your hardware", model: "Downloading the search model",
    validate: "Checking the search model", activate: "Activating the search model",
    speed: "Measuring this PC's speed", done: "Search model ready",
  };
  const megabytes = (n: number) => `${(n / 1048576).toFixed(0)} MB`;
  const files = $derived(Object.values(total).reduce((a, b) => a + b, 0));
</script>

<section class="welcome">
  <div class="rack" aria-hidden="true">
    {#each SHOWCASE as item, i}
      <div class="case" style:--i={i}>
        <div class="inside k-{item.kind}">
          {#if item.kind === "audio"}{#each Array.from({ length: 22 }, (_, n) => n) as n}<span style:height="{25 + Math.abs(Math.sin(n * 1.3 + i)) * 60}%"></span>{/each}
          {:else if item.kind === "pdf" || item.kind === "doc"}<p></p><p></p><mark></mark><p></p><p class="short"></p>
          {:else}<Icon name={item.icon} size={34} />{/if}
        </div>
        <div class="tag"><b>{item.name}</b><span>{item.line}</span></div>
      </div>
    {/each}
  </div>
  <div class="plate">
    <div class="mark"><Brand height={40} /></div>

    {#if step === 0}
      <h1 class="sign">Find anything by what's in it.</h1>
      <p class="lede">Describe what you remember: a photo by what it shows, a PDF by a clause on page 12, a recording by
        the minute something is said. Dowse reads your files here, on this PC, and never uploads them.</p>
      <ul class="truths">
        <li><Icon name="shield" size={20} /><span>Private by construction: it goes online only to download your chosen model and search engine.</span></li>
        <li><Icon name="chip" size={20} /><span>{gpu ? `Your ${gpu} does the work.` : "Your processor does the work: no graphics card here is suited to it."}</span></li>
        <li><Icon name="keyboard" size={20} /><span>Then it's a shortcut away, from any app.</span></li>
      </ul>
      <div class="next"><button class="go" onclick={() => (step = 1)}>Choose a search model<Icon name="chevron" size={18} /></button></div>

    {:else if step === 1}
      <h1 class="sign">Choose a search model</h1>
      <p class="lede">It runs here, on your PC. You can change it later in Settings; changing models rebuilds the search index, not your files.</p>
      <ModelPicker {models} bind:value={model} disabled={preparing || modelJob?.state === "running"} />
      {#if needsConfirmation}
        <label class="model-rebuild"><input type="checkbox" bind:checked={confirmRebuild} />Rebuild the existing search index for this model. My original files stay untouched.</label>
      {/if}
      <div class="next">
        <button class="quiet" onclick={() => (step = 0)}>Back</button>
        <button class="go" disabled={!models.length || preparing || (needsConfirmation && !confirmRebuild)} onclick={prepare}>
          {preparing ? "Preparing…" : modelJob?.state === "running" ? "Continue" : "Prepare and continue"}<Icon name="chevron" size={18} />
        </button>
      </div>

    {:else if step === 2}
      <h1 class="sign">What should Dowse search?</h1>
      <p class="lede">Pick folders; you can add single files, or remove anything, later. Dowse skips code projects,
        app data and hidden folders on its own.</p>
      <ul class="places">
        {#each places as place (place.path)}
          <li>
            <label class:chosen={place.chosen}>
              <input type="checkbox" bind:checked={place.chosen} />
              <Icon name="folder" size={22} />
              <span class="what"><b>{place.name}</b><span>{place.path}</span></span>
              <span class="count data">{describe(place.counts)}</span>
            </label>
          </li>
        {/each}
      </ul>
      <button class="plain" onclick={another}><Icon name="add" size={18} />Another folder</button>
      <p class="estimate">
        {#if counting}Counting the files in the folders you chose…
        {:else if files && estimate}{files.toLocaleString()} files to read: {duration(estimate.seconds)}{estimate.measured ? "" : " (a first guess, until this PC's speed is measured)"}, once. After that, new files are searchable seconds after they appear.{/if}
      </p>
      <div class="next">
        <button class="quiet" onclick={() => (step = 1)}>Back</button>
        <button class="go" disabled={!chosen.length} onclick={() => (step = 3)}>Continue<Icon name="chevron" size={18} /></button>
      </div>

    {:else}
      <h1 class="sign">Search from anywhere</h1>
      {#if system() === "linux"}
        <p class="lede">Open Dowse from your apps, or give it a shortcut in your desktop's keyboard settings: Settings shows
          the command it runs.</p>
      {:else}
        <p class="lede">Press <span class="keys">{#each (dowse.state?.settings.hotkey ?? "").split("+") as key}<kbd>{keyName(key)}</kbd>{/each}</span>
          in any app to open search. Change it any time in Settings.</p>
      {/if}
      <label class="startup">
        <Toggle on={startup} label={startupName()} onchange={(on) => (startup = on)} />
        <span class="what"><b>{startupName()}</b><span>Keeps your files searchable as they change, from {trayName()}.</span></span>
      </label>
      <div class="next">
        <button class="quiet" onclick={() => (step = 2)}>Back</button>
        <button class="go" disabled={!ready} onclick={finish}>{ready ? "Start searching" : "Preparing search…"}<Icon name="chevron" size={18} /></button>
      </div>
    {/if}

    {#if progress || failed || modelJob?.state === "running"}
      <div class="download" class:done={ready}>
        <Icon name={failed ? "warning" : ready ? "check" : "flash"} size={20} />
        <span class="what">
          <b>{failed ? "The download stopped" : ready ? "Search model ready" : STAGE[progress?.stage ?? "runtime"]}</b>
          <span class="data">{failed ?? (ready ? "" : progress && (progress.stage === "model" || progress.stage === "runtime") ? `${megabytes(progress.done)} of ${megabytes(progress.total)}${progress.rate ? ` · ${(progress.rate / 1048576).toFixed(0)} MB/s` : ""}${progress.left ? ` · ${duration(progress.left)} left` : ""}` : "Your original files stay untouched.")}</span>
        </span>
        {#if !ready && !failed}<span class="bar"><span style:width="{progress?.total ? (progress.done / progress.total) * 100 : 2}%"></span></span>{/if}
      </div>
      {#if failed}<div class="next"><button class="plain" onclick={prepare}>Retry preparation</button><button class="quiet" onclick={() => (step = 1)}>Choose another model</button></div>{/if}
      {#if modelJob?.state === "running" && modelJob.canCancel}<button class="quiet" onclick={cancelPreparation}>Cancel preparation</button>{/if}
    {/if}
  </div>
</section>

<style>
  .welcome {
    height: 100%;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(360px, 0.82fr);
    align-items: center;
    gap: var(--s-7);
    padding: var(--s-7) var(--s-8);
    background: var(--floor);
    overflow-y: auto;
  }
  .plate { order: -1; width: min(640px, 100%); justify-self: end; display: grid; gap: var(--s-4); }
  .model-rebuild { display: flex; align-items: flex-start; gap: var(--s-2); color: var(--warn); font-size: var(--text-sm); }

  /* The rack: six glass cases on steel, each with its accession tag */
  .rack {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s-4);
    padding: var(--s-5); border-radius: var(--r-steel); background: var(--steel);
    box-shadow: var(--lift-3), inset 0 1px 0 rgba(255, 255, 255, 0.06);
    max-width: 520px;
  }
  .case {
    position: relative; aspect-ratio: 4 / 3; border-radius: var(--r-glass);
    background: linear-gradient(160deg, rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0.12) 42%, rgba(255, 255, 255, 0.04));
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.55), inset 0 10px 18px rgba(255, 255, 255, 0.25), 0 10px 24px rgba(0, 0, 0, 0.35);
    padding: 8px;
    animation: pull var(--t-glide) var(--glide) both;
    animation-delay: calc(var(--i) * 70ms + 120ms);
  }
  @keyframes pull { from { opacity: 0; transform: translateY(10px) scale(0.96); } }
  .inside { height: 100%; border-radius: 9px; display: grid; place-items: center; color: #e8efed; overflow: hidden; }
  .k-image { background: linear-gradient(170deg, #9cc3d4, #e8d8b8 58%, #8a7d63); }
  .k-video { background: linear-gradient(160deg, #304b5c, #7aa0a8); }
  .k-screenshot { background: linear-gradient(180deg, #e9eef0 0 18%, #ffffff 18%); color: #1d3335; }
  .k-pdf, .k-doc { background: #fbfcfb; place-items: stretch; align-content: start; padding: 14px 16px; gap: 7px; display: grid; }
  .k-pdf p, .k-doc p { margin: 0; height: 6px; border-radius: 2px; background: #d7dfdc; }
  .k-pdf p.short, .k-doc p.short { width: 60%; }
  .k-pdf mark, .k-doc mark { display: block; height: 8px; border-radius: 2px; background: rgba(255, 214, 10, 0.7); width: 74%; }
  .k-audio { background: #1d3335; display: flex; align-items: center; gap: 3px; padding: 0 14px; }
  .k-audio span { flex: 1; border-radius: 2px; background: var(--found); }
  .case .tag {
    position: absolute; left: 14px; bottom: 12px; max-width: calc(100% - 28px);
    display: grid; padding: 4px 8px; border-radius: var(--r-steel); background: var(--tag); color: var(--tag-ink);
    box-shadow: var(--lift-1);
  }
  .case .tag b { font: 600 var(--text-xs) / 1.25 var(--face-ui); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .case .tag span { font: 400 10px / 1.3 var(--face-data); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  @media (max-width: 1100px) { .welcome { grid-template-columns: minmax(0, 1fr); } .rack { display: none; } .plate { justify-self: center; } }
  .mark { justify-self: start; margin-bottom: var(--s-4); }
  h1 { margin: 0; font-size: var(--text-3xl); line-height: 1.05; text-wrap: balance; }
  .lede { margin: 0; color: var(--ink-2); font-size: var(--text-lg); max-width: 62ch; line-height: 1.5; }
  .truths { list-style: none; margin: var(--s-3) 0 0; padding: 0; display: grid; gap: var(--s-3); }
  .truths li { display: flex; gap: var(--s-3); align-items: center; }
  .truths :global(.icon) { color: var(--found-ink); }

  .places { list-style: none; margin: var(--s-2) 0 0; padding: 0; display: grid; gap: 2px; }
  .places label {
    display: flex; align-items: center; gap: var(--s-3); padding: var(--s-3) var(--s-4); border-radius: var(--r-field);
    background: var(--floor-raised); cursor: pointer; border: 1px solid transparent;
  }
  .places label.chosen { border-color: color-mix(in srgb, var(--found) 45%, transparent); }
  .places input { accent-color: var(--found); width: 18px; height: 18px; }
  .places :global(.icon) { color: var(--found-ink); }
  .what { display: grid; gap: 1px; min-width: 0; flex: 1; }
  .what span { color: var(--ink-3); font-size: var(--text-sm); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .count { color: var(--ink-2); text-align: right; }
  .estimate { margin: 0; min-height: 1.5em; color: var(--ink-2); font-size: var(--text-sm); }

  .startup { display: flex; gap: var(--s-4); align-items: center; padding: var(--s-4); border-radius: var(--r-field); background: var(--floor-raised); }
  .keys { display: inline-flex; gap: 4px; vertical-align: middle; }
  kbd { padding: 3px 8px; border-radius: 4px; background: var(--tag); color: var(--tag-ink); font: 500 var(--text-sm) / 1.3 var(--face-data); }

  .next { display: flex; justify-content: flex-end; gap: var(--s-2); margin-top: var(--s-3); }
  .go { display: inline-flex; align-items: center; gap: var(--s-2); padding: 10px 18px; border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); font-weight: 600; }
  .go :global(.icon) { transform: rotate(-90deg); }
  .go:hover:not(:disabled) { background: var(--steel-raised); }
  .quiet { padding: 10px 14px; border-radius: var(--r-field); color: var(--ink-2); }
  .quiet:hover { background: var(--floor-sunk); }
  .plain { justify-self: start; display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: var(--r-field); border: 1px solid var(--hairline); background: var(--floor-raised); font-size: var(--text-sm); }

  .download { display: flex; align-items: center; gap: var(--s-3); margin-top: var(--s-5); padding: var(--s-3) var(--s-4); border-radius: var(--r-field); background: var(--floor-raised); box-shadow: inset 0 0 0 1px var(--hairline); }
  .download :global(.icon) { color: var(--found-ink); }
  .bar { width: 180px; height: 4px; border-radius: 2px; background: var(--floor-sunk); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--found); transition: width var(--t-glide) var(--glide); }
</style>
