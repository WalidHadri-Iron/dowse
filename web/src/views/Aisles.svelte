<script lang="ts">
  // Aisles: shelves of your own, for looking at first. Choose one and its files are there, as pictures; take one out
  // or add one that might belong, at once (Undo puts it back). Its name and what goes in it change under Edit, and a
  // new aisle is one short form: a name, a sentence about what goes in it, and a live look at what it would hold.
  // Dowse's own ideas (files it found alike) are a click from being aisles.

  import { onMount, untrack } from "svelte";
  import { api, pictureUrl, type AisleDefinition, type AisleFile, type MyAisle, type SuggestedAisle } from "../lib/api";
  import { KIND_PLURAL, dowse } from "../lib/app.svelte";
  import type { Kind } from "../lib/api";
  import Toggle from "../components/Toggle.svelte";
  import Toast from "../components/Toast.svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "../components/Icon.svelte";

  const ICON: Record<string, IconName> = { image: "image", screenshot: "screenshot", pdf: "pdf", doc: "doc",
                                           audio: "audio", video: "video" };
  const KINDS: Kind[] = ["image", "screenshot", "pdf", "doc", "audio", "video"];
  // Ideas for a first aisle: a click fills the form, to change as you like
  const EXAMPLES = [
    { name: "Bills & receipts", about: "receipts, invoices and bills, anything I paid for" },
    { name: "Pets", about: "photos and videos of my cats and dogs" },
    { name: "Work", about: "contracts, reports and presentations for work" },
    { name: "Trips", about: "tickets, bookings, itineraries and photos from trips" },
    { name: "Recipes", about: "recipes, cooking and food" },
  ];

  type Chosen = { kind: "mine" | "idea"; id: string } | null;

  let mine = $state<MyAisle[]>([]);
  let ideas = $state<SuggestedAisle[]>([]);
  let grouped = $state(true);
  let loading = $state(true);
  let chosen = $state<Chosen>(null);
  let mode = $state<"view" | "edit" | "new">("view");

  // Looking at an aisle
  let members = $state<AisleFile[]>([]);
  let near = $state<AisleFile[]>([]);
  let excluded = $state<AisleFile[]>([]);
  let fetching = $state(false);
  let showLeftOut = $state(false);
  let confirmDelete = $state(false);
  let working = $state(false);
  let unseen = $state(new Set<string>());
  let toast = $state<{ id: number; text: string; undo?: () => Promise<void> } | null>(null);
  // An aisle's cards a screenful at a time: an automatic one can hold thousands of files, and making every card at
  // once froze the page (22 long tasks opening the aisles of 10,700 files). More come as the end comes into view.
  const PAGE = 120;
  let shownCount = $state(PAGE);
  let more = $state<HTMLElement | null>(null);
  $effect(() => {
    if (!more) return;
    const watcher = new IntersectionObserver((seen) => { if (seen.some((e) => e.isIntersecting)) shownCount += PAGE; },
                                             { rootMargin: "600px" });
    watcher.observe(more);
    return () => watcher.disconnect();
  });
  let toastId = 0;

  // Making or editing one
  let draft = $state<AisleDefinition>({ name: "", about: "", pinned: [], excluded: [], kinds: [] });
  let preview = $state<{ count: number; members: AisleFile[] } | null>(null);
  let previewing = $state(false);
  let needsName = $state(false);
  let showKinds = $state(false);
  let kindsChosen = $state(false);   // you picked kinds yourself: your words don't change them any more
  let nameField = $state<HTMLInputElement | null>(null);
  let controller: AbortController | null = null;
  let timer = 0;

  const aisle = $derived(chosen?.kind === "mine" ? mine.find((a) => a.id === chosen!.id) ?? null : null);
  const idea = $derived(chosen?.kind === "idea" ? ideas.find((s) => s.id === chosen!.id) ?? null : null);

  async function load(): Promise<void> {
    try {
      const listing = await api.aisles();
      mine = listing.aisles;
      ideas = listing.suggestions;
      grouped = listing.auto;
    } catch (error) { dowse.error = String(error); }
    loading = false;
  }

  onMount(() => {
    load().then(() => {
      const wanted = dowse.params.get("id");
      const first = mine.find((a) => a.id === wanted) ?? mine[0];
      if (first) look({ kind: "mine", id: first.id });
      else startNew();
    });
  });

  // Files read, changed or switched off, or aisles changed elsewhere: the lists follow (an edit in progress is left be)
  let seenVersion = dowse.mapVersion;
  $effect(() => {
    const version = dowse.mapVersion;
    untrack(() => {
      if (version === seenVersion) return;
      seenVersion = version;
      load().then(() => { if (mode === "view" && chosen && !working) fetchMembers(); });
    });
  });

    // An aisle named in the address (a link from elsewhere) is the one shown
  $effect(() => {
    const wanted = dowse.params.get("id");
    untrack(() => {
      if (wanted && !(chosen?.kind === "mine" && chosen.id === wanted) && mine.some((a) => a.id === wanted)) {
        look({ kind: "mine", id: wanted });
      }
    });
  });

  // Looking

  async function look(next: Chosen): Promise<void> {
    chosen = next;
    mode = "view";
    shownCount = PAGE;
    confirmDelete = false;
    showLeftOut = false;
    await fetchMembers();
  }

  async function fetchMembers(): Promise<void> {
    if (!chosen) return;
    const asked = chosen.id;
    fetching = true;
    try {
      const answer = await api.aisleMembers(asked);
      if (chosen?.id !== asked) return;
      members = answer.members; near = chosen.kind === "mine" ? answer.near : []; excluded = answer.excluded;
    } catch (error) { dowse.error = String(error); }
    finally { fetching = false; }
  }

  function definition(of: MyAisle): AisleDefinition {
    return { id: of.id, name: of.name, about: of.about, pinned: [...of.pinned], excluded: [...of.excluded], kinds: [...(of.kinds ?? [])] };
  }

  function tell(text: string, undo?: () => Promise<void>): void { toast = { id: ++toastId, text, undo }; }

  /** A change to the aisle shown, saved at once, with Undo. */
  async function change(text: string, edit: (d: AisleDefinition) => void): Promise<void> {
    if (!aisle || working) return;
    const before = definition(aisle), after = definition(aisle);
    edit(after);
    working = true;
    try {
      await api.saveAisle(after);
      await load();
      await fetchMembers();
      tell(text, async () => { await api.saveAisle(before); await load(); await fetchMembers(); });
    } catch (error) { dowse.error = String(error); }
    finally { working = false; }
  }

  const takeOut = (file: AisleFile) => change(`${file.name} is out of ${aisle?.name}`, (d) => {
    d.pinned = d.pinned.filter((p) => p !== file.path);
    if (!d.excluded.includes(file.path)) d.excluded = [...d.excluded, file.path];
  });
  const addIn = (file: AisleFile) => change(`${file.name} is in ${aisle?.name}`, (d) => {
    d.excluded = d.excluded.filter((p) => p !== file.path);
    if (!d.pinned.includes(file.path)) d.pinned = [...d.pinned, file.path];
  });
  const putBack = (file: AisleFile) => change(`${file.name} can be in ${aisle?.name} again`, (d) => {
    d.excluded = d.excluded.filter((p) => p !== file.path);
  });

  async function remove(): Promise<void> {
    if (!aisle) return;
    const gone = definition(aisle);
    await api.deleteAisle(gone.id!);
    await load();
    tell(`Deleted “${gone.name}”. Its files are still on your PC.`, async () => {
      await api.saveAisle(gone); await load(); await look({ kind: "mine", id: gone.id! });
    });
    if (mine[0]) await look({ kind: "mine", id: mine[0].id }); else startNew();
  }

  async function keepIdea(s: SuggestedAisle): Promise<void> {
    const made = await api.saveAisle({ name: s.label, about: s.about, pinned: [], excluded: [], kinds: [] });
    await load();
    await look({ kind: "mine", id: made.id });
    tell(`“${made.name}” is one of your aisles now. Edit renames it.`);
  }

  async function dismissIdea(s: SuggestedAisle): Promise<void> {
    await api.dismissAisle(s.label);
    await load();
    tell(`Dowse won't suggest “${s.label}” again.`);
    if (mine[0]) await look({ kind: "mine", id: mine[0].id }); else startNew();
  }

  async function setGrouped(on: boolean): Promise<void> {
    grouped = on;
    await api.aislesAuto(on);
    await load();
  }

  // Making and editing

  function startNew(from?: { name: string; about: string }): void {
    chosen = null;
    mode = "new";
    draft = { name: from?.name ?? "", about: from?.about ?? "", pinned: [], excluded: [], kinds: [] };
    kindsChosen = false; showKinds = false; needsName = false;
    kindsFromWords();
    preview = null;
    schedule(0);
    setTimeout(() => nameField?.focus());
  }

  function startEdit(): void {
    if (!aisle) return;
    mode = "edit";
    draft = definition(aisle);
    kindsChosen = true; showKinds = draft.kinds.length > 0; needsName = false;
    preview = null;
    schedule(0);
    setTimeout(() => nameField?.focus());
  }

  function cancel(): void {
    clearTimeout(timer); controller?.abort();
    if (mode === "edit" && aisle) mode = "view";
    else if (mine[0]) look({ kind: "mine", id: mine[0].id });
  }

  // What the aisle would hold, as you type: asked again a moment after the last change
  function schedule(delay = 450): void {
    clearTimeout(timer);
    if (!draft.name.trim() && !draft.about.trim()) { preview = null; return; }
    timer = window.setTimeout(async () => {
      controller?.abort();
      const now = new AbortController();
      controller = now;
      previewing = true;
      try {
        const asked = $state.snapshot(draft);   // no name yet: the description alone says what it holds
        if (!asked.name.trim()) asked.name = asked.about.trim().slice(0, 80);
        const answer = await api.aislePreview(asked, now.signal);
        if (!now.signal.aborted) preview = answer;
      } catch (error) {
        if ((error as Error).name !== "AbortError") dowse.error = String(error);
      } finally {
        if (controller === now) previewing = false;
      }
    }, delay);
  }

  // Kinds your words name ("pictures of people": pictures and videos), chosen for you until you choose
  const NAMED: [RegExp, Kind[]][] = [
    [/\b(pictures?|photos?|images?|portraits?|selfies?)\b/i, ["image", "screenshot", "video"]],
    [/\b(videos?|recordings?|clips?|movies?|footage)\b/i, ["video"]],
    [/\bscreenshots?\b/i, ["screenshot"]],
    [/\bpdfs?\b/i, ["pdf"]],
    [/\b(documents?|letters?|e-?mails?|notes|contracts?|invoices?)\b/i, ["pdf", "doc"]],
    [/\b(audio|sounds?|podcasts?|songs?|music|voice memos?)\b/i, ["audio"]],
  ];
  function kindsFromWords(): void {
    if (kindsChosen) return;
    const words = `${draft.name} ${draft.about}`;
    const named = new Set<Kind>(NAMED.filter(([pattern]) => pattern.test(words)).flatMap(([, kinds]) => kinds));
    draft.kinds = KINDS.filter((k) => named.has(k));
  }
  function toggleKind(kind: Kind | null): void {
    kindsChosen = true;
    draft.kinds = kind === null ? [] : draft.kinds.includes(kind) ? draft.kinds.filter((k) => k !== kind) : [...draft.kinds, kind];
    schedule(0);
  }
  const kindsText = (kinds: Kind[]) => kinds.length ? kinds.map((k) => KIND_PLURAL[k]).join(", ") : "All kinds of files";

  async function saveDraft(): Promise<void> {
    if (!draft.name.trim()) { needsName = true; nameField?.focus(); return; }
    if (!draft.about.trim()) draft.about = draft.name;
    working = true;
    try {
      const made = await api.saveAisle($state.snapshot(draft));
      const fresh = mode === "new";
      await load();
      await look({ kind: "mine", id: made.id });
      tell(fresh ? `“${made.name}” is ready. Explore shows it too.` : "Saved.");
    } catch (error) { dowse.error = String(error); }
    finally { working = false; }
  }

  const fileCount = (n: number) => `${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
</script>

{#snippet thumb(file: AisleFile)}
  <span class="picture">
    <Icon name={ICON[file.kind ?? ""] ?? "doc"} size={26} />
    {#if file.visual && !unseen.has(file.path)}
      <img src={pictureUrl(file.path, null, 320)} alt="" loading="lazy" onerror={() => (unseen = new Set(unseen).add(file.path))} />
    {/if}
  </span>
{/snippet}

{#snippet card(file: AisleFile, action: { label: string; icon: IconName; run: () => void; strong?: boolean } | null)}
  <li class="card" title={`${file.name}\n${file.folder}`}>
    <button class="look" ondblclick={() => api.open(file.path, null)} aria-label="{file.name}, double-click to open">
      {@render thumb(file)}
      <span class="caption"><b>{file.name}</b><small>{file.folder}</small></span>
    </button>
    {#if file.pinned}<span class="badge" title="You added it: it stays whatever the description says"><Icon name="pin" size={12} />Added by you</span>{/if}
    {#if action}
      <button class="act" class:strong={action.strong} onclick={action.run} disabled={working} aria-label="{action.label}: {file.name}" title={action.label}>
        <Icon name={action.icon} size={14} /><span>{action.label}</span>
      </button>
    {/if}
  </li>
{/snippet}

<section class="aisles">
  <aside class="shelf-list">
    <h1 class="sign">Aisles</h1>
    <button class="new" class:on={mode === "new"} onclick={() => startNew()}><Icon name="add" size={18} />New aisle</button>

    {#if mine.length}
      <h2 class="group">Your aisles</h2>
      <ul>
        {#each mine as a (a.id)}
          <li>
            <button class="item" class:on={chosen?.kind === "mine" && chosen.id === a.id && mode !== "new"}
                    onclick={() => look({ kind: "mine", id: a.id })}>
              <Icon name="aisles" size={18} /><span class="label">{a.name}</span><span class="count data">{a.count}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    {#if ideas.length}
      <h2 class="group"><Icon name="sparkle" size={14} />Ideas from Dowse</h2>
      <p class="small">Files Dowse found alike. Keep the ones you like.</p>
      <ul>
        {#each ideas as s (s.id)}
          <li class="idea-row">
            <button class="item" class:on={chosen?.kind === "idea" && chosen.id === s.id}
                    onclick={() => look({ kind: "idea", id: s.id })}>
              <span class="label">{s.label}</span><span class="count data">{s.count}</span>
            </button>
            <button class="keep" onclick={() => keepIdea(s)} title="Keep as an aisle" aria-label="Keep {s.label} as an aisle"><Icon name="add" size={16} /></button>
          </li>
        {/each}
      </ul>
    {/if}

    <div class="auto">
      <span><b>Group the other files</b><small>{grouped ? "Files outside your aisles are grouped by Dowse (the ideas above)." : "Files outside your aisles are all in “Everything else”."}</small></span>
      <Toggle on={grouped} label="Group the other files" onchange={setGrouped} />
    </div>
  </aside>

  <div class="bench">
    {#if mode === "view" && aisle}
      <header class="title">
        <div class="heading">
          <h2 class="sign">{aisle.name}</h2>
          <p class="facts">
            {#if aisle.about.trim().toLowerCase() !== aisle.name.trim().toLowerCase()}<span>“{aisle.about}”</span><span class="dot">·</span>{/if}
            <span>{kindsText(aisle.kinds ?? [])}</span><span class="dot">·</span><span class="data">{fileCount(aisle.count)}</span>
          </p>
        </div>
        <div class="tools">
          {#if confirmDelete}
            <span class="confirm">Delete this aisle? Your files stay where they are.</span>
            <button class="plain" onclick={() => (confirmDelete = false)}>Cancel</button>
            <button class="plain danger" onclick={remove}>Delete</button>
          {:else}
            <button class="plain" onclick={startEdit}><Icon name="edit" size={16} />Edit</button>
            <button class="plain" onclick={() => (confirmDelete = true)}><Icon name="trash" size={16} />Delete</button>
          {/if}
        </div>
      </header>

      <div class="body" aria-busy={fetching}>
        {#if members.length}
          <ul class="grid">
            {#each members.slice(0, shownCount) as file (file.path)}{@render card(file, { label: "Take out", icon: "dismiss", run: () => takeOut(file) })}{/each}
          </ul>
          {#if members.length > shownCount}<div class="more-coming" bind:this={more}>{(members.length - shownCount).toLocaleString()} more…</div>{/if}
        {:else if !fetching}
          <div class="empty-note">
            <b>Nothing in it yet</b>
            <p>Dowse puts files here when they match “{aisle.about}”. Try other words under Edit, or add files below.</p>
          </div>
        {/if}

        {#if near.length}
          <section class="more">
            <h3 class="group">Might also belong <span class="count data">{near.length}</span></h3>
            <ul class="grid small-grid">
              {#each near as file (file.path)}{@render card(file, { label: "Add", icon: "add", run: () => addIn(file), strong: true })}{/each}
            </ul>
          </section>
        {/if}

        {#if excluded.length}
          <section class="more">
            <button class="disclose" onclick={() => (showLeftOut = !showLeftOut)} aria-expanded={showLeftOut}>
              <Icon name="chevron" size={14} />{excluded.length} taken out by you
            </button>
            {#if showLeftOut}
              <ul class="grid small-grid">
                {#each excluded as file (file.path)}{@render card(file, { label: "Put back", icon: "again", run: () => putBack(file) })}{/each}
              </ul>
            {/if}
          </section>
        {/if}
      </div>

    {:else if mode === "view" && idea}
      <header class="title">
        <div class="heading">
          <p class="kicker"><Icon name="sparkle" size={14} />An idea from Dowse</p>
          <h2 class="sign">{idea.label}</h2>
          <p class="facts"><span class="data">{fileCount(idea.count)}</span><span class="dot">·</span><span>found alike, whatever their folder</span></p>
        </div>
        <div class="tools">
          <button class="plain" onclick={() => dismissIdea(idea)}>Not interested</button>
          <button class="plain" onclick={() => startNew({ name: "", about: idea.about })}><Icon name="edit" size={16} />Name it myself</button>
          <button class="primary" onclick={() => keepIdea(idea)}><Icon name="add" size={16} />Keep as an aisle</button>
        </div>
      </header>
      <div class="body" aria-busy={fetching}>
        <ul class="grid">
          {#each members.slice(0, shownCount) as file (file.path)}{@render card(file, null)}{/each}
        </ul>
        {#if members.length > shownCount}<div class="more-coming" bind:this={more}>{(members.length - shownCount).toLocaleString()} more…</div>{/if}
      </div>

    {:else if mode === "new" || mode === "edit"}
      <div class="form-wrap">
        <form class="form" onsubmit={(event) => { event.preventDefault(); saveDraft(); }}>
          <h2 class="sign">{mode === "new" ? "New aisle" : `Edit “${aisle?.name}”`}</h2>
          {#if mode === "new"}<p class="intro">An aisle is a shelf for files about one thing, from any folder. Say what goes in it; Dowse finds the files and keeps it up to date.</p>{/if}

          <label class="field">
            <span class="field-label">Name</span>
            <input bind:this={nameField} bind:value={draft.name} placeholder="Bills & receipts" maxlength="80" spellcheck="false"
                   oninput={() => { needsName = false; kindsFromWords(); schedule(); }} aria-invalid={needsName} />
            {#if needsName}<span class="needed" role="alert">Give it a name</span>{/if}
          </label>

          <label class="field">
            <span class="field-label">What goes in it?</span>
            <input bind:value={draft.about} placeholder="receipts, invoices and bills, anything I paid for" maxlength="400" spellcheck="false"
                   oninput={() => { kindsFromWords(); schedule(); }} />
            <span class="hint">In your own words, about what's in the files. Dowse understands the meaning, in any language.</span>
          </label>

          {#if mode === "new" && !draft.name.trim() && !draft.about.trim()}
            <div class="examples" role="group" aria-label="Ideas to start from">
              <span class="hint">Or start from</span>
              {#each EXAMPLES as example (example.name)}
                <button type="button" class="example" onclick={() => startNew(example)}>{example.name}</button>
              {/each}
            </div>
          {/if}

          <div class="kinds-line">
            {#if showKinds}
              <span class="field-label">Kinds of files</span>
              <div class="drawers" role="group" aria-label="Kinds of files">
                <button type="button" class:on={!draft.kinds.length} onclick={() => toggleKind(null)}>All</button>
                {#each KINDS as kind (kind)}
                  <button type="button" class:on={draft.kinds.includes(kind)} aria-pressed={draft.kinds.includes(kind)}
                          onclick={() => toggleKind(kind)}>{KIND_PLURAL[kind]}</button>
                {/each}
              </div>
            {:else}
              <span class="kinds-now">{kindsText(draft.kinds)}{#if draft.kinds.length && !kindsChosen}<em> (from your words)</em>{/if}</span>
              <button type="button" class="link" onclick={() => (showKinds = true)}>Change</button>
            {/if}
          </div>

          <div class="form-actions">
            <button type="button" class="plain" onclick={cancel}>Cancel</button>
            <button type="submit" class="primary" disabled={working}>{mode === "new" ? "Create aisle" : "Save changes"}</button>
          </div>
        </form>

        <section class="would">
          <h3 class="group">
            {#if !draft.name.trim() && !draft.about.trim()}What it would hold shows here as you type
            {:else if preview}{fileCount(preview.count)} would be in it
            {:else}Looking…{/if}
            {#if previewing}<span class="busy" aria-label="Updating"></span>{/if}
          </h3>
          {#if preview?.members.length}
            <ul class="grid small-grid">
              {#each preview.members as file (file.path)}{@render card(file, null)}{/each}
            </ul>
          {:else if preview && !previewing}
            <p class="hint">No file matches yet. Try saying it another way, or more broadly.</p>
          {/if}
        </section>
      </div>

    {:else if !loading}
      <div class="empty">
        <h2 class="sign">No aisles yet</h2>
        <p>An aisle is a shelf for files about one thing, from any folder: bills, a trip, your cat.</p>
        <button class="primary" onclick={() => startNew()}><Icon name="add" size={16} />New aisle</button>
      </div>
    {/if}
  </div>
</section>

{#if toast}
  {#key toast.id}
    <Toast text={toast.text} action={toast.undo ? "Undo" : undefined}
           onaction={async () => { const undo = toast?.undo; toast = null; await undo?.(); }}
           onclose={() => (toast = null)} />
  {/key}
{/if}

<style>
  .aisles { display: grid; grid-template-columns: 260px minmax(0, 1fr); height: 100%; min-height: 0; }

  /* The list of aisles */
  .shelf-list { display: flex; flex-direction: column; gap: var(--s-1); padding: var(--s-5) var(--s-3); border-right: 1px solid var(--hairline); min-height: 0; overflow-y: auto; }
  .shelf-list h1 { margin: 0 var(--s-3) var(--s-3); font-size: var(--text-xl); }
  .new {
    display: flex; align-items: center; justify-content: center; gap: 6px; margin: 0 var(--s-2) var(--s-2); padding: 8px 12px;
    border-radius: var(--r-field); background: var(--steel); color: var(--steel-ink); font-weight: 600; font-size: var(--text-sm);
  }
  .new:hover, .new.on { background: var(--steel-raised); }
  .group {
    display: flex; align-items: center; gap: 6px; margin: var(--s-4) var(--s-3) var(--s-2);
    font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--ink-3);
  }
  .small { margin: -4px var(--s-3) var(--s-2); color: var(--ink-3); font-size: var(--text-xs); line-height: 1.4; }
  .shelf-list ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; }
  .item {
    width: 100%; display: flex; align-items: center; gap: var(--s-3); padding: 8px var(--s-3);
    border-radius: var(--r-steel); text-align: left; color: var(--ink-2); font-size: var(--text-sm);
  }
  .item:hover { background: var(--floor-sunk); color: var(--ink); }
  .item.on { background: var(--floor-raised); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hairline), var(--lift-1); }
  .item.on :global(.icon) { color: var(--found-ink); }
  .idea-row { display: flex; align-items: center; gap: 2px; }
  .idea-row .item { flex: 1; min-width: 0; }
  .keep { display: grid; place-items: center; flex: none; width: 30px; height: 30px; border-radius: var(--r-steel); color: var(--ink-3); }
  .keep:hover { background: var(--found-wash); color: var(--found-ink); }
  .label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .count { color: var(--ink-3); font-size: var(--text-xs); }
  .auto {
    display: flex; align-items: center; justify-content: space-between; gap: var(--s-3); margin: auto var(--s-3) 0;
    padding-top: var(--s-4); border-top: 1px solid var(--hairline); font-size: var(--text-sm); color: var(--ink);
  }
  .auto b { font-weight: 600; }
  .auto small { display: block; margin-top: 2px; color: var(--ink-3); font-size: var(--text-xs); line-height: 1.35; }

  /* An aisle */
  .bench { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 0; }
  .title { display: flex; align-items: flex-end; justify-content: space-between; flex-wrap: wrap; gap: var(--s-3) var(--s-5);
           padding: var(--s-5) var(--s-6) var(--s-4); border-bottom: 1px solid var(--hairline); }
  .heading { min-width: 0; }
  .heading h2 { margin: 0; font-size: var(--text-2xl); line-height: 1.1; }
  .kicker { display: flex; align-items: center; gap: 6px; margin: 0 0 4px; color: var(--found-ink); font-size: var(--text-sm); font-weight: 600; }
  .facts { display: flex; flex-wrap: wrap; gap: 4px 8px; margin: 6px 0 0; color: var(--ink-2); font-size: var(--text-sm); }
  .dot { color: var(--ink-3); }
  .tools { display: flex; align-items: center; flex-wrap: wrap; gap: var(--s-2); }
  .confirm { color: var(--ink-2); font-size: var(--text-sm); }

  .body { overflow-y: auto; min-height: 0; padding: var(--s-5) var(--s-6) var(--s-6); }
  .grid { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-4); grid-template-columns: repeat(auto-fill, minmax(156px, 1fr)); }
  .small-grid { gap: var(--s-3); grid-template-columns: repeat(auto-fill, minmax(124px, 1fr)); }
  .card { position: relative; min-width: 0; }
  .look { display: grid; gap: 6px; width: 100%; padding: 0; text-align: left; border-radius: var(--r-field); }
  .look:focus-visible { outline: 2px solid var(--focus); outline-offset: 3px; }
  .picture {
    position: relative; display: grid; place-items: center; aspect-ratio: 4 / 3; border-radius: var(--r-field); overflow: hidden;
    background: var(--floor-sunk); color: var(--ink-3); box-shadow: inset 0 0 0 1px var(--glass-shade);
    transition: box-shadow var(--t-rail) var(--rail), transform var(--t-rail) var(--rail);
  }
  .picture img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .card:hover .picture { box-shadow: inset 0 0 0 1px var(--glass-shade), var(--lift-2); }
  .caption { display: grid; min-width: 0; padding: 0 2px; }
  .caption b { font-size: var(--text-sm); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .caption small { color: var(--ink-3); font-size: var(--text-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge {
    position: absolute; left: 6px; top: 6px; display: inline-flex; align-items: center; gap: 3px; padding: 2px 7px;
    border-radius: 999px; background: var(--floor-raised); color: var(--found-ink); font-size: 11px; font-weight: 600;
    box-shadow: var(--lift-1); pointer-events: none;
  }
  /* The one thing to do with a file, on hover or focus: always there for touch and keyboard, quiet until wanted */
  .act {
    position: absolute; right: 6px; top: 6px; display: inline-flex; align-items: center; gap: 4px; padding: 4px 9px;
    border-radius: 999px; background: var(--floor-raised); color: var(--ink); font-size: var(--text-xs); font-weight: 600;
    box-shadow: var(--lift-2); opacity: 0; transition: opacity var(--t-rail) var(--rail);
  }
  .act.strong { opacity: 1; background: var(--steel); color: var(--steel-ink); }
  .card:hover .act, .card:focus-within .act { opacity: 1; }
  .act:hover:not(.strong) { background: var(--floor-sunk); }
  .act.strong:hover { background: var(--steel-raised); }
  @media (hover: none) { .act { opacity: 1; } }

  .more { margin-top: var(--s-6); }
  .more-coming { padding: var(--s-5) 0 var(--s-2); text-align: center; color: var(--ink-3); font-size: var(--text-sm); }
  .more .group { margin-left: 0; }
  .disclose { display: inline-flex; align-items: center; gap: 6px; margin-bottom: var(--s-3); color: var(--ink-2); font-size: var(--text-sm); }
  .disclose[aria-expanded="false"] :global(.icon) { rotate: -90deg; }
  .disclose:hover { color: var(--ink); }
  .empty-note { display: grid; gap: 4px; max-width: 56ch; padding: var(--s-5); border: 1px dashed var(--hairline); border-radius: var(--r-field); }
  .empty-note p { margin: 0; color: var(--ink-2); font-size: var(--text-sm); }

  /* Making one */
  .form-wrap { grid-row: 1 / -1; overflow-y: auto; min-height: 0; padding: var(--s-5) var(--s-6) var(--s-6); }
  .form { display: grid; gap: var(--s-4); max-width: 640px; }
  .form h2 { margin: 0; font-size: var(--text-2xl); }
  .intro { margin: -6px 0 0; color: var(--ink-2); font-size: var(--text-sm); line-height: 1.5; max-width: 60ch; }
  .field { display: grid; gap: 6px; }
  .field-label { font-size: var(--text-sm); font-weight: 600; color: var(--ink); }
  .field input {
    width: 100%; height: 40px; padding: 0 12px; border: 1px solid var(--hairline); border-radius: var(--r-field);
    background: var(--floor-raised); color: var(--ink); font: inherit; font-size: var(--text-md, 15px); outline: none; user-select: text;
  }
  .field input:focus { border-color: var(--found); box-shadow: 0 0 0 3px var(--found-wash); }
  .field input::placeholder { color: var(--ink-3); }
  .field input[aria-invalid="true"] { border-color: var(--warn); }
  .needed { color: var(--warn); font-size: var(--text-sm); }
  .hint { color: var(--ink-3); font-size: var(--text-xs); line-height: 1.45; }
  .examples { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  .example { padding: 4px 11px; border-radius: 999px; border: 1px solid var(--hairline); background: var(--floor-raised); color: var(--ink-2); font-size: var(--text-sm); }
  .example:hover { background: var(--found-wash); color: var(--found-ink); border-color: transparent; }
  .kinds-line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-2) var(--s-3); font-size: var(--text-sm); color: var(--ink-2); }
  .kinds-line .field-label { flex-basis: 100%; }
  .kinds-now em { color: var(--found-ink); font-style: normal; }
  .link { color: var(--found-ink); text-decoration: underline; font-size: var(--text-sm); }
  .drawers { display: flex; flex-wrap: wrap; gap: 2px; }
  .drawers button { padding: 5px 11px; border-radius: var(--r-steel); font: 500 var(--text-sm) / 1.2 var(--face-ui); color: var(--ink-2); }
  .drawers button:hover { background: var(--floor-sunk); color: var(--ink); }
  .drawers button.on { background: var(--steel); color: var(--steel-ink); }
  .form-actions { display: flex; gap: var(--s-2); justify-content: flex-start; flex-direction: row-reverse; justify-self: start; }
  .would { margin-top: var(--s-6); padding-top: var(--s-4); border-top: 1px solid var(--hairline); }
  .would .group { margin-left: 0; }
  .busy { width: 6px; height: 6px; border-radius: 50%; background: var(--found); animation: breathe 1.2s var(--glide) infinite alternate; }
  @keyframes breathe { to { opacity: 0.25; } }

  .plain { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 12px; border-radius: var(--r-field);
           border: 1px solid var(--hairline); background: var(--floor-raised); font-size: var(--text-sm); font-weight: 500; color: var(--ink); }
  .plain:hover { background: var(--floor-sunk); }
  .plain.danger { border-color: color-mix(in srgb, var(--warn) 55%, var(--hairline)); color: var(--warn-ink, var(--ink)); }
  .primary { display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 16px; border-radius: var(--r-field);
             background: var(--steel); color: var(--steel-ink); font-weight: 600; font-size: var(--text-sm); }
  .primary:hover:not(:disabled) { background: var(--steel-raised); }
  .primary:disabled { opacity: 0.5; cursor: default; }

  .empty { grid-row: 1 / -1; display: grid; place-content: center; justify-items: center; gap: var(--s-3); padding: var(--s-6); text-align: center; }
  .empty h2 { margin: 0; font-size: var(--text-xl); }
  .empty p { margin: 0; max-width: 48ch; color: var(--ink-2); }

  @media (prefers-reduced-motion: reduce) { .busy { animation: none; } }
</style>
