<script lang="ts" module>
  export interface MapPiece {
    path: string;
    kind: string;
    name: string;
    folder: string;
    visual: boolean;
    fileDate?: string | null; // capture date, otherwise modified date (server-local calendar day)
    aisle: number;
    x: number;
    y: number;
  }
  export interface MapAisle {
    index: number;
    label: string;
    count: number;
    x: number;
    y: number;
    radius: number;
    id?: string;        // yours: its id; automatic: "auto-<n>"
    custom?: boolean;   // an aisle you made
    about?: string;
  }
</script>

<script lang="ts">
  // The storehouse floor seen from above the racks: every file a small glass case, standing in the aisle of the
  // files it's like, each aisle under its sign. Wheel to come closer (toward the pointer), drag to walk; a sign takes
  // you to its aisle. A search pulls green matching cases forward; red context cases soften. Pictures load
  // only for the cases close enough to be seen, and nothing is drawn while nothing moves.

  import { onMount, untrack } from "svelte";
  import * as THREE from "three";
  import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
  import { pictureUrl } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { cardTexture, roundedPlane, roundedFrame } from "../lib/glass";
  import { relevance, softenBackground } from "../lib/search-relevance";
  import { texturePixels } from "../lib/texture-quality";
  import { topScore } from "../lib/score-size";
  import { CLOSEST_LABEL } from "../lib/search-confidence";
  import { exploreAppearance, fitExplore } from "../lib/explore-search";

  let { pieces, aisles, grouping = "aisles", found = null, scores = null, clear = true, matches = null, filtered = false, selected = null, onselect }: {
    pieces: MapPiece[];
    aisles: MapAisle[];
    grouping?: "aisles" | "folders";
    found?: Map<string, number> | null;   // path → rank, while a search is on
    scores?: Map<string, number> | null;
    clear?: boolean;
    matches?: ReadonlySet<string> | null;
    filtered?: boolean;
    selected?: string | null;
    onselect: (piece: MapPiece) => void;
  } = $props();

  let host: HTMLDivElement;
  let signLayer: HTMLDivElement;
  let tagLayer: HTMLDivElement;
  let rankLayer: HTMLDivElement;
  const badges = new Map<string, HTMLButtonElement>();

  const W = 0.84, H = 0.62;
  const GLASS_FROM = 60, GLASS_FULL = 120;   // a case's height on screen (CSS pixels): no glass, then all of it
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  interface Case {
    piece: MapPiece;
    group: THREE.Group;
    material: THREE.MeshBasicMaterial;
    outline: THREE.MeshBasicMaterial;
    glass: THREE.Mesh; edge: THREE.Mesh; shade: THREE.Mesh;
    softness: { value: number };
    texel: { value: THREE.Vector2 };
    z: number; zTo: number; light: number; lightTo: number; s: number; sTo: number;
    hover: number;   // 0 to 1: how far the case has come forward for the pointer (eased, not a jump)
    pixels: number;
    pending: boolean;
    fallback: boolean;
    aspect: number;
    bytes: number;
    seen: number;
    retryAt: number;
  }

  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.PerspectiveCamera;
  let cases: Case[] = [];
  let byPath = new Map<string, Case>();
  let tops = new Map<number, { x: number; y: number }>();   // each aisle's highest case: where its sign stands
  let signs: HTMLButtonElement[] = [];
  let tag: HTMLDivElement;
  let frame = 0;
  let view = { x: 0, y: 0, z: 30 }, viewTo = { x: 0, y: 0, z: 30 };
  let cameraMode: "all" | "results" | "piece" | "aisle" | "manual" = "all";
  let focusedPath: string | null = null;
  let focusedAisle: number | null = null;
  let hovered: Case | null = null;
  let loading = 0;
  let disposed = false;
  let builtGrouping: "aisles" | "folders" = "aisles";
  let textureBytes = 0;
  const TEXTURE_BUDGET = 128 * 1024 * 1024;
  const loader = new THREE.TextureLoader();
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let plate: THREE.BufferGeometry, glass: THREE.BufferGeometry, glassMaterial: THREE.MeshPhysicalMaterial;
  // Seen from far, a case is its rim and its shadow: a lit edge of glass around the picture, a soft shade beneath
  let rim: THREE.BufferGeometry, rimMaterial: THREE.MeshBasicMaterial;
  let outline: THREE.BufferGeometry;
  const matchColor = new THREE.Color(), closestColor = new THREE.Color(), otherColor = new THREE.Color(), fogColor = new THREE.Color(), focusColor = new THREE.Color();

  function caseRelevance(path: string) { return relevance(path, found, clear && (matches === null || matches.has(path))); }
  let shade: THREE.BufferGeometry, shadeMaterial: THREE.MeshBasicMaterial;

  function build(): void {
    if (builtGrouping !== grouping) {
      builtGrouping = grouping; focusedAisle = null;
      cameraMode = found?.size ? "results" : "all";
    }
    for (const c of cases) { scene.remove(c.group); c.material.map?.dispose(); c.material.dispose(); c.outline.dispose(); }
    cases = [];
    textureBytes = 0;
    hovered = null;
    byPath = new Map();
    for (const badge of badges.values()) badge.remove();
    badges.clear();
    for (const piece of pieces) {
      const material = new THREE.MeshBasicMaterial({ color: 0xc9d3d0, toneMapped: false });
      const { softness, texel } = softenBackground(material, fogColor, .45);
      const picture = new THREE.Mesh(plate, material);
      const front = new THREE.Mesh(glass, glassMaterial);
      front.position.z = 0.012;
      const edge = new THREE.Mesh(rim, rimMaterial);
      edge.position.z = -0.004;
      const under = new THREE.Mesh(shade, shadeMaterial);
      under.position.set(0.025, -0.05, -0.01);
      const outlineMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
      const highlight = new THREE.Mesh(outline, outlineMaterial);
      highlight.position.z = 0.024;
      const group = new THREE.Group();
      group.add(under, edge, picture, front, highlight);
      group.position.set(piece.x, piece.y, 0);
      group.userData.path = piece.path;
      scene.add(group);
      const item: Case = { piece, group, material, outline: outlineMaterial, glass: front, edge, shade: under, softness, texel,
                          z: 0, zTo: 0, light: 1, lightTo: 1, s: 1, sTo: 1, hover: 0,
                          pixels: 0, pending: false, fallback: !piece.visual, aspect: W / H, bytes: 0, seen: 0, retryAt: 0 };
      cases.push(item);
      byPath.set(piece.path, item);
    }
    tops = new Map();
    for (const piece of pieces) {
      const top = tops.get(piece.aisle);
      if (!top || piece.y > top.y) tops.set(piece.aisle, { x: piece.x, y: piece.y });
    }
    for (const aisle of aisles) {   // centred over the aisle, at its top
      const top = tops.get(aisle.index);
      if (top) top.x = aisle.x;
    }
    signLayer.innerHTML = "";
    signs = aisles.map((aisle) => {
      const sign = document.createElement("button");
      sign.className = "aisle-sign";
      sign.dataset.groupId = aisle.id ?? String(aisle.index);
      sign.innerHTML = `<b></b><span></span>`;
      sign.querySelector("b")!.textContent = aisle.label;
      sign.querySelector("span")!.textContent = `${aisle.count.toLocaleString()} file${aisle.count === 1 ? "" : "s"}`;
      sign.title = aisle.about || aisle.label;
      sign.onclick = () => goTo(aisle.index);
      signLayer.append(sign);
      return sign;
    });
    const first = view.z === 30;
    refit();
    if (first) view = { ...viewTo };
    emphasise();
  }

  /** The whole floor in view. */
  function frameAll(): void {
    const fit = fitExplore(cases.map(c => ({...c.piece, size: c.sTo})), camera.aspect, camera.fov, 1.1);
    if (fit) viewTo = fit;
  }

  function emphasise(): void {
    const bestScore = topScore([...(scores ?? [])].filter(([path]) => matches === null || matches.has(path)).map(([,score]) => score));
    for (const c of cases) {
      const rank = found?.get(c.piece.path);
      const style = exploreAppearance(c.piece.path, found, matches, clear, scores?.get(c.piece.path), bestScore, selected);
      c.zTo = style.lift; c.sTo = style.size; c.lightTo = style.light;
      const state = style.state;
      c.softness.value = state === "other" ? 1 : 0;
      c.glass.visible = c.edge.visible = c.shade.visible = state !== "other";
      c.outline.color.copy(selected === c.piece.path && state !== "other" && found ? focusColor
        : state === "other" ? otherColor : state === "closest" ? closestColor : matchColor);
      c.outline.opacity = style.outline;
      let badge = badges.get(c.piece.path);
      if (rank === undefined) { badge?.remove(); badges.delete(c.piece.path); continue; }
      if (!badge) {
        badge = document.createElement("button"); badge.className = "rank-badge";
        badge.dataset.path = c.piece.path;
        badge.onclick = event => { event.stopPropagation(); onselect(c.piece); focusResult(c.piece.path); };
        rankLayer.append(badge); badges.set(c.piece.path, badge);
      }
      badge.textContent = String(rank + 1);
      badge.title = `${state === "match" ? "Result" : "Candidate"} ${rank + 1}: ${c.piece.name}`;
      badge.setAttribute("aria-label", badge.title);
      badge.setAttribute("aria-pressed", String(selected === c.piece.path));
      badge.dataset.relevance = state; badge.dataset.size = c.sTo.toFixed(4); badge.dataset.light = c.lightTo.toFixed(2);
      badge.dataset.x = String(c.piece.x); badge.dataset.y = String(c.piece.y); badge.dataset.aisle = String(c.piece.aisle);
    }
    for (const [path, badge] of badges) {
      if (!found?.has(path) || !byPath.has(path)) { badge.remove(); badges.delete(path); }
    }
    if (hovered && caseRelevance(hovered.piece.path) === "other") { hovered = null; host.style.cursor = "grab"; }
    host.dataset.contextLight = found ? ".35" : "1";
    host.dataset.contextFade = found ? ".45" : "0";
    wake();
  }

  const corner = new THREE.Vector3();
  function screenBox(c: Case): { width: number; height: number; visible: boolean } {
    let left = Infinity, right = -Infinity, top = -Infinity, bottom = Infinity;
    for (const [x, y] of [[-W / 2, -H / 2], [W / 2, -H / 2], [-W / 2, H / 2], [W / 2, H / 2]]) {
      corner.set(x, y, 0).applyMatrix4(c.group.matrixWorld).project(camera);
      if (corner.z < -1 || corner.z > 1) return { width: 0, height: 0, visible: false };
      left = Math.min(left, corner.x); right = Math.max(right, corner.x);
      top = Math.max(top, corner.y); bottom = Math.min(bottom, corner.y);
    }
    return { width: (right - left) * host.clientWidth / 2, height: (top - bottom) * host.clientHeight / 2,
             visible: right > -1 && left < 1 && top > -1 && bottom < 1 };
  }

  function current(c: Case): boolean { return !disposed && byPath.get(c.piece.path) === c; }

  function useTexture(c: Case, texture: THREE.Texture, pixels: number): void {
    if (!current(c)) { texture.dispose(); return; }   // removed/rebuilt floor, or a window that already left Explore
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    const image = texture.image as { width: number; height: number };
    const aspect = image.width / image.height, box = W / H;
    c.aspect = aspect;
    if (c.piece.kind === "pdf" && !c.fallback) {
      // Page text stays at full width, starting at the top, rather than an arbitrary middle crop.
      texture.repeat.set(1, aspect / box); texture.offset.set(0, 1 - aspect / box);
    } else if (aspect > box) { texture.repeat.set(box / aspect, 1); texture.offset.set((1 - box / aspect) / 2, 0); }
    else { texture.repeat.set(1, aspect / box); texture.offset.set(0, (1 - aspect / box) / 2); }
    textureBytes -= c.bytes;
    c.material.map?.dispose();
    c.bytes = Math.ceil(image.width * image.height * 4 * 4 / 3);   // RGBA plus mipmaps, not encoded WebP size
    textureBytes += c.bytes;
    c.material.map = texture; c.material.color.setScalar(c.light); c.material.needsUpdate = true;
    c.pixels = pixels;
    c.retryAt = 0;
    wake();
  }

  function card(c: Case, pixels: number): void {
    useTexture(c, cardTexture({ ...c.piece, excerpt: null }, !!dowse.state?.theme.dark, pixels), pixels);
  }

  function trimTextures(visible: Set<Case>): void {
    if (textureBytes <= TEXTURE_BUDGET) return;
    // A long walk must not retain a full-resolution texture for every file. Keep visible cases; evict old ones.
    const old = cases.filter((c) => c.bytes && !c.pending && !visible.has(c)).sort((a, b) => a.seen - b.seen);
    for (const c of old) {
      c.material.map?.dispose(); c.material.map = null; c.material.needsUpdate = true;
      c.material.color.set(0xc9d3d0);
      textureBytes -= c.bytes; c.bytes = 0; c.pixels = 0;
      if (textureBytes <= TEXTURE_BUDGET) break;
    }
  }

  // Which cases are on screen, and how big, at most every SCAN_MS while the floor moves (and once it stops): each
  // scan projects every case's corners, 0.3 s of a walk through 1,200 cases when done every frame.
  const SCAN_MS = 120;
  let lastScan = 0, scanTimer = 0;
  function loadVisible(): void {
    // Don't fetch every intermediate zoom level: upgrade once the camera is close to its new scale.
    if (Math.abs(view.z - viewTo.z) > Math.max(viewTo.z * 0.05, 0.1)) return;
    const now = performance.now();
    if (now - lastScan < SCAN_MS) {
      if (!scanTimer) scanTimer = window.setTimeout(() => { scanTimer = 0; if (!disposed) wake(); }, SCAN_MS);
      return;
    }
    lastScan = now;
    const visible = new Set<Case>(), pending: { c: Case; pixels: number }[] = [];
    for (const c of cases) {
      const box = screenBox(c);
      if (!box.visible || box.width < 10) continue;
      // Keep the slight blur stable on screen even when zooming upgrades the underlying texture.
      const repeat = c.material.map?.repeat;
      c.texel.value.set((repeat?.x ?? 1) / Math.max(box.width, 96), (repeat?.y ?? 1) / Math.max(box.height, 72));
      visible.add(c); c.seen = now;
      const pixels = texturePixels(box.width, box.height, renderer.getPixelRatio(), c.aspect);
      // Keep a loaded high-quality image when zooming out; never reload the same level or duplicate a request.
      if (c.pending || c.pixels >= pixels || now < c.retryAt) continue;
      pending.push({ c, pixels });
    }
    pending.sort((a, b) => {
      const priority = (c: Case) => c.piece.path === selected ? 2 : c === hovered ? 1 : 0;
      return priority(b.c) - priority(a.c)
        || Math.hypot(a.c.piece.x - viewTo.x, a.c.piece.y - viewTo.y) - Math.hypot(b.c.piece.x - viewTo.x, b.c.piece.y - viewTo.y);
    });
    for (const { c, pixels } of pending) {
      if (c.fallback) { card(c, pixels); continue; }
      if (loading >= 10) continue;
      c.pending = true; loading++;
      loader.load(pictureUrl(c.piece.path, null, pixels), (texture) => {
        c.pending = false; loading--;
        useTexture(c, texture, pixels);
        if (!disposed) wake();   // starts the next batch, even if the camera is already still
      }, undefined, () => {
        c.pending = false; loading--;
        if (current(c)) {
          if (!c.material.map) { c.fallback = true; card(c, pixels); }
          else c.retryAt = performance.now() + 5000;   // retain the old picture; retry on a later camera action
        }
        if (!disposed) wake();
      });
    }
    trimTextures(visible);
  }

  function wake(): void { if (!frame) frame = requestAnimationFrame(tick); }

  function tick(): void {
    frame = 0;
    let moving = false;
    const k = reduced ? 1 : 0.16;
    for (const key of ["x", "y", "z"] as const) {
      view[key] += (viewTo[key] - view[key]) * k;
      if (Math.abs(viewTo[key] - view[key]) > 0.002) moving = true;
    }
    camera.position.set(view.x, view.y - view.z * 0.12, view.z);   // a little from the front: the cases stand
    camera.lookAt(view.x, view.y, 0);
    // The glass as far as it can be seen. From afar a case is a few dozen pixels tall, its reflections thinner than a
    // pixel: they jumped from pixel to pixel as the floor was dragged, a shimmer over every case. It fades in as the
    // cases grow on screen (fully from GLASS_FULL pixels), and isn't drawn at all below GLASS_FROM.
    const casePixels = H * host.clientHeight / (2 * Math.max(view.z, 0.1) * Math.tan((camera.fov * Math.PI) / 360));
    const glassy = Math.min(Math.max((casePixels - GLASS_FROM) / (GLASS_FULL - GLASS_FROM), 0), 1);
    glassMaterial.opacity = 0.16 * glassy;
    glassMaterial.envMapIntensity = 1.6 * glassy;
    glassMaterial.visible = glassy > 0.01;
    for (const c of cases) {
      c.z += (c.zTo - c.z) * k; c.s += (c.sTo - c.s) * k; c.light += (c.lightTo - c.light) * k;
      if (Math.abs(c.zTo - c.z) + Math.abs(c.sTo - c.s) + Math.abs(c.lightTo - c.light) > 0.002) moving = true;
      const hoverTo = !found && hovered === c ? 1 : 0, chosen = !found && selected === c.piece.path ? 0.2 : 0;
      c.hover += (hoverTo - c.hover) * (reduced ? 1 : 0.3);
      if (Math.abs(hoverTo - c.hover) > 0.002) moving = true; else c.hover = hoverTo;
      c.group.position.z = c.z + 0.15 * c.hover + chosen;
      c.group.scale.setScalar(c.s * (chosen ? 1.12 : 1 + 0.12 * c.hover));
      if (c.material.map) c.material.color.setScalar(c.light);
    }
    renderer.render(scene, camera);
    placeOverlays();
    loadVisible();
    if (moving) wake();   // outstanding requests wake on completion, not via a busy GPU loop
  }

  const v = new THREE.Vector3();
  function placeOverlays(): void {
    const width = host.clientWidth, height = host.clientHeight;
    // Every size read before any style is written: read between the writes, each one laid the page out again (0.3 s
    // of a walk through Explore, measured with a JavaScript CPU profile).
    const sizes = signs.map((sign) => sign ? [sign.offsetWidth, sign.offsetHeight] : [0, 0]);
    aisles.forEach((aisle, i) => {
      const top = tops.get(aisle.index) ?? { x: aisle.x, y: aisle.y + aisle.radius };
      v.set(top.x, top.y + H / 2 + 0.12, 0).project(camera);   // just above the aisle's own top case
      const sign = signs[i];
      if (!sign) return;
      const screenX = ((v.x + 1) / 2) * width, screenY = ((1 - v.y) / 2) * height;
      const [signWidth, signHeight] = sizes[i];
      const visible = v.z < 1 && screenX > signWidth / 2 && screenX < width - signWidth / 2
        && screenY > signHeight && screenY < height;
      sign.style.opacity = visible ? "1" : "0";
      sign.style.pointerEvents = visible ? "auto" : "none";
      sign.tabIndex = visible ? 0 : -1;
      sign.style.transform = `translate(${((v.x + 1) / 2) * width}px, ${((1 - v.y) / 2) * height}px) translate(-50%, -100%)`;
    });
    for (const [path, badge] of badges) {
      const c = byPath.get(path)!;
      v.set(-W / 2, H / 2, .03).applyMatrix4(c.group.matrixWorld).project(camera);
      const x = (v.x + 1) / 2 * width, y = (1 - v.y) / 2 * height;
      const visible = v.z >= -1 && v.z <= 1 && x > 2 && x < width - 26 && y > 2 && y < height - 26;
      badge.style.opacity = visible ? "1" : "0";
      badge.style.pointerEvents = visible ? "auto" : "none";
      badge.tabIndex = visible ? 0 : -1;
      badge.setAttribute("aria-hidden", String(!visible));
      badge.style.transform = `translate(${x}px, ${y}px)`;
    }
    const shown = hovered ?? (selected ? byPath.get(selected) : null);
    if (shown) {
      v.set(-W / 2, -H / 2 - .04, .03).applyMatrix4(shown.group.matrixWorld).project(camera);
      tag.style.opacity = "1";
      tag.dataset.relevance = caseRelevance(shown.piece.path);
      tag.dataset.size = shown.sTo.toFixed(4);
      tag.classList.toggle("selected", selected === shown.piece.path);
      tag.style.transform = `translate(${((v.x + 1) / 2) * width}px, ${((1 - v.y) / 2) * height}px)`;
      if (tag.dataset.path !== shown.piece.path) {
        tag.dataset.path = shown.piece.path;
        tag.innerHTML = "<b></b><span></span>";
        tag.querySelector("b")!.textContent = shown.piece.name;
      }
      const rank = found?.get(shown.piece.path);
      tag.querySelector("span")!.textContent = `${rank === undefined ? "" : `#${rank + 1} · `}${shown.piece.folder}`;
    } else tag.style.opacity = "0";
  }

  /** The case under the pointer, by where each case rests: not by its lifted, grown shape. Judged by that shape, a
   *  case coming forward for the pointer moved its edge off it (the floor is seen a little from below), dropped
   *  back under it, and came forward again: cases flickering as the mouse moved along an edge. The hovered case
   *  keeps an edge as wide as it grows, so the pointer has to leave it plainly. */
  function caseAt(event: MouseEvent): Case | null {
    const rect = host.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const { origin, direction } = raycaster.ray;
    if (Math.abs(direction.z) < 1e-6) return null;
    let best: Case | null = null;
    for (const c of cases) {
      if (caseRelevance(c.piece.path) === "other") continue;   // context cases can't take a result's selection
      const along = (c.z - origin.z) / direction.z;            // where the ray meets this case's resting plane
      if (along <= 0) continue;
      const grow = c === hovered ? 1.12 : 1;
      const x = origin.x + direction.x * along - c.group.position.x, y = origin.y + direction.y * along - c.group.position.y;
      if (Math.abs(x) <= (W / 2) * c.s * grow && Math.abs(y) <= (H / 2) * c.s * grow && (!best || c.z > best.z || c === hovered)) {
        best = c;
        if (c === hovered) break;   // still on the hovered case: it stays
      }
    }
    return best;
  }

  let drag: { x: number; y: number; vx: number; vy: number; moved: boolean } | null = null;

  function down(event: PointerEvent): void {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button")) return;
    host.focus({preventScroll: true});
    drag = { x: event.clientX, y: event.clientY, vx: viewTo.x, vy: viewTo.y, moved: false };
    host.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent): void {
    const badge = (event.target as HTMLElement).closest<HTMLButtonElement>(".rank-badge");
    if (badge) { hovered = byPath.get(badge.dataset.path!) ?? null; wake(); return; }
    if (drag) {
      const perPixel = (2 * view.z * Math.tan((camera.fov * Math.PI) / 360)) / host.clientHeight;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      viewTo = { ...viewTo, x: drag.vx - dx * perPixel, y: drag.vy + dy * perPixel };
      cameraMode = "manual";
      wake();
      return;
    }
    const over = caseAt(event);
    if (over !== hovered) { hovered = over; host.style.cursor = over ? "pointer" : "grab"; wake(); }
  }
  function up(event: PointerEvent): void {
    if (!drag) return;
    const moved = drag?.moved;
    drag = null;
    if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
    if (!moved) { const at = caseAt(event); if (at) onselect(at.piece); }
  }
  function wheel(event: WheelEvent): void {
    event.preventDefault();
    const factor = Math.exp(event.deltaY * 0.0014);
    const z = Math.min(Math.max(viewTo.z * factor, 2.2), 80);
    // come closer toward the pointer
    const rect = host.getBoundingClientRect();
    const nx = (event.clientX - rect.left) / rect.width - 0.5, ny = (event.clientY - rect.top) / rect.height - 0.5;
    const halfH = viewTo.z * Math.tan((camera.fov * Math.PI) / 360);
    const shift = 1 - z / viewTo.z;
    viewTo = { x: viewTo.x + nx * 2 * halfH * camera.aspect * shift, y: viewTo.y - ny * 2 * halfH * shift, z };
    cameraMode = "manual";
    wake();
  }

  function resize(): void {
    if (!host.clientWidth || !host.clientHeight) return;
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setSize(host.clientWidth, host.clientHeight, false);
    camera.aspect = host.clientWidth / Math.max(host.clientHeight, 1);
    camera.updateProjectionMatrix();
    if (cases.length && cameraMode !== "manual") refit();
    wake();
  }

  function floor(): void {
    // The clear color reaches the screen as given (no output conversion): the page's sRGB numbers, as they are
    const color = getComputedStyle(document.documentElement).getPropertyValue("--floor").trim() || "#e4e9e7";
    scene.background = new THREE.Color(color);   // no tone mapping (below): the floor is exactly the page's
    const styles = getComputedStyle(document.documentElement);
    matchColor.set(styles.getPropertyValue("--search-match").trim());
    closestColor.set(styles.getPropertyValue("--ink-3").trim());
    otherColor.set(styles.getPropertyValue("--search-other").trim());
    fogColor.set(color);
    focusColor.set(styles.getPropertyValue("--focus").trim());
    for (const c of cases) {
      const state = caseRelevance(c.piece.path);
      c.outline.color.copy(selected === c.piece.path && state !== "other" && found ? focusColor : state === "other" ? otherColor : state === "closest" ? closestColor : matchColor);
    }
    const dark = document.documentElement.dataset.theme === "dark";
    rimMaterial?.color.set(dark ? 0x6f8f8c : 0xfbfdfc);
    shadeMaterial?.color.set(dark ? 0x000000 : 0x1d3335);
    if (shadeMaterial) shadeMaterial.opacity = dark ? 0.5 : 0.2;
  }

  onMount(() => {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.NoToneMapping;   // tone mapping would shade the floor too, off the page's color
    host.prepend(renderer.domElement);
    scene = new THREE.Scene();
    rim = roundedPlane(W + 0.07, H + 0.07, 0.06);
    outline = roundedFrame(W + 0.05, H + 0.05, 0.05, 0.028);
    rimMaterial = new THREE.MeshBasicMaterial({ toneMapped: false });
    shade = roundedPlane(W + 0.1, H + 0.1, 0.08);
    shadeMaterial = new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, toneMapped: false });
    floor();
    const pmrem = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
    const environment = pmrem.fromScene(room, 0.04, 0.1, 100, { size: 64 });
    scene.environment = environment.texture;
    room.dispose(); pmrem.dispose();
    camera = new THREE.PerspectiveCamera(34, 1, 0.1, 400);
    plate = roundedPlane(W, H, 0.035);
    // Reflective glass only at the edge: a full-face pane catches the environment's rectangular lights
    // and washes out the image. The open centre keeps both photos and document previews untouched.
    glass = roundedFrame(W + 0.02, H + 0.02, 0.04, 0.022);
    glassMaterial = new THREE.MeshPhysicalMaterial({ color: 0xffffff, transparent: true, opacity: 0.16, roughness: 0.05,
      metalness: 0, clearcoat: 1, clearcoatRoughness: 0.05, envMapIntensity: 1.6, depthWrite: false });
    tag = document.createElement("div");
    tag.className = "case-tag";
    tagLayer.append(tag);
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    const themed = new MutationObserver(() => { floor(); wake(); });
    themed.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    resize();
    build();
    return () => {
      disposed = true;
      clearTimeout(scanTimer);
      cancelAnimationFrame(frame);
      observer.disconnect();
      themed.disconnect();
      for (const c of cases) { c.material.map?.dispose(); c.material.dispose(); c.outline.dispose(); }
      plate.dispose(); glass.dispose(); glassMaterial.dispose();
      rim.dispose(); rimMaterial.dispose(); outline.dispose(); shade.dispose(); shadeMaterial.dispose();
      environment.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    };
  });

  // Search only updates relevance; it must not rebuild cases, discard sharp textures or reset a user's pan.
  $effect(() => { void pieces; void aisles; if (renderer) untrack(build); });
  $effect(() => { void found; void scores; void clear; void matches; if (renderer) untrack(() => { emphasise(); found ? fitResults() : overview(); }); });
  $effect(() => { void selected; if (renderer) untrack(emphasise); });

  function refit(): void {
    let fit: ReturnType<typeof fitExplore> = null;
    if (cameraMode === "piece" && focusedPath) {
      const c = byPath.get(focusedPath);
      fit = c ? fitExplore([{...c.piece, size: c.sTo}], camera.aspect, camera.fov, .45, 2.2) : null;
    } else if (cameraMode === "aisle" && focusedAisle !== null) {
      fit = fitExplore(cases.filter(c => c.piece.aisle === focusedAisle).map(c => ({...c.piece, size:c.sTo})), camera.aspect, camera.fov, 1.0);
    } else if (cameraMode === "results" && found?.size) {
      fit = fitExplore(cases.filter(c => found!.has(c.piece.path)).map(c => ({...c.piece, size:c.sTo})), camera.aspect, camera.fov, .75);
    }
    if (fit) viewTo = fit;
    else { cameraMode = "all"; frameAll(); } // a hard filter may remove the focused file or aisle
    host.dataset.cameraMode = cameraMode;
    host.dataset.targetDistance = viewTo.z.toFixed(4);
    wake();
  }

  export function focusResult(path: string): void {
    if (!renderer || !byPath.has(path)) return;
    focusedPath = path; cameraMode = "piece"; refit();
  }

  export function fitResults(): void {
    if (!renderer) return;
    cameraMode = "results"; refit();
  }

  export function goTo(index: number): void {
    if (renderer && aisles.some(a => a.index === index)) { focusedAisle = index; cameraMode = "aisle"; refit(); }
  }

  export function overview(): void {
    if (renderer) { cameraMode = "all"; refit(); }
  }
</script>

<div class="map-view">
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="floor" bind:this={host} role="region" aria-label="Explore {grouping === 'folders' ? 'folder' : 'aisle'} map · {pieces.length} files" tabindex="0" onpointerdown={down} onpointermove={move} onpointerup={up}
     onpointerleave={() => { hovered = null; wake(); }} onwheel={wheel}>
  <div class="signs" bind:this={signLayer}></div>
  <div class="tags" bind:this={tagLayer} aria-hidden="true"></div>
  <div class="ranks" bind:this={rankLayer}></div>
</div>
  {#if found !== null}
    <div class="relevance-key" aria-label="Search relevance">
      {#if !found.size}<span class="size-note">No results here</span>
      {:else if filtered}<span class="closest">Filtered files</span>
      {:else if clear}<span class="match">Matches · sharp</span>
      {:else}<span class="closest">{CLOSEST_LABEL}</span>{/if}
      <span class="other">Other files · softened</span>
      {#if found.size && clear}<span class="size-note">Larger = stronger match</span>{/if}
      {#if found.size}<span class="size-note">Numbers = rank · Alt+↑/↓</span>{/if}
    </div>
  {/if}
</div>

<style>
  .map-view { display: grid; grid-template-rows: minmax(0, 1fr) auto; min-height: 0; }
  .floor { position: relative; width: 100%; height: 100%; overflow: hidden; cursor: grab; touch-action: none; }
  .floor :global(canvas) { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .signs, .tags, .ranks { position: absolute; inset: 0; pointer-events: none; }
  .relevance-key { display: flex; flex-wrap: wrap;
                   gap: var(--s-2) var(--s-3); padding: var(--s-2) var(--s-3);
                   border-radius: var(--r-field); background: var(--floor-raised); border: 1px solid var(--hairline);
                   font-size: var(--text-xs); pointer-events: none; }
  .relevance-key span { display: flex; align-items: center; gap: var(--s-2); }
  .relevance-key span::before { content: ""; width: 8px; height: 8px; border-radius: 2px; }
  .relevance-key .match { color: var(--search-match-ink); }
  .relevance-key .closest { color: var(--ink-2); }
  .relevance-key .other { color: var(--search-other-ink); }
  .relevance-key .match::before { background: var(--search-match); }
  .relevance-key .closest::before { background: var(--ink-3); }
  .relevance-key .other::before { background: var(--search-other); }
  .relevance-key .size-note::before { display: none; }
  .ranks :global(.rank-badge) { position: absolute; left: 0; top: 0; min-width: 23px; height: 23px; padding: 1px 5px;
    background: var(--tag); border: 1px solid var(--ink-3); border-radius: var(--r-steel); color: var(--tag-ink);
    font: 600 var(--text-xs) / 1 var(--face-data); font-variant-numeric: tabular-nums; box-shadow: var(--lift-1); }
  .ranks :global(.rank-badge[data-relevance="match"]) { border-color: var(--search-match); color: var(--search-match-ink); }
  .ranks :global(.rank-badge[aria-pressed="true"]) { outline: 2px solid var(--focus); outline-offset: 2px; }
  .signs :global(.aisle-sign) {
    position: absolute; left: 0; top: 0; pointer-events: auto;
    display: grid; justify-items: start; gap: 1px;
    padding: 6px 12px 7px;
    background: var(--steel); color: var(--steel-ink);
    border-radius: var(--r-steel);
    box-shadow: var(--lift-2), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    transition: opacity var(--t-rail) var(--rail), background var(--t-rail) var(--rail);
    white-space: nowrap;
  }
  .signs :global(.aisle-sign:hover) { background: var(--steel-raised); }
  .signs :global(.aisle-sign b) { font: 620 var(--text-md) / 1.15 var(--face-sign); font-variation-settings: "wdth" 112; }
  .signs :global(.aisle-sign span) { font: 400 10.5px / 1.3 var(--face-data); color: var(--steel-ink-2); }
  .tags :global(.case-tag) {
    position: absolute; left: 0; top: 0; display: grid; gap: 1px; max-width: 260px;
    padding: 5px 9px; background: var(--tag); color: var(--tag-ink); border-radius: var(--r-steel); box-shadow: var(--lift-1);
    opacity: 0; transition: opacity var(--t-rail) var(--rail);
  }
  .tags :global(.case-tag b) { font: 600 var(--text-sm) / 1.25 var(--face-ui); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tags :global(.case-tag span) { font: 400 10.5px / 1.3 var(--face-data); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tags :global(.case-tag[data-relevance="match"]) { box-shadow: var(--lift-1), inset 0 -2px 0 var(--search-match); }
  .tags :global(.case-tag[data-relevance="closest"].selected) { box-shadow: var(--lift-2), inset 0 -2px 0 var(--focus); }
  .tags :global(.case-tag[data-relevance="other"]) { box-shadow: var(--lift-1), inset 0 -2px 0 var(--search-other); }
</style>
