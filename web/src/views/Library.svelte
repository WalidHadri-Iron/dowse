<script lang="ts">
  // The Library: what Dowse searches, file by file. Places on the left (everything, each folder, the files added one
  // by one, what was removed, what needs attention), the files of the chosen one as dense rows with their pictures,
  // and the viewing table: any file previewed where it lies, played, read. Add, remove (with Undo), read again.

  import { untrack } from "svelte";
  import { api, pictureUrl, type Entry } from "../lib/api";
  import { primary } from "../lib/keys";
  import { KIND_NAME, dowse, size, when } from "../lib/app.svelte";
  import { fileManager, hasFileMenu } from "../lib/system";
  import { search } from "../lib/search.svelte";
  import type { IconName } from "../lib/icons";
  import Icon from "../components/Icon.svelte";
  import ViewingTable from "../components/ViewingTable.svelte";
  import Toggle from "../components/Toggle.svelte";
  import { inactiveBy, pathKey } from "../lib/selection-state";

  const ALL = "all", NAMED = "named", REMOVED = "removed", ATTENTION = "attention", FOLDERS = "folders", INACTIVE = "inactive";
  const ROW = 36;

  let view = $state(decodeURIComponent(dowse.path.split("/")[2] ?? "") || ALL);
  let preselected = dowse.params.get("file");
  let unseen = $state(new Set<string>());   // files with nothing to show (a black recording): their kind instead   // a file to show at once (a link from elsewhere, or a review)

  // The address leads when it changes from outside (the rail, a link, the tray's Library)
  $effect(() => {
    const wanted = decodeURIComponent(dowse.path.split("/")[2] ?? "") || ALL, file = dowse.params.get("file");
    untrack(() => {
      if (file) { preselected = file; if (entries.some((e) => e.path === file)) { current = file; selected = new Set([file]); } }
      if (wanted !== view && dowse.path.startsWith("/library")) view = wanted;
    });
  });
  let entries = $state<Entry[]>([]);
  let counts = $state<Record<string, number>>({});
  let loading = $state(true);
  let filter = $state("");
  let sort = $state<{ by: "name" | "folder" | "kind" | "size" | "mtime" | "status"; up: boolean }>({ by: "name", up: true });
  let selected = $state<Set<string>>(new Set());
  let current = $state<string | null>(null);
  let busy = $state(false);
  let removal = $state<string[] | null>(null);
  let stateFilter = $state("all");
  let loadVersion = 0;
  let scroller = $state<HTMLDivElement | null>(null);
  let scrollTop = $state(0);
  let viewport = $state(600);

  const places = $derived([
    // The folders themselves are in Dowsed folders (one click away), not listed here: the panel stays short however
    // many there are.
    { view: FOLDERS, label: "Dowsed folders", icon: "folder" as IconName },
    { view: NAMED, label: "Dowsed files", icon: "pin" as IconName },   // added one by one, from anywhere
    { view: ALL, label: "All files", icon: "layers" as IconName },
    { view: INACTIVE, label: "Inactive", icon: "pause" as IconName },
    { view: REMOVED, label: "Removed", icon: "skip" as IconName },
    { view: ATTENTION, label: "Needs attention", icon: "warning" as IconName },
  ]);

  const ABOUT: Record<string, string> = {
    [ALL]: "Everything Dowse searches, from your folders and added one by one, including inactive files. Turn search on or off without deleting anything.",
    [NAMED]: "Files you added one by one, from anywhere. The rules for folders (hidden files, code projects) don't apply to them.",
    [REMOVED]: "What you took out. Dowse never reads these, even inside the folders it searches.",
    [ATTENTION]: "Files Dowse couldn't read, or won't search, and why.",
    [FOLDERS]: "Manage your folders here. Inactive folders keep their indexed data; removal never deletes originals.",
    [INACTIVE]: "Paused files and folders. Activate them to include them in search again; their indexed data is kept.",
  };

  async function load(): Promise<void> {
    const asked = view;
    const version = ++loadVersion;
    try {
      const answer = await api.library(asked);
      if (asked !== view || version !== loadVersion) return;
      entries = answer.entries;
      counts = answer.counts;
      selected = new Set([...selected].filter((path) => entries.some((e) => e.path === path)));
      if (current && !entries.some((e) => e.path === current)) current = null;
      if (preselected && entries.some((e) => e.path === preselected)) {
        current = preselected; selected = new Set([preselected]); preselected = null;
      }
    } catch (error) { dowse.error = String(error); } finally {
      loading = false;
    }
  }

  $effect(() => { void view; untrack(() => { loading = true; entries = []; selected = new Set(); current = null; removal = null; stateFilter = "all"; load(); }); });
  $effect(() => { void dowse.indexVersion; untrack(() => load()); });
  $effect(() => { history.replaceState(null, "", `#/library/${encodeURIComponent(view)}`); });

  const shown = $derived.by(() => {
    const words = filter.trim().toLowerCase();
    const rows = entries.filter((e) => (!words || e.name.toLowerCase().includes(words))
      && (stateFilter === "all" || e.active === (stateFilter === "active")));
    const key = sort.by;
    const value = (e: Entry): string | number =>
      key === "size" ? (e.size ?? -1) : key === "mtime" ? (e.mtime ?? 0) : key === "kind" ? (e.kind ?? "") :
      key === "folder" ? e.folder.toLowerCase() : key === "status" ? e.statusText : e.name.toLowerCase();
    rows.sort((a, b) => {
      const x = value(a), y = value(b);
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.up ? 1 : -1);
    });
    return rows;
  });

  // Only the rows in view are drawn: a hundred thousand files scroll like twenty.
  const first = $derived(Math.max(0, Math.floor(scrollTop / ROW) - 6));
  const last = $derived(Math.min(shown.length, Math.ceil((scrollTop + viewport) / ROW) + 6));

  const focused = $derived(shown.find((e) => e.path === current) ?? null);
  const viewed = $derived(focused && !focused.isFolder && focused.status !== "removed" ? {
    path: focused.path, name: focused.name, folder: focused.folder, kind: focused.kind, loc: null, place: null,
    excerpt: null, mtime: focused.mtime, size: focused.size, info: {}, segment: 0, visual: focused.visual, moments: [],
  } : null);

  function click(entry: Entry, event: MouseEvent): void {
    if (primary(event)) {
      const next = new Set(selected);
      next.has(entry.path) ? next.delete(entry.path) : next.add(entry.path);
      selected = next;
    } else if (event.shiftKey && current) {
      const a = shown.findIndex((e) => e.path === current), b = shown.findIndex((e) => e.path === entry.path);
      selected = new Set(shown.slice(Math.min(a, b), Math.max(a, b) + 1).map((e) => e.path));
    } else {
      selected = new Set([entry.path]);
    }
    current = entry.path;
  }

  function keys(event: KeyboardEvent): void {
    const index = shown.findIndex((e) => e.path === current);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = shown[Math.max(0, Math.min(shown.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next) {
        current = next.path;
        selected = event.shiftKey ? new Set([...selected, next.path]) : new Set([next.path]);
        const top = shown.indexOf(next) * ROW;
        if (scroller && (top < scroller.scrollTop || top + ROW > scroller.scrollTop + viewport)) scroller.scrollTop = top - viewport / 2;
      }
    } else if (event.key === "Delete" && view !== REMOVED) { event.preventDefault(); removal = [...chosen]; }
    else if (event.key === "Escape") { removal = null; }
    else if (event.key === "Enter" && focused) { focused.isFolder ? (view = focused.path) : api.open(focused.path, null); }
    else if (event.key.toLowerCase() === "a" && primary(event)) { event.preventDefault(); selected = new Set(shown.map((e) => e.path)); }
  }

  const chosen = $derived(selected.size ? [...selected] : current ? [current] : []);

  async function changeSearch(op: "activate" | "deactivate", paths = chosen): Promise<void> {
    if (!paths.length || busy) return;
    busy = true;
    try {
      const answer = await api.choose(op, { paths });
      if (answer.changed) dowse.tell(`${paths.length.toLocaleString()} ${paths.length === 1 ? "item" : "items"} ${op === "activate" ? "activated" : "deactivated"} for search.`, answer.before);
      if (Object.keys(answer.blocked ?? {}).length) dowse.tell("Some items are inside an inactive folder. Activate that folder first.", answer.before);
      await dowse.refresh(); await load();
    } catch (error) { dowse.error = String(error); }
    finally { busy = false; }
  }

  async function remove(): Promise<void> {
    const paths = removal;
    if (!paths?.length || busy) return;
    busy = true;
    try {
      const answer = await api.choose("remove", { paths });
      if (answer.changed) dowse.tell(`Removed ${paths.length.toLocaleString()} from Dowse. Originals stay on disk.`, answer.before);
      removal = null; selected = new Set();
      if (paths.includes(view)) view = FOLDERS;
      await dowse.refresh(); await load();
    } catch (error) { dowse.error = String(error); }
    finally { busy = false; }
  }

  async function add(paths: string[]): Promise<void> {
    if (!paths.length) return;
    busy = true;
    try {
    const answer = await api.choose("add", { paths });
    const refused = Object.entries(answer.refused ?? {});
    if (refused.length) {
      const [path, why] = refused[0];
      dowse.tell(`Can't search ${path.split(/[\\/]/).pop()}: ${why}.`, answer.changed ? answer.before : undefined);
    } else if (answer.changed) {
      dowse.tell("Added. Dowse reads new files in the background.", answer.before);
    } else {
      dowse.tell("Dowse already searches that, or it isn't available on disk.");
    }
    await dowse.refresh(); await load();
    } catch (error) { dowse.error = String(error); }
    finally { busy = false; }
  }

  async function readAgain(): Promise<void> {
    if (!chosen.length) return;
    try { await api.choose("readAgain", { paths: chosen });
      dowse.tell(`Reading ${chosen.length.toLocaleString()} again. They stay searchable meanwhile.`);
    } catch (error) { dowse.error = String(error); }
  }

  const COLUMNS: { by: typeof sort.by; label: string }[] = [
    { by: "name", label: "Name" }, { by: "folder", label: "Folder" }, { by: "size", label: "Size" }, { by: "mtime", label: "Modified" }, { by: "status", label: "Status" },
  ];

  const ICON: Record<string, IconName> = { image: "image", screenshot: "screenshot", pdf: "pdf", doc: "doc",
                                           audio: "audio", video: "video" };
  const isFolder = $derived(!(view in ABOUT));
  const title = $derived(view === REMOVED ? "Removed from Dowse" : places.find((p) => p.view === view)?.label
    ?? (view.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || view));
  const folderInactive = $derived(inactiveBy(view, dowse.state?.selection.inactive ?? []));
  const parentInactive = $derived(!!folderInactive && pathKey(folderInactive) !== pathKey(view));
  const chosenEntries = $derived(entries.filter((e) => chosen.includes(e.path)));

  function toggle(entry: Entry, on: boolean): void {
    changeSearch(on ? "activate" : "deactivate", [entry.path]);
  }
  function select(entry: Entry, on: boolean): void {
    const next = new Set(selected);
    on ? next.add(entry.path) : next.delete(entry.path);
    selected = next;
  }
</script>

<section class="library" class:previewing={!!viewed} class:in-folder={isFolder}>
  <aside class="places" aria-label="Places">
    <h1 class="sign">Library</h1>
    <ul>
      {#each places as place (place.view)}
        <li>
          <button class:on={view === place.view || (place.view === FOLDERS && isFolder)} onclick={() => (view = place.view)}
                  aria-current={view === place.view || (place.view === FOLDERS && isFolder) ? "page" : undefined}>
            <Icon name={place.icon} size={18} />
            <span class="label">{place.label}</span>
            {#if counts[place.view]}<span class="data count">{counts[place.view].toLocaleString()}</span>{/if}
          </button>
        </li>
      {/each}
    </ul>
    <div class="adders">
      <button onclick={async () => add((await api.pick("files")).paths)}><Icon name="add" size={18} />Add files</button>
      <button onclick={async () => add((await api.pick("folder")).paths)}><Icon name="folder" size={18} />Add a folder</button>
    </div>
  </aside>

  <div class="shelf">
    <header>
      <div class="heading">
        {#if isFolder}<button class="crumb" onclick={() => (view = FOLDERS)}><span aria-hidden="true">‹</span> Dowsed folders</button>{/if}
        <h2 class="sign">{title}</h2>
        <p class="about" title={isFolder ? view : ""}>{isFolder ? view : ABOUT[view]}</p>
      </div>
      <label class="filter">
        <Icon name="search" size={16} />
        <input bind:value={filter} placeholder="Filter by name" spellcheck="false" oninput={() => { selected = new Set(); current = null; }} />
      </label>
      {#if view !== REMOVED && view !== INACTIVE}
        <select class="state-filter" aria-label="Filter search activity" bind:value={stateFilter} onchange={() => { selected = new Set(); current = null; }}>
          <option value="all">Active and inactive</option><option value="active">Active only</option><option value="inactive">Inactive only</option>
        </select>
      {/if}
    </header>

    <div class="commands" role="toolbar" aria-label="What to do with the selected files">
      {#if view === REMOVED}
        <button disabled={busy || !chosen.length} onclick={() => add(chosen)} title="Add back" aria-label="Add back"><Icon name="again" size={18} /><span class="label">Add back</span></button>
      {:else}
        <button disabled={busy || !chosenEntries.some((e) => !e.active)} onclick={() => changeSearch("activate")} title="Activate selected items" aria-label="Activate selected items"><Icon name="play" size={18} /><span class="label">Activate</span></button>
        <button disabled={busy || !chosenEntries.some((e) => e.active)} onclick={() => changeSearch("deactivate")} title="Deactivate selected items" aria-label="Deactivate selected items"><Icon name="pause" size={18} /><span class="label">Deactivate</span></button>
        <button disabled={busy || !chosen.length} onclick={() => (removal = [...chosen])} title="Remove from Dowse" aria-label="Remove from Dowse"><Icon name="remove" size={18} /><span class="label">Remove…</span></button>
        {#if view !== FOLDERS && view !== INACTIVE}
          <button disabled={busy || !chosenEntries.some((e) => e.active && !e.isFolder)} onclick={readAgain} title="Read again" aria-label="Read again"><Icon name="again" size={18} /><span class="label">Read again</span></button>
        {/if}
      {/if}
      {#if focused?.isFolder && view !== REMOVED}<button onclick={() => focused && (view = focused.path)} title="View folder files" aria-label="View folder files"><Icon name="folder" size={18} /><span class="label">View files</span></button>{/if}
      <button disabled={!focused} onclick={() => focused && api.reveal(focused.path)} title="Show in folder" aria-label="Show in folder"><Icon name="folderOpen" size={18} /><span class="label">Show in folder</span></button>
      {#if isFolder}
        <span class="spacer"></span>
        <button onclick={() => dowse.go(`/search?in=${encodeURIComponent(view)}`)} title="Search in this folder" aria-label="Search in this folder"><Icon name="search" size={18} /><span class="label">Search in this folder</span></button>
        <button disabled={busy || parentInactive} onclick={() => changeSearch(folderInactive ? "activate" : "deactivate", [view])}
                title={folderInactive ? "Activate folder" : "Deactivate folder"} aria-label={folderInactive ? "Activate folder" : "Deactivate folder"}>
          <Icon name={folderInactive ? "play" : "pause"} size={18} /><span class="label">{folderInactive ? "Activate folder" : "Deactivate folder"}</span></button>
        <button disabled={busy} onclick={() => (removal = [view])} title="Remove folder from Dowse" aria-label="Remove folder from Dowse"><Icon name="remove" size={18} /><span class="label">Remove folder…</span></button>
      {/if}
      <span class="tally data">{selected.size > 1 ? `${selected.size.toLocaleString()} selected` : `${shown.length.toLocaleString()} ${shown.length === 1 ? "item" : "items"}`}</span>
    </div>

    {#if removal?.length}
      <div class="remove-confirm" role="group" aria-label="Confirm removal">
        <span><b>Remove {removal.length === 1 ? removal[0].split(/[\\/]/).pop() : `${removal.length} items`} from Dowse?</b>
          <small>They leave the search index, not your disk. Original folders and files are never deleted. You can add them back later.</small></span>
        <button class="yes" disabled={busy} onclick={remove}>Remove from Dowse</button><button disabled={busy} onclick={() => (removal = null)}>Cancel</button>
      </div>
    {/if}
    {#if isFolder && folderInactive}
      <div class="inactive-note" role="status">This folder is inactive. Its files are not used in search or Explore.
        {#if parentInactive}<button onclick={() => (view = folderInactive!)}>Manage the inactive parent folder</button>{/if}
      </div>
    {/if}

    <div class="table" role="grid" aria-rowcount={shown.length} tabindex="0" onkeydown={keys}>
      <div class="head" role="row">
        <input class="select-all" type="checkbox" aria-label="Select all visible items" checked={!!shown.length && shown.every((e) => selected.has(e.path))}
               onchange={(event) => (selected = event.currentTarget.checked ? new Set(shown.map((e) => e.path)) : new Set())} />
        {#each COLUMNS as column (column.by)}
          <button role="columnheader" class:on={sort.by === column.by}
                  onclick={() => (sort = { by: column.by, up: sort.by === column.by ? !sort.up : true })}>
            {column.label}{#if sort.by === column.by}<span class="arrow">{sort.up ? "↑" : "↓"}</span>{/if}
          </button>
        {/each}
        <span class="search-heading" role="columnheader">Search</span>
      </div>
      <div class="rows" bind:this={scroller} bind:clientHeight={viewport} onscroll={() => (scrollTop = scroller?.scrollTop ?? 0)}>
        {#if shown.length}
          <div style:height="{shown.length * ROW}px" style:position="relative">
            {#each shown.slice(first, last) as entry, i (entry.path)}
              <div class="row" role="row" aria-selected={selected.has(entry.path)} data-path={entry.path} style:top="{(first + i) * ROW}px"
                   class:selected={selected.has(entry.path)} class:current={entry.path === current} class:inactive={!entry.active}>
                <input class="select-row" type="checkbox" aria-label="Select {entry.name}" checked={selected.has(entry.path)} onchange={(event) => select(entry, event.currentTarget.checked)} />
                <button class="row-main" onclick={(event) => click(entry, event)} aria-label="Select {entry.name}"
                        ondblclick={() => entry.isFolder ? (view = entry.path) : entry.status !== "removed" && api.open(entry.path, null)}>
                <span class="name">
                  <span class="thumb">
                    {#if entry.visual && entry.status !== "removed" && !unseen.has(entry.path)}
                      <img src={pictureUrl(entry.path, null, 120)} alt="" loading="lazy"
                           onerror={() => (unseen = new Set(unseen).add(entry.path))} />
                    {:else}
                      <Icon name={entry.isFolder ? "folder" : ICON[entry.kind ?? ""] ?? "doc"} size={18} />
                    {/if}
                  </span>
                  <span class="text" title="{entry.isFolder ? 'Folder' : entry.kind ? KIND_NAME[entry.kind] : 'File'}: {entry.path}">{entry.name}</span>
                  {#if entry.named}<Icon name="pin" size={12} label="Added by you" />{/if}
                </span>
                <span class="muted">{entry.folder}</span>
                <span class="muted data num">{size(entry.size)}</span>
                <span class="muted">{when(entry.mtime)}</span>
                <span class="status s-{entry.status}" title={entry.statusText}><span class="dot"></span>{entry.statusText}</span>
                </button>
                <span class="activity">
                  {#if entry.status === "removed"}<span class="muted">Removed</span>
                  {:else if entry.inactiveBy && pathKey(entry.inactiveBy) !== pathKey(entry.path)}
                    <button class="parent-link" title="Activate the containing folder first" onclick={() => (view = entry.inactiveBy!)}>Folder off</button>
                  {:else}
                    <Toggle on={entry.active} label="Search {entry.name}" disabled={busy} onchange={(on) => toggle(entry, on)} />
                    <span class="activity-word">{entry.active ? "Active" : "Inactive"}</span>
                  {/if}
                </span>
              </div>
            {/each}
          </div>
        {:else if !loading}
          <div class="empty">
            {#if filter}<h3 class="sign">No matches</h3><p>No file here has “{filter}” in its name.</p>
            {:else if view === ATTENTION}<h3 class="sign">All good</h3><p>Every file Dowse found was read.</p>
            {:else if view === REMOVED}<h3 class="sign">Nothing removed</h3><p>Select files and choose Remove{hasFileMenu() ? `, or right-click them in ${fileManager()},` : ""} to keep them out of search.</p>
            {:else if view === NAMED}<h3 class="sign">No files added one by one</h3><p>Add files from anywhere: Add files on the left{hasFileMenu() ? `, or right-click them in ${fileManager()} and choose Dowse › Add to Dowse` : ""}.</p>
            {:else if stateFilter !== "all"}<h3 class="sign">No {stateFilter} items here</h3><p>Change the activity filter to see the other items.</p>
            {:else if view === FOLDERS}<h3 class="sign">No folders added</h3><p>Add a folder to search its contents. You can deactivate it anytime.</p>
            {:else if view === INACTIVE}<h3 class="sign">Nothing inactive</h3><p>Turn off a file or folder's Search switch to keep it here without removing it.</p>
            {:else}<h3 class="sign">Nothing read here yet</h3><p>Dowse is looking through it. Files show up here as they're read.</p>{/if}
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div class="viewing" class:open={!!viewed}>
    {#if viewed}
      <button class="close" onclick={() => (current = null)} aria-label="Close the preview"><Icon name="dismiss" size={18} /></button>
      <ViewingTable item={viewed} onlike={focused && viewed.kind && (focused.status === "done" || focused.status === "partial")
        ? (loc, place) => { search.likeFile({ path: viewed!.path, name: viewed!.name, kind: viewed!.kind! }, loc, place); dowse.go(search.route()); }
        : undefined} />
    {/if}
  </div>

</section>

<style>
  .library { display: grid; grid-template-columns: 208px minmax(0, 1fr) 0; height: 100%; position: relative;
             transition: grid-template-columns var(--t-glide) var(--glide); }
  .library.previewing { grid-template-columns: 208px minmax(0, 1fr) 360px; }
  .in-folder .head button:nth-of-type(2), .in-folder .row-main > :nth-child(2) { display: none; }
  .in-folder .head, .in-folder .row { grid-template-columns: minmax(170px, 3fr) 74px 96px minmax(118px, 1fr) 106px; }

  .places { display: flex; flex-direction: column; gap: var(--s-2); padding: var(--s-5) var(--s-3); border-right: 1px solid var(--hairline); min-height: 0; }
  .places h1 { margin: 0 var(--s-3) var(--s-3); font-size: var(--text-xl); }
  .places ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; overflow: hidden auto; min-height: 0; }
  .places li button {
    width: 100%; display: flex; align-items: center; gap: var(--s-3); padding: 8px var(--s-3);
    border-radius: var(--r-steel); text-align: left; color: var(--ink-2); font-size: var(--text-sm);
  }
  .places li button:hover { background: var(--floor-sunk); color: var(--ink); }
  .places li button.on { background: var(--floor-raised); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hairline), var(--lift-1); }
  .places li button.on :global(.icon) { color: var(--found-ink); }
  .places .label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .crumb { display: block; margin-bottom: 2px; padding: 0; color: var(--ink-2); font-size: var(--text-sm); }
  .crumb:hover { color: var(--found-ink); text-decoration: underline; }
  .count { color: var(--ink-3); }
  .adders { margin-top: auto; display: grid; gap: var(--s-1); padding-top: var(--s-3); border-top: 1px solid var(--hairline); }
  .adders button { display: flex; align-items: center; gap: var(--s-3); padding: 8px var(--s-3); border-radius: var(--r-steel); color: var(--ink-2); font-size: var(--text-sm); }
  .adders button:hover { background: var(--floor-sunk); color: var(--ink); }

  .shelf { display: flex; flex-direction: column; min-width: 0; min-height: 0;
           container: shelf / inline-size; }
  header { display: flex; align-items: end; justify-content: space-between; gap: var(--s-4); padding: var(--s-5) var(--s-5) var(--s-3); }
  .heading { min-width: 0; flex: 1 1 280px; }
  h2 { margin: 0; font-size: var(--text-2xl); line-height: 1.1; }
  .about { margin: 4px 0 0; color: var(--ink-2); font-size: var(--text-sm); overflow-wrap: anywhere; line-height: 1.5; max-width: 70ch; }
  .filter {
    display: flex; align-items: center; gap: var(--s-2); height: 34px; padding: 0 var(--s-3); width: 240px; flex: none;
    border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); color: var(--ink-3);
  }
  .filter:focus-within { border-color: var(--found); }
  .filter input { flex: 1; min-width: 0; border: 0; background: transparent; font-size: var(--text-sm); outline: none; user-select: text; }
  .state-filter { height: 34px; border: 1px solid var(--hairline); border-radius: var(--r-field); background: var(--floor-raised); color: var(--ink); font-size: var(--text-xs); max-width: 180px; }
  header { flex-wrap: wrap; }
  .remove-confirm, .inactive-note { padding: var(--s-3) var(--s-5); background: var(--floor-raised); border-bottom: 1px solid var(--hairline); font-size: var(--text-sm); }
  .remove-confirm { display: flex; flex-wrap: wrap; align-items: center; gap: var(--s-3); }
  .remove-confirm span { display: grid; gap: 4px; flex: 1; min-width: 180px; overflow-wrap: anywhere; }
  .remove-confirm small { color: var(--ink-2); line-height: 1.5; }
  .remove-confirm button { padding: 7px 10px; border-radius: var(--r-field); }
  .remove-confirm .yes { background: var(--steel); color: var(--steel-ink); }
  .inactive-note { color: var(--ink-2); }
  .inactive-note button { text-decoration: underline; color: var(--found-ink); margin-left: var(--s-2); }

  .commands { display: flex; flex-wrap: wrap; align-items: center; gap: 2px; padding: 0 var(--s-4) var(--s-2); border-bottom: 1px solid var(--hairline); }
  .commands button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: var(--r-steel); font-size: var(--text-sm); color: var(--ink); }
  .commands button :global(.icon) { color: var(--found-ink); }
  .commands button:hover:not(:disabled) { background: var(--floor-sunk); }
  .spacer { width: 1px; height: 20px; background: var(--hairline); margin: 0 var(--s-2); }
  .tally { margin-left: auto; color: var(--ink-3); }

  .table { display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; flex: 1; outline: none; }
  .head, .row { display: grid; grid-template-columns: minmax(170px, 2.6fr) minmax(70px, 1fr) 74px 96px minmax(118px, 1fr) 106px; gap: var(--s-3); align-items: center; }
  .head { position: relative; padding: 0 var(--s-5); height: 34px; border-bottom: 1px solid var(--hairline); }
  .head button:first-of-type { padding-left: 24px; }
  .select-row, .select-all { position: absolute; width: 15px; height: 15px; margin: 0; accent-color: var(--found); }
  .select-row { left: var(--s-3); }
  .select-all { left: var(--s-5); }
  .head button, .search-heading { text-align: left; font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--ink-3); }
  .head button.on, .head button:hover { color: var(--ink); }
  .arrow { margin-left: 4px; }
  .rows { overflow-y: auto; min-height: 0; }
  .row {
    position: absolute; left: var(--s-2); right: var(--s-2); height: 34px; padding: 0 var(--s-3);
    border-radius: var(--r-field); text-align: left; font-size: var(--text-sm);
  }
  .row:hover { background: var(--floor-sunk); }
  .row.selected { background: var(--found-wash); }
  .row.current { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--found) 50%, transparent); }
  .row-main { display: contents; text-align: left; }
  .row:has(.row-main:focus-visible) { outline: 2px solid var(--focus); outline-offset: -2px; }
  .row.inactive .name { color: var(--ink-2); }
  .activity { display: flex; align-items: center; gap: 5px; font-size: var(--text-xs); }
  .activity-word { color: var(--ink-2); }
  .parent-link { color: var(--ink-2); text-decoration: underline; font-size: var(--text-xs); }
  .name { display: flex; align-items: center; gap: var(--s-3); min-width: 0; font-weight: 600; color: var(--ink); padding-left: 24px; }
  .name :global(.icon:last-child) { color: var(--ink-3); }
  .thumb { width: 30px; height: 22px; flex: none; border-radius: 3px; overflow: hidden; display: grid; place-items: center; background: var(--floor-sunk); color: var(--ink-2); box-shadow: inset 0 0 0 1px var(--glass-shade); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--ink-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .num { text-align: right; }
  .status { display: flex; align-items: center; gap: 6px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: var(--ink-2); }
  .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--found); }
  .s-pending .dot, .s-partial .dot { background: transparent; box-shadow: inset 0 0 0 1.5px var(--found); }
  .s-failed .dot, .s-refused .dot, .s-missing .dot { background: var(--warn); }
  .s-failed, .s-refused, .s-missing { color: var(--ink); }
  .s-skipped .dot, .s-removed .dot, .s-inactive .dot { background: var(--ink-3); }

  .empty { display: grid; place-content: center; justify-items: center; gap: var(--s-2); height: 100%; padding: var(--s-6); text-align: center; }
  .empty h3 { margin: 0; font-size: var(--text-xl); }
  .empty p { margin: 0; color: var(--ink-2); max-width: 48ch; }

  .viewing { min-height: 0; position: relative; overflow: hidden; }
  .close { display: none; }

  /* A narrow window: while a file is on the viewing table, the places step aside (the rail still leads to them) and
     the list keeps what tells files apart; the table can be closed to bring them back */
  @media (max-width: 1296px) {
    .library { grid-template-columns: 184px minmax(0, 1fr) 0; }
    .library.previewing { grid-template-columns: minmax(0, 1fr) 320px; }
    .library.previewing .places { display: none; }
    .previewing .close { display: grid; place-items: center; position: absolute; right: var(--s-3); top: var(--s-3);
                         z-index: 2; width: 30px; height: 30px; border-radius: 50%; color: var(--ink-2);
                         background: var(--table); box-shadow: var(--lift-1); }
    .close:hover { color: var(--ink); background: var(--floor-sunk); }
  }
  @container shelf (max-width: 640px) {
    .head, .row, .in-folder .head, .in-folder .row { grid-template-columns: minmax(0, 2.6fr) 60px 82px minmax(90px, 1fr) 94px; gap: var(--s-2); }
    .head button:nth-of-type(2), .row-main > :nth-child(2) { display: none; }
  }
  @container shelf (max-width: 520px) {
    .head, .row, .in-folder .head, .in-folder .row { grid-template-columns: minmax(0, 1fr) minmax(84px, auto) 66px; }
    .head button:nth-of-type(3), .row-main > :nth-child(3), .head button:nth-of-type(4), .row-main > :nth-child(4) { display: none; }
    .activity-word { display: none; }
    header { flex-wrap: wrap; }
    .filter { width: 100%; }
    .about { white-space: normal; }
    .commands button { padding: 6px 8px; }
    .commands .label { display: none; }
  }
</style>
