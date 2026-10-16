<script lang="ts">
  // Settings, applied as they change, one section at a time (as Windows' own Settings): General, what's searched,
  // indexing (what it's doing, where and when files are read), the search model, privacy, and the index itself with
  // its careful actions apart. Folders are managed in the Library; here, a summary and the way there.

  import { onMount, untrack } from "svelte";
  import { api, listen, type Kind, type ModelJob, type SearchModel } from "../lib/api";
  import { KIND_PLURAL, dowse, size } from "../lib/app.svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "../components/Icon.svelte";
  import Toggle from "../components/Toggle.svelte";
  import Segmented from "../components/Segmented.svelte";
  import ModelPicker from "../components/ModelPicker.svelte";
  import ModelProgress from "../components/ModelProgress.svelte";
  import { search } from "../lib/search.svelte";
  import { preparationBusy } from "../lib/model-preparation";

  const settings = $derived(dowse.state!.settings);
  const selection = $derived(dowse.state!.selection);
  const device = $derived(dowse.state!.device);

  let counts = $state<Record<string, number>>({});
  let facts = $state<{ files: number; activeFiles?: number; vectors: number; bytes: number } | null>(null);
  let recording = $state(false);
  let hotkeyProblem = $state("");
  let processorRequest = $state(false);
  let processorError = $state("");
  let confirm = $state<"rebuild" | "encrypt" | null>(null);
  let extensions = $state((dowse.state?.settings.skip_extensions ?? []).join(", "));
  let models = $state<SearchModel[]>([]);
  let modelChoice = $state(dowse.state?.searchModel.id ?? "");
  let modelJob = $state<ModelJob | null>(null);
  let confirmModel = $state(false);
  let modelRequest = $state(false);
  let modelError = $state("");
  let modelRevision = 0;
  let themeBusy = $state(false);
  const changingModel = $derived(preparationBusy(modelJob, modelRequest || processorRequest));
  const nextModel = $derived(models.find((m) => m.id === modelChoice));

  async function refreshModels(): Promise<void> {
    const revision = modelRevision;
    const answer = await api.models();
    models = answer.models;
    // A slower snapshot must not replace a newer progress/completion event.
    if (revision === modelRevision) modelJob = answer.job;
    if (modelJob?.state === "running" && modelJob.kind === "model") modelChoice = modelJob.target;
  }

  async function switchModel(): Promise<void> {
    confirmModel = false; modelError = ""; modelRequest = true;
    const revision = modelRevision;
    try {
      const answer = await api.changeModel(modelChoice);
      if (revision === modelRevision) modelJob = answer.job;
    }
    catch (error) { modelError = String((error as Error).message ?? error); }
    finally { modelRequest = false; }
  }

  async function switchProcessor(backend: string): Promise<void> {
    if (changingModel || backend === (device.onGpu ? "auto" : "cpu")) return;
    processorError = ""; processorRequest = true;
    try {
      // Its response contains the pre-switch device; completion refreshes the actual active device.
      await api.settings({ backend });
      await refreshModels();
    } catch (error) {
      processorError = String((error as Error).message ?? error);
      // The request may have reached the server before its response was lost.
      await refreshModels().catch(() => {});
    } finally { processorRequest = false; }
  }

  async function cancelModel(): Promise<void> {
    try { await api.cancelModel(); }
    catch (error) {
      const message = String((error as Error).message ?? error);
      if (modelJob?.kind === "processor") processorError = message; else modelError = message;
      await refreshModels().catch(() => {});
    }
  }

  onMount(() => {
    api.library("named").then((answer) => { counts = answer.counts; });
    api.facts().then((answer) => { facts = answer; });
    const stop = listen({
      switched: () => {
        dowse.refresh().catch((error) => { processorError = String(error); });
        refreshModels().catch((error) => { processorError = String(error); });
      },
      modelChange: (job: ModelJob) => { modelRevision++; modelJob = job; },
      modelChanged: () => {
        dowse.refresh().catch((error) => { modelError = String(error); });
        refreshModels().catch((error) => { modelError = String(error); });
      },
    });
    // Subscribe first, then recover jobs already in progress (e.g. after revisiting Settings).
    refreshModels().catch((error) => { modelError = String(error); });
    dowse.refresh().catch((error) => { modelError = String(error); });
    return stop;
  });

  async function change(changes: Record<string, unknown>): Promise<void> {
    const answer = await api.settings(changes);
    if (answer.state) dowse.state = answer.state;
  }

  let historyCleared = $state(false);
  async function rememberHistory(on: boolean): Promise<void> {
    try {
      await change({ history: on });
      if (!on) search.forgetHistory();
    } catch (error) { dowse.error = String((error as Error).message ?? error); }
  }
  async function clearHistory(): Promise<void> {
    try {
      await api.clearHistory();
      search.forgetHistory();
      historyCleared = true;
      setTimeout(() => (historyCleared = false), 1800);
    } catch (error) { dowse.error = String((error as Error).message ?? error); }
  }

  async function changeTheme(theme: string): Promise<void> {
    themeBusy = true;
    try {
      await change({ theme });
      dowse.applyTheme();
    } catch (error) { dowse.error = String((error as Error).message ?? error); }
    finally { themeBusy = false; }
  }

  $effect(() => { void dowse.indexVersion; untrack(() => api.library("folders").then((answer) => { counts = answer.counts; }).catch(() => {})); });

  async function addFolder(): Promise<void> {
    const { paths } = await api.pick("folder");
    if (paths[0]) {
      try { const answer = await api.choose("add", { paths });
        if (answer.changed) dowse.tell("Folder added. Dowse reads new files in the background.", answer.before);
        await dowse.refresh();
      } catch (error) { dowse.error = String(error); }
    }
  }

  const KINDS: Kind[] = ["image", "screenshot", "pdf", "doc", "audio", "video"];
  const KIND_ICON: Record<string, IconName> = { image: "image", screenshot: "screenshot", pdf: "pdf", doc: "doc",
                                                audio: "audio", video: "video" };

  function toggleKind(kind: Kind): void {
    const skipped = settings.skip_kinds.includes(kind)
      ? settings.skip_kinds.filter((k) => k !== kind) : [...settings.skip_kinds, kind];
    change({ skipKinds: skipped });
  }

  function saveExtensions(): void {
    const list = extensions.split(/[\s,;]+/).map((e) => e.replace(/^\*?\./, "").toLowerCase()).filter(Boolean);
    extensions = [...new Set(list)].join(", ");
    change({ skipExtensions: list });
  }

  function record(event: KeyboardEvent): void {
    event.preventDefault();
    if (event.key === "Escape") { recording = false; hotkeyProblem = ""; return; }
    if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) return;
    const mods = [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Win"].filter(Boolean);
    if (!mods.length || (mods.length === 1 && mods[0] === "Shift")) { hotkeyProblem = "Add Ctrl, Alt or Win"; return; }
    const key = event.key === " " ? "Space" : event.key.length === 1 ? event.key.toUpperCase() : event.key;
    const combination = [...mods, key].join("+");
    api.settings({ hotkey: combination }).then((answer) => {
      if (answer.ok) { recording = false; hotkeyProblem = ""; if (answer.state) dowse.state = answer.state; }
      else hotkeyProblem = answer.why ?? "That one can't be used";
    });
  }

  function name(path: string): string { return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path; }

  const SECTIONS: { id: string; label: string; icon: IconName; about: string }[] = [
    { id: "general", label: "General", icon: "settings", about: "How Dowse looks and how you reach it." },
    { id: "files", label: "Files & folders", icon: "folder", about: "What Dowse searches." },
    { id: "indexing", label: "Indexing", icon: "chip", about: "Reading your files so they can be found." },
    { id: "model", label: "Search model", icon: "sparkle", about: "The model that understands your files. It runs on this PC." },
    { id: "privacy", label: "Privacy", icon: "shield", about: "What Dowse keeps, and how it's protected." },
    { id: "index", label: "Index & storage", icon: "storage", about: "What Dowse has read, where it's kept, and starting over." },
  ];
  const section = $derived(SECTIONS.find((s) => s.id === dowse.path.split("/")[2]) ?? SECTIONS[0]);
  const status = $derived(dowse.state!.status);
  const STATUS_NOTE: Record<string, string> = {
    starting: "Getting ready to read your files.",
    reading: "Search works meanwhile, and goes first while you use it.",
    idle: "New and changed files are read as they appear.",
    waiting: "Another copy of Dowse is reading the same index; this one waits for it.",
    off: "Nothing is chosen to search yet. Add a folder in Files & folders.",
    error: "Reading stopped. Open the logs in Index & storage to see why.",
  };
  const total = $derived(counts.all ?? facts?.files ?? 0);
  const removedCount = $derived(selection.removed.length + selection.forgotten.length);
  $effect(() => { if (section.id === "index") untrack(() => api.facts().then((answer) => { facts = answer; }).catch(() => {})); });
</script>

<section class="settings">
  <nav class="sections" aria-label="Settings sections">
    <h1 class="sign">Settings</h1>
    {#each SECTIONS as s (s.id)}
      <button class:on={section.id === s.id} aria-current={section.id === s.id ? "page" : undefined}
              onclick={() => dowse.go(`/settings/${s.id}`)}><Icon name={s.icon} size={18} />{s.label}</button>
    {/each}
  </nav>

  <div class="page">
    <header>
      <h2 class="sign">{section.label}</h2>
      <p>{section.about}</p>
    </header>

    {#if section.id === "general"}
      <div class="card">
        <div class="row">
          <span class="what"><b>Theme</b><span>Follow Windows, or always light or dark.</span></span>
          <Segmented label="Theme" value={settings.theme ?? "system"} disabled={themeBusy}
                     options={[{ value: "system", label: "Follow Windows" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]}
                     onchange={changeTheme} />
        </div>
        <div class="row">
          <span class="what"><b>Shortcut</b><span>Opens Quick search from any app.{#if hotkeyProblem} <em>{hotkeyProblem}.</em>{/if}</span></span>
          <button class="keys" class:recording onclick={() => { recording = true; hotkeyProblem = ""; }} onkeydown={(e) => recording && record(e)}
                  onblur={() => (recording = false)} title="Click, then press the new shortcut">
            {#if recording}Press the new shortcut…{:else}{#each settings.hotkey.split("+") as key}<kbd>{key}</kbd>{/each}{/if}
          </button>
        </div>
        <div class="row">
          <span class="what"><b>Start with Windows</b><span>Dowse waits in the tray and keeps up with your files.</span></span>
          <Toggle on={settings.startup} label="Start with Windows" onchange={(on) => change({ startup: on })} />
        </div>
        <div class="row">
          <span class="what"><b>Explorer's right-click menu</b><span>Add, remove, find similar or search in a folder from Explorer. On Windows 11: Show more options.</span></span>
          <Toggle on={settings.explorerMenu} label="Explorer's right-click menu" onchange={(on) => change({ explorerMenu: on })} />
        </div>
      </div>
      <p class="version data">Dowse {dowse.state?.version ?? ""}</p>

    {:else if section.id === "files"}
      <div class="card summary">
        <span class="summary-text">
          <b>{selection.folders.length} {selection.folders.length === 1 ? "folder" : "folders"}{#if selection.files.length}&nbsp;and {selection.files.length} {selection.files.length === 1 ? "file" : "files"} added by name{/if}</b>
          <span>{total.toLocaleString()} files in all{#if selection.inactive.length}<span class="sep">·</span><button class="link" onclick={() => dowse.go("/library/inactive")}>{selection.inactive.length} switched off</button>{/if}{#if removedCount}<span class="sep">·</span><button class="link" onclick={() => dowse.go("/library/removed")}>{removedCount} removed</button>{/if}</span>
        </span>
        <span class="buttons">
          <button class="plain" onclick={addFolder}><Icon name="add" size={18} />Add a folder</button>
          <button class="primary" onclick={() => dowse.go("/library/folders")}>Manage in Library</button>
        </span>
      </div>

      <div class="card">
        <div class="row stacked">
          <span class="what"><b>Kinds of files</b><span>Switched off, a kind leaves search. Files you add by name are always searched.</span></span>
          <div class="kinds" role="group" aria-label="Kinds of files searched">
            {#each KINDS as kind (kind)}
              <button class="kind" class:on={!settings.skip_kinds.includes(kind)} aria-pressed={!settings.skip_kinds.includes(kind)}
                      onclick={() => toggleKind(kind)}><Icon name={KIND_ICON[kind]} size={18} />{KIND_PLURAL[kind]}{#if !settings.skip_kinds.includes(kind)}<Icon name="check" size={14} />{/if}</button>
            {/each}
          </div>
        </div>
        <div class="row">
          <span class="what"><b>Skip these extensions</b><span>Wherever they are, separated by commas.</span></span>
          <input class="text data" bind:value={extensions} onblur={saveExtensions} onkeydown={(e) => e.key === "Enter" && saveExtensions()}
                 placeholder="txt, log, csv" spellcheck="false" aria-label="Extensions to skip" />
        </div>
        <div class="row">
          <span class="what"><b>Add new screenshots</b><span>Screenshots saved with Win+PrtScn go to your Dowsed files as you take them.</span></span>
          <Toggle on={settings.screenshots} label="Add new screenshots" onchange={(on) => change({ screenshots: on })} />
        </div>
      </div>

    {:else if section.id === "indexing"}
      <div class="card status" class:paused={settings.paused}>
        <span class="dot" class:busy={status.state === "reading"}></span>
        <span class="what"><b>{settings.paused ? "Paused" : status.text}</b><span>{settings.paused ? "Search works with what's already read." : STATUS_NOTE[status.state] ?? ""}</span></span>
        <button class="plain" onclick={() => change({ paused: !settings.paused })}>
          <Icon name={settings.paused ? "play" : "pause"} size={18} />{settings.paused ? "Resume" : "Pause"}
        </button>
      </div>
      <div class="card">
        <div class="row">
          <span class="what"><b>Read files with</b><span>{device.gpu ? (device.onGpu ? `${device.gpu}: the fastest.` : `Your ${device.gpu} reads several times faster.`) : "No graphics card on this PC is suited to it."}</span></span>
          <Segmented label="Read files with" value={device.onGpu ? "auto" : "cpu"} disabled={!device.gpu || changingModel}
                     options={[{ value: "auto", label: "Graphics card" }, { value: "cpu", label: "Processor" }]}
                     onchange={switchProcessor} />
        </div>
        {#if modelJob?.kind === "processor"}<ModelProgress job={modelJob} oncancel={cancelModel} />{/if}
        {#if processorError}<p class="warning" role="alert">{processorError}</p>{/if}
        <div class="row">
          <span class="what"><b>Only when plugged in</b><span>Waits while on battery. Always waits on Battery Saver.</span></span>
          <Toggle on={settings.pluggedInOnly} label="Only when plugged in" onchange={(on) => change({ pluggedInOnly: on })} />
        </div>
      </div>

    {:else if section.id === "model"}
      <div class="card">
        <p class="model-note">Using <b>{dowse.state!.searchModel.name}</b>. Your files never leave this PC.</p>
        {#if models.length}<ModelPicker {models} bind:value={modelChoice} disabled={changingModel} />{/if}
        {#if confirmModel && nextModel}
          <div class="confirm-box" role="group" aria-label="Confirm model change">
            <b>Switch to {nextModel.name}?</b>
            <p>It's downloaded and checked first; then Dowse reads your files again. Names and text stay searchable meanwhile, and your files aren't touched.</p>
            {#if !nextModel.multimodal}<p class="warning">Photos, sound and video will be found by their names and words only, not by what's in them.</p>{/if}
            <div class="buttons"><button class="primary" onclick={switchModel}>{nextModel.installed ? "Check and switch" : "Download and switch"}</button><button class="quiet" onclick={() => (confirmModel = false)}>Cancel</button></div>
          </div>
        {:else}
          <div class="buttons"><button class="plain" disabled={changingModel || !nextModel || modelChoice === dowse.state!.searchModel.id}
                                        onclick={() => (confirmModel = true)}>Switch model…</button></div>
        {/if}
        {#if modelJob && modelJob.kind !== "processor"}<ModelProgress job={modelJob} oncancel={cancelModel} />{/if}
        {#if modelError}<p class="warning" role="alert">{modelError}</p>{/if}
      </div>

    {:else if section.id === "privacy"}
      <div class="card note">
        <Icon name="shield" size={22} />
        <span class="what"><b>Your files stay on this PC</b><span>They're read and searched here, never uploaded. Dowse only goes online to download its model.</span></span>
      </div>
      <div class="card">
        <div class="row">
          <span class="what"><b>Remember what you open</b><span>Files you went back to come first, and earlier searches are offered as you type. Off forgets them.</span></span>
          <span class="inline">
            <button class="quiet" onclick={clearHistory} disabled={!settings.history}>{historyCleared ? "Cleared" : "Clear history"}</button>
            <Toggle on={settings.history} label="Remember what you open" onchange={rememberHistory} />
          </span>
        </div>
        <div class="row">
          <span class="what"><b>Encrypt the index</b><span>{settings.encryptionSupported
            ? "With your Windows sign-in, so a copy can't be read elsewhere. Dowse restarts to apply it."
            : "Not on this edition of Windows; Device encryption or BitLocker protects the whole drive instead."}</span></span>
          {#if confirm === "encrypt"}
            <span class="inline"><button class="primary" onclick={() => change({ encrypt: !settings.encrypt })}>Restart Dowse</button><button class="quiet" onclick={() => (confirm = null)}>Cancel</button></span>
          {:else}
            <Toggle on={settings.encrypt} label="Encrypt the index" disabled={!settings.encryptionSupported} onchange={() => (confirm = "encrypt")} />
          {/if}
        </div>
      </div>

    {:else if section.id === "index"}
      <div class="card">
        {#if facts}
          <dl class="facts">
            <div><dt>Files</dt><dd class="data">{facts.files.toLocaleString()}</dd></div>
            {#if facts.activeFiles !== undefined}<div><dt>Searched now</dt><dd class="data">{facts.activeFiles.toLocaleString()}</dd></div>{/if}
            <div><dt>Pictures, pages, passages and moments</dt><dd class="data">{facts.vectors.toLocaleString()}</dd></div>
            <div><dt>Space on disk</dt><dd class="data">{size(facts.bytes)}</dd></div>
          </dl>
        {:else}
          <p class="model-note">Counting…</p>
        {/if}
        <div class="buttons">
          <button class="plain" onclick={() => api.openFolder("index")}><Icon name="folderOpen" size={18} />Open the index folder</button>
          <button class="plain" onclick={() => api.openFolder("logs")}><Icon name="folderOpen" size={18} />Open the logs</button>
        </div>
      </div>
      <div class="card careful">
        <span class="what"><b>Rebuild the index</b><span>Reads every file again from scratch. Search finds less until it's done. Only needed if something seems wrong.</span></span>
        {#if confirm === "rebuild"}
          <span class="inline"><button class="danger" onclick={() => { change({ rebuild: true }); confirm = null; }}>Rebuild</button><button class="quiet" onclick={() => (confirm = null)}>Cancel</button></span>
        {:else}
          <button class="plain" disabled={changingModel} onclick={() => (confirm = "rebuild")}><Icon name="again" size={18} />Rebuild…</button>
        {/if}
      </div>
    {/if}
  </div>
</section>

<style>
  .settings { display: grid; grid-template-columns: 236px minmax(0, 1fr); height: 100%; min-height: 0; }

  .sections { display: flex; flex-direction: column; gap: 2px; padding: var(--s-5) var(--s-3); border-right: 1px solid var(--hairline); overflow-y: auto; }
  .sections h1 { margin: 0 var(--s-3) var(--s-4); font-size: var(--text-xl); }
  .sections button {
    display: flex; align-items: center; gap: var(--s-3); padding: 9px var(--s-3); border-radius: var(--r-steel);
    color: var(--ink-2); font-size: var(--text-sm); text-align: left;
  }
  .sections button:hover { background: var(--floor-sunk); color: var(--ink); }
  .sections button.on { background: var(--floor-raised); color: var(--ink); font-weight: 600; box-shadow: inset 0 0 0 1px var(--hairline), var(--lift-1); }
  .sections button.on :global(.icon) { color: var(--found-ink); }

  .page { overflow-y: auto; min-height: 0; padding: var(--s-6) var(--s-7) var(--s-8); }
  .page > * { max-width: 820px; }
  header { margin-bottom: var(--s-5); }
  header h2 { margin: 0; font-size: var(--text-2xl); line-height: 1.1; }
  header p { margin: 6px 0 0; color: var(--ink-2); font-size: var(--text-sm); }

  .card { display: grid; padding: var(--s-2) var(--s-5); margin-bottom: var(--s-4); border: 1px solid var(--hairline);
          border-radius: var(--r-field); background: var(--floor-raised); }
  .row { display: flex; align-items: center; justify-content: space-between; gap: var(--s-5); padding: var(--s-4) 0; min-height: 64px; }
  .row.stacked { flex-direction: column; align-items: stretch; gap: var(--s-3); }
  .row + .row { border-top: 1px solid color-mix(in srgb, var(--hairline) 70%, transparent); }
  .what { display: grid; gap: 3px; min-width: 0; }
  .what b { font-weight: 600; }
  .version { margin: 0; padding: 0 var(--s-2); color: var(--ink-2); font-size: var(--text-xs); }
  .what span { color: var(--ink-2); font-size: var(--text-sm); line-height: 1.45; max-width: 62ch; }
  .what em { color: var(--warn); font-style: normal; }

  .summary { display: flex; align-items: center; justify-content: space-between; gap: var(--s-4); flex-wrap: wrap; padding: var(--s-4) var(--s-5); }
  .summary-text { display: grid; gap: 3px; }
  .summary-text b { font-size: var(--text-md, 15px); }
  .summary-text span { color: var(--ink-2); font-size: var(--text-sm); }
  .buttons { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2); padding: var(--s-3) 0; }
  .summary .buttons { padding: 0; }
  .inline { display: inline-flex; align-items: center; gap: var(--s-2); flex: none; }

  .status { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-4) var(--s-5); }
  .status .what { flex: 1; }
  .dot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--found); }
  .dot.busy { animation: breathe 1.6s var(--glide) infinite alternate; }
  .status.paused .dot { background: var(--ink-3); }
  @keyframes breathe { to { opacity: 0.3; } }

  .note { display: flex; align-items: center; gap: var(--s-4); padding: var(--s-4) var(--s-5); }
  .note :global(.icon) { color: var(--found-ink); flex: none; }
  .careful { display: flex; align-items: center; justify-content: space-between; gap: var(--s-4); padding: var(--s-4) var(--s-5);
             border-color: color-mix(in srgb, var(--warn) 40%, var(--hairline)); background: color-mix(in srgb, var(--warn) 5%, var(--floor-raised)); }

  .kinds { display: flex; flex-wrap: wrap; gap: var(--s-2); }
  /* On: tinted, with a check. Off: an empty outline, dimmed. */
  .kind { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px;
          border: 1px dashed var(--hairline); color: var(--ink-3); font-size: var(--text-sm); background: transparent; }
  .kind:hover { color: var(--ink); border-style: solid; }
  .kind.on { background: var(--found-wash); border: 1px solid color-mix(in srgb, var(--found) 55%, transparent); color: var(--ink); }
  .kind.on :global(.icon) { color: var(--found-ink); }
  .sep { margin: 0 6px; color: var(--ink-3); }

  .plain { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 12px; border-radius: var(--r-field);
           border: 1px solid var(--hairline); background: var(--floor); font-size: var(--text-sm); font-weight: 500; }
  .plain:hover:not(:disabled) { background: var(--floor-sunk); }
  .plain :global(.icon) { color: var(--found-ink); }
  .primary { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 14px; border-radius: var(--r-field);
             background: var(--steel); color: var(--steel-ink); font-size: var(--text-sm); font-weight: 600; }
  .primary:hover { background: var(--steel-raised); }
  .danger { height: 34px; padding: 0 14px; border-radius: var(--r-field); background: var(--warn); color: #fff; font-size: var(--text-sm); font-weight: 600; }
  .quiet { padding: 6px 10px; border-radius: var(--r-field); color: var(--ink-2); font-size: var(--text-sm); }
  .quiet:hover:not(:disabled) { background: var(--floor-sunk); color: var(--ink); }
  .link { color: var(--found-ink); text-decoration: underline; font-size: inherit; padding: 0; }
  button:disabled { opacity: 0.5; cursor: default; }

  .text { width: 220px; height: 34px; padding: 0 var(--s-3); border-radius: var(--r-field); border: 1px solid var(--hairline); background: var(--floor); outline: none; user-select: text; }
  .text:focus { border-color: var(--found); box-shadow: 0 0 0 3px var(--found-wash); }
  .keys { display: inline-flex; gap: 4px; padding: 4px; border-radius: var(--r-field); border: 1px solid var(--hairline); background: var(--floor); min-width: 120px; justify-content: center; font-size: var(--text-sm); }
  .keys.recording { border-color: var(--found); color: var(--found-ink); padding: 6px 12px; }
  kbd { padding: 3px 8px; border-radius: 4px; background: var(--tag); color: var(--tag-ink); font: 500 var(--text-xs) / 1.3 var(--face-data); }

  .model-note { margin: var(--s-3) 0; color: var(--ink-2); font-size: var(--text-sm); line-height: 1.5; }
  .confirm-box { display: grid; gap: var(--s-2); padding: var(--s-4); margin: var(--s-3) 0; border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor); }
  .confirm-box p { margin: 0; font-size: var(--text-sm); color: var(--ink-2); line-height: 1.5; }
  .warning { color: var(--warn); font-size: var(--text-sm); overflow-wrap: anywhere; }

  .facts { display: grid; gap: 8px; margin: var(--s-3) 0 0; }
  .facts div { display: flex; justify-content: space-between; gap: var(--s-4); }
  dt { color: var(--ink-2); font-size: var(--text-sm); }
  dd { margin: 0; }

  @media (max-width: 900px) {
    .settings { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
    .sections { flex-direction: row; flex-wrap: wrap; border-right: 0; border-bottom: 1px solid var(--hairline); }
    .sections h1 { flex-basis: 100%; }
    .row { flex-wrap: wrap; }
  }
  @media (prefers-reduced-motion: reduce) { .dot.busy { animation: none; } }
</style>
