<script lang="ts" module>
  export interface Piece {
    key: string;          // the file's path: the same file keeps its vitrine from one query to the next
    path: string;
    kind: string;
    loc: number | null;
    name: string;
    line: string;         // the tag's machine line: kind, page or moment, folder
    strong: boolean;
    visual: boolean;
    excerpt: string | null;
    back?: boolean;       // the rest of the rack, behind what a search pulled forward
    closest?: boolean;    // optional weaker candidate, not an engine-confirmed strong match
    score?: number;       // relative ranking value; used for visual area, never presented as confidence
  }
</script>

<script lang="ts">
  // A viewport-shaped rack with a gentle curve, one file in each stable spatial cell. Search highlights the
  // loaded collection rather than replacing it: clear matches gather on a closer viewing plane, other files
  // soften in their original cells. Unclear candidates stay in the rack without a false match presentation.
  // Rendering happens only while something moves or a sharper preview arrives; at rest the GPU does nothing.

  import { onMount, untrack } from "svelte";
  import * as THREE from "three";
  import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
  import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
  import { pictureUrl } from "../lib/api";
  import { dowse } from "../lib/app.svelte";
  import { cardTexture, roundedPlane, roundedFrame } from "../lib/glass";
  import { softenBackground, wallRelevance } from "../lib/search-relevance";
  import { scoreSize, topScore } from "../lib/score-size";
  import { wallSlots, wallOrder, wallRadius, foregroundGroup } from "../lib/wall-layout";
  import { CLOSEST_LABEL } from "../lib/search-confidence";
  import Icon from "./Icon.svelte";

  let {
    pieces, collectionKeys = [], selected = 0, terms = [], searching = false, clear = true, active = true, onselect, onopen,
  }: {
    pieces: Piece[];
    collectionKeys?: string[]; // stable file order: a search highlights the rack instead of replacing it
    selected?: number;
    terms?: string[];
    searching?: boolean;
    clear?: boolean;       // the answer has a clear match; otherwise these are only the closest candidates
    active?: boolean;      // a retained gallery is entirely idle while Quick search is visible
    onselect: (index: number) => void;
    onopen: (index: number) => void;
  } = $props();

  let host: HTMLDivElement;
  let tagLayer: HTMLDivElement;
  let hovered = $state(-1);
  let prepared = $state(false);
  let shadersReady = false;
  let disposed = false;

  const CELL_W = 1.36, CELL_H = 1.0, GAP = 0.16, DEPTH = 0.14;
  let radius = 9;                            // grows with the rack so its edges never fold over each other
  const MIN_ZOOM = 0.5, MAX_ZOOM = 8;
  let zoom = $state(1);
  let baseDistance = 6.4, targetDistance = 6.4;
  let extent = { x: 0, y: 0 };
  let layoutKey = "";
  let presentationKey = "";
  let foregroundDistance = 0, focusDepth = 0;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** Where a case stands and how it shows: place, scale, opacity. */
  type Stance = { x: number; y: number; z: number; s: number; o: number };

  interface Vitrine {
    piece: Piece;
    group: THREE.Group;
    glass: THREE.Mesh;
    picture: THREE.Mesh;
    pictureMaterial: THREE.MeshBasicMaterial;
    rim: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    softness: { value: number };
    texel: { value: THREE.Vector2 };
    faceVersion: number;
    faceKey: string;
    from: Stance;
    to: Stance;
    now: Stance;
    tilt: { x: number; y: number };
    tiltTo: { x: number; y: number };
    facing: number;   // its angle on the curved rack; the pointer's tilt is added to it
    started: number;
    delay: number;
    leaving: boolean;
    tag: HTMLDivElement;
    pixels: number;          // keep the sharper thumbnail if a later query makes the case smaller
  }

  let renderer: THREE.WebGLRenderer;
  let scene: THREE.Scene;
  let camera: THREE.PerspectiveCamera;
  let rack: THREE.Group;
  let vitrines = new Map<string, Vitrine>();
  let order: Vitrine[] = [];
  let frame = 0;
  let pan = { x: 0, y: 0 }, panTo = { x: 0, y: 0 }, bounds = { x: 0, y: 0 }, centre = 0, middle = 0;
  const loader = new THREE.TextureLoader();
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2(-9, -9);

  // Shared pieces of every vitrine
  let glassGeometry: THREE.BufferGeometry;
  let pictureGeometry: THREE.BufferGeometry;
  let rimGeometry: THREE.BufferGeometry;
  let glassMaterial: THREE.MeshPhysicalMaterial;
  const matchColor = new THREE.Color(), closestColor = new THREE.Color(), otherColor = new THREE.Color(), fogColor = new THREE.Color(), focusColor = new THREE.Color();

  // Pictures reach the graphics card a few a frame. Arriving together (from the picture cache, all in a fraction of
  // a second), sixty were uploaded in one frame and froze the page for half a second (texSubImage2D, measured with
  // a JavaScript CPU profile on 10,700 files).
  const UPLOADS_PER_FRAME = 3;
  let uploads: { vitrine: Vitrine; texture: THREE.Texture }[] = [];

  function setFace(vitrine: Vitrine, texture: THREE.Texture): void {
    uploads.push({ vitrine, texture });
    wake();
  }

  function applyFaces(): void {
    for (const { vitrine, texture } of uploads.splice(0, UPLOADS_PER_FRAME)) {
      if (vitrine.leaving || vitrines.get(vitrine.piece.key) !== vitrine) { texture.dispose(); continue; }
      vitrine.pictureMaterial.map?.dispose();
      vitrine.pictureMaterial.map = texture;
      const image = texture.image as { width: number; height: number };
      vitrine.texel.value.set(1 / Math.max(image.width, 1), 1 / Math.max(image.height, 1));
      vitrine.pictureMaterial.needsUpdate = true;
    }
  }

  function face(vitrine: Vitrine): void {
    const piece = vitrine.piece;
    const version = ++vitrine.faceVersion;
    if (!piece.visual) {
      setFace(vitrine, cardTexture(piece, !!dowse.state?.theme.dark, vitrine.pixels));
      return;
    }
    loader.load(pictureUrl(piece.path, piece.loc, vitrine.pixels, piece.kind === "pdf" && !piece.back ? terms : []), (texture) => {
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = 8;
      // A query can replace this case while its thumbnail is still loading.
      if (vitrine.leaving || vitrines.get(piece.key) !== vitrine || vitrine.faceVersion !== version) { texture.dispose(); return; }
      // cover the vitrine: crop the picture to its shape
      const image = texture.image as { width: number; height: number };
      const box = CELL_W / CELL_H;
      const aspect = image.width / image.height;
      if (piece.kind === "pdf") {   // a page: its full width, from the top (the marked words are near it), white below
        texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
        texture.repeat.set(1, aspect / box);
        texture.offset.set(0, 1 - aspect / box);
      } else if (aspect > box) { texture.repeat.set(box / aspect, 1); texture.offset.set((1 - box / aspect) / 2, 0); }
      else { texture.repeat.set(1, aspect / box); texture.offset.set(0, (1 - aspect / box) / 2); }
      setFace(vitrine, texture);
    }, undefined, () => {
      if (!vitrine.leaving && vitrines.get(piece.key) === vitrine && vitrine.faceVersion === version) {
        setFace(vitrine, cardTexture(piece, !!dowse.state?.theme.dark, vitrine.pixels));
      }
    });
  }

  /** A case's picture size: smaller when the wall holds hundreds (each is small on screen then, and their pictures
   *  together would fill the graphics memory); the viewing table and the inspector load their own, full size. */
  function basePixels(): number {
    return pieces.length > 160 ? 96 : 560;
  }

  function makeVitrine(piece: Piece): Vitrine {
    const width = CELL_W, height = CELL_H;
    const group = new THREE.Group();
    const glass = new THREE.Mesh(glassGeometry, glassMaterial);
    glass.scale.set(width, height, 1);
    // The picture is the unobstructed front surface. Keep the physical glass behind it so the room's
    // bright rectangular lights can reflect on the bevel, but never cover or refract the file itself.
    // Dimming is a darker tint, not transparency.
    const pictureMaterial = new THREE.MeshBasicMaterial({ color: 0xcfd8d5, toneMapped: false });
    const { softness, texel } = softenBackground(pictureMaterial, fogColor);
    const picture = new THREE.Mesh(pictureGeometry, pictureMaterial);
    picture.position.z = DEPTH / 2 + 0.003;
    const rim = new THREE.Mesh(rimGeometry,
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
    rim.position.z = DEPTH / 2 + 0.006;
    group.add(picture, glass, rim);
    rack.add(group);
    const tag = document.createElement("div");
    tag.className = "tag";
    tagLayer.append(tag);
    const start = { x: 0, y: 0, z: -2.4, s: 0.9, o: 0 };
    const vitrine: Vitrine = {
      piece, group, glass, picture, pictureMaterial, rim, softness, texel, faceVersion: 0, faceKey: "", pixels: basePixels(), tag,
      from: { ...start }, to: { ...start }, now: { ...start }, tilt: { x: 0, y: 0 }, tiltTo: { x: 0, y: 0 }, facing: 0,
      started: 0, delay: 0, leaving: false,
    };
    return vitrine;
  }

  function layout(): void {
    const now = performance.now();
    const keep = new Set(pieces.map((p) => p.key));
    for (const [key, vitrine] of vitrines) {
      if (!keep.has(key) && !vitrine.leaving) {   // no longer matches: back into the rack
        vitrine.leaving = true;
        vitrine.from = { ...vitrine.now };
        vitrine.to = { ...vitrine.now, z: -2.6, o: 0, s: 0.9 };
        vitrine.started = now;
        vitrine.delay = 0;
      }
    }
    const best = topScore(pieces.filter((p) => !p.back && !p.closest).map((p) => p.score));
    const sizes = pieces.map((p) => searching && clear && !p.back && !p.closest ? scoreSize(p.score, best) : 1);
    const spatialOrder = wallOrder(pieces.map((p) => p.key), collectionKeys);
    // The rack does not reshuffle when matches are picked up and gathered in front.
    const packed = wallSlots(spatialOrder.map(() => 1), camera.aspect, CELL_W, CELL_H, GAP);
    const slots = new Map(spatialOrder.map((i, slot) => [i, packed[slot]]));
    radius = wallRadius(Math.max(0, ...packed.map((p) => Math.abs(p.x))));
    const foregroundRanks = searching && clear ? pieces.flatMap((p, i) => p.back || p.closest ? [] : [i]) : [];
    const group = foregroundGroup(foregroundRanks.map((i) => sizes[i]), camera.aspect, camera.fov, CELL_W, CELL_H, GAP);
    const foregroundSlots = new Map(foregroundRanks.map((i, slot) => [i, group.slots[slot]]));
    foregroundDistance = foregroundRanks.length ? group.distance : 0;
    const nextPresentation = JSON.stringify(foregroundRanks.map((i) => pieces[i].key));
    if (presentationKey !== nextPresentation) zoom = 1;
    presentationKey = nextPresentation;
    extent = {
      x: Math.max(0, ...packed.map((p) => Math.abs(Math.sin(p.x / radius) * radius) + CELL_W / 2)),
      y: Math.max(0, ...packed.map((p) => Math.abs(p.y) + CELL_H / 2)),
    };
    fitDistance();
    const foregroundScale = foregroundDistance ? foregroundDistance / group.distance : 1;
    // Keep the lower-right controls clear, especially in the minimum-height workspace.
    const groupLift = foregroundDistance ? Math.min(32, host.clientHeight * .1)
      * 2 * foregroundDistance * Math.tan(camera.fov * Math.PI / 360) / Math.max(1, host.clientHeight) : 0;
    order = [];
    pieces.forEach((piece, rank) => {
      const size = sizes[rank];
      const slot = slots.get(rank)!;
      const cx = slot.x, cy = slot.y;
      const angle = cx / radius;
      const x = Math.sin(angle) * radius;
      const z = -(1 - Math.cos(angle)) * radius;
      const picked = foregroundSlots.get(rank);
      let vitrine = vitrines.get(piece.key);
      if (vitrine?.leaving) {
        removeVitrine(vitrine);
        vitrine = undefined;
      }
      const created = !vitrine;
      if (!vitrine) {
        vitrine = makeVitrine(piece);
        vitrine.now = { x, y: cy, z: z - 2.4, s: 0.9, o: 0 };
        vitrines.set(piece.key, vitrine);
      }
      vitrine.piece = piece;
      vitrine.pixels = Math.max(vitrine.pixels, size >= 1.25 ? 900 : basePixels());
      vitrine.tag.dataset.path = piece.path;
      vitrine.tag.dataset.rank = String(rank);
      vitrine.tag.dataset.size = size.toFixed(4);
      vitrine.tag.dataset.column = String(slot.column);
      vitrine.tag.dataset.row = String(slot.row);
      vitrine.tag.dataset.layer = picked ? "foreground" : "rack";
      vitrine.tag.dataset.relevance = wallRelevance(searching, piece.back, clear && !piece.closest);
      const faceKey = JSON.stringify([piece.loc, piece.excerpt, piece.name, piece.kind, piece.visual, vitrine.pixels,
                                      piece.kind === "pdf" && !piece.back ? terms : []]);
      if (vitrine.faceKey !== faceKey) {
        vitrine.faceKey = faceKey;
        face(vitrine);
      }
      // pulled forward: the strong ones come out of the rack; the weaker stay back, dimmer; the rest of the rack
      // stays where it stands, in the shade
      const target = picked ? { x: picked.x * foregroundScale, y: picked.y * foregroundScale + groupLift,
          z: focusDepth, s: size * foregroundScale, o: piece.strong ? 1 : 0.9 }
        : piece.back ? { x, y: cy, z: z - 0.3, s: 1, o: 0.45 }
        : { x, y: cy, z: z + (piece.strong ? 0.35 : -0.1), s: size,
            o: piece.strong ? 1 : 0.8 };
      // A case that stays where it is keeps its motion as it was: a new layout doesn't replay every entrance.
      const moves = created || !sameStance(vitrine.to, target);
      vitrine.tag.dataset.depth = target.z.toFixed(4);
      vitrine.tag.dataset.foregroundX = picked ? picked.x.toFixed(4) : "";
      vitrine.tag.dataset.foregroundY = picked ? picked.y.toFixed(4) : "";
      vitrine.facing = picked ? 0 : -angle;
      if (moves) {
        vitrine.from = { ...vitrine.now };
        vitrine.to = target;
        vitrine.started = now;
        vitrine.delay = reduced ? 0 : Math.min(rank, 24) * 28;
      }
      order.push(vitrine);
    });
    // Search keeps the surrounding collection in frame, including when nothing was returned.
    const ys = packed.map((v) => v.y), xs = packed.map((v) => Math.sin(v.x / radius) * radius);
    centre = ys.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : 0;
    middle = xs.length ? (Math.max(...xs) + Math.min(...xs)) / 2 : 0;
    updateBounds();
    const nextKey = JSON.stringify([camera.aspect, spatialOrder.map((i) => pieces[i].key)]);
    if (layoutKey !== nextKey) panTo = { x: clamp(middle, bounds.x), y: centre };
    layoutKey = nextKey;
    wake();
  }

  /** Two stances close enough that moving between them wouldn't show. */
  function sameStance(a: Stance | undefined, b: Stance): boolean {
    return !!a && Math.abs(a.x - b.x) < 1e-3 && Math.abs(a.y - b.y) < 1e-3 && Math.abs(a.z - b.z) < 1e-3
      && Math.abs(a.s - b.s) < 1e-3 && Math.abs(a.o - b.o) < 1e-3;
  }

  function removeVitrine(vitrine: Vitrine): void {
    rack.remove(vitrine.group);
    vitrine.pictureMaterial.map?.dispose();
    vitrine.pictureMaterial.dispose();
    vitrine.rim.material.dispose();
    vitrine.tag.remove();
    vitrines.delete(vitrine.piece.key);
  }

  const glide = (t: number) => 1 - Math.pow(1 - t, 4);

  function wake(): void {
    if (!disposed && shadersReady && active && !frame) frame = requestAnimationFrame(tick);
  }

  function tick(time: number): void {
    frame = 0;
    if (!active) return;
    applyFaces();
    let moving = uploads.length > 0;
    for (const vitrine of [...vitrines.values()]) {
      const span = reduced ? 1 : 520;
      const t = Math.min(Math.max((time - vitrine.started - vitrine.delay) / span, 0), 1);
      const e = glide(t);
      for (const k of ["x", "y", "z", "s", "o"] as const) vitrine.now[k] = vitrine.from[k] + (vitrine.to[k] - vitrine.from[k]) * e;
      if (t < 1) moving = true;
      if (vitrine.leaving && t >= 1) { removeVitrine(vitrine); continue; }
      vitrine.tilt.x += (vitrine.tiltTo.x - vitrine.tilt.x) * 0.18;
      vitrine.tilt.y += (vitrine.tiltTo.y - vitrine.tilt.y) * 0.18;
      if (Math.abs(vitrine.tilt.x - vitrine.tiltTo.x) + Math.abs(vitrine.tilt.y - vitrine.tiltTo.y) > 0.0005) moving = true;
      const lift = order[hovered] === vitrine ? 0.12 : 0;
      const chosen = order[selected] === vitrine ? 0.08 : 0;
      vitrine.group.position.set(vitrine.now.x, vitrine.now.y, vitrine.now.z + lift + chosen);
      // The whole case tilts toward the pointer, picture and glass together: turning the glass alone swung its
      // front through the picture, and the part behind showed as a refracted, washed-out copy.
      vitrine.group.rotation.set(vitrine.tilt.x, vitrine.facing + vitrine.tilt.y, 0);
      const light = 0.5 + 0.5 * vitrine.now.o;
      vitrine.pictureMaterial.color.setRGB(light, light, light);
      const background = searching && !!vitrine.piece.back;
      vitrine.softness.value = background ? 1 : 0;
      if (background) vitrine.pictureMaterial.color.lerp(otherColor, 0.12);
      vitrine.rim.material.color.copy(!searching ? focusColor : background ? otherColor : clear && !vitrine.piece.closest ? matchColor : closestColor);
      vitrine.rim.material.opacity = searching ? (background ? 0.55 : order[selected] === vitrine ? 1 : 0.85) : order[selected] === vitrine ? 0.8 : 0;
      // Closeness changes lift/light, not the score-to-area relationship.
      vitrine.group.scale.setScalar(vitrine.now.s); // context stays the same physical size, only softened/dimmed
      vitrine.group.visible = vitrine.now.o > 0.02 || !vitrine.leaving;
    }
    const ease = reduced ? 1 : 0.16;
    pan.x += (panTo.x - pan.x) * ease;
    pan.y += (panTo.y - pan.y) * ease;
    if (Math.abs(pan.x - panTo.x) + Math.abs(pan.y - panTo.y) > 0.001) moving = true;
    camera.position.z += (targetDistance - camera.position.z) * ease;
    if (Math.abs(camera.position.z - targetDistance) > 0.001) moving = true;
    else camera.position.z = targetDistance;
    camera.position.set(pan.x, pan.y, camera.position.z);
    camera.lookAt(pan.x, pan.y, -1.35);
    renderer.render(scene, camera);
    prepared = true;
    placeTags();
    if (!moving && (zoom > 1 || foregroundDistance)) sharpenVisiblePictures();
    if (moving) wake();
  }

  const v = new THREE.Vector3();
  function sharpenVisiblePictures(): void {
    const started = performance.now();
    let upgraded = 0;
    for (const vitrine of order) {
      v.copy(vitrine.group.position).project(camera);
      if (Math.abs(v.x) > 1.1 || Math.abs(v.y) > 1.1) continue;
      const projected = CELL_W * vitrine.now.s * host.clientHeight * renderer.getPixelRatio()
        / (2 * (camera.position.z - vitrine.now.z) * Math.tan(camera.fov * Math.PI / 360));
      const pixels = projected > 700 ? 1600 : projected > 350 ? 900 : projected > 200 ? 560 : basePixels();
      if (pixels > vitrine.pixels) {
        vitrine.pixels = pixels; face(vitrine);
        // Zooming a document-heavy rack can otherwise rasterize/upload many sharp cards at once.
        // Keep the interaction responsive and finish the remaining visible upgrades on later frames.
        if (++upgraded >= 2 || performance.now() - started >= 4) { wake(); break; }
      }
    }
  }

  function placeTags(): void {
    const width = host.clientWidth, height = host.clientHeight;
    order.forEach((vitrine, index) => {
      const tag = vitrine.tag;
      if (vitrine.piece.back || vitrine.now.o < 0.5) { tag.style.opacity = "0"; return; }
      const w = CELL_W / 2, h = CELL_H / 2;
      v.set(-w + 0.1, -h + 0.1, DEPTH).applyMatrix4(vitrine.group.matrixWorld).project(camera);
      const x = ((v.x + 1) / 2) * width, y = ((1 - v.y) / 2) * height;
      v.set(w, h, DEPTH).applyMatrix4(vitrine.group.matrixWorld).project(camera);
      const right = ((v.x + 1) / 2) * width, top = ((1 - v.y) / 2) * height;
      // At overview scale labels would cover the file and collide with neighbouring cases.
      // Zoom reveals accession tags; hovering still provides the full filename as a native tooltip.
      if (right - x < 75) { tag.style.opacity = "0"; return; }
      const full = right - x >= 110 && (index === hovered || index === selected
        || (vitrine.tag.dataset.layer === "foreground" && index < 6));
      if (x < 4 || right > width - 4 || top < 0 || y > height) { tag.style.opacity = "0"; return; }   // half out: no tag
      tag.style.opacity = "1";
      tag.style.maxWidth = `${Math.max(right - x - 12, 80)}px`;
      tag.style.transform = `translate(${x}px, ${y}px) translateY(-100%)`;
      // A high score can still project to a narrow case in a small window: keep its label legible.
      const big = vitrine.to.s >= 1.25 && right - x >= 170;
      tag.classList.toggle("big", big);
      tag.classList.toggle("on", index === selected);
      tag.classList.toggle("number", !full);
      // a small tag's machine line is kind and place; the folder is on the big one, the list and the viewing table
      const text = full ? (big ? vitrine.piece.line : vitrine.piece.line.split(" · ").slice(0, 2).join(" · ")) : "";
      const key = `${vitrine.piece.key}|${index}|${text}`;
      if (tag.dataset.key !== key) {
        tag.dataset.key = key;
        tag.innerHTML = "";
        const number = document.createElement("i");
        number.textContent = String(index + 1);
        if (full) {
          const name = document.createElement("b");
          name.textContent = vitrine.piece.name;
          const line = document.createElement("span");
          line.append(number, text);
          tag.append(name, line);
        } else tag.append(number);
      }
    });
  }

  function hit(event: PointerEvent | MouseEvent): number {
    const rect = host.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    // Background context isn't a result: it must not clear the preview or consume a result index.
    const found = raycaster.intersectObjects(order.filter((v) => !v.piece.back).map((v) => v.glass), false)[0];
    return found ? order.findIndex((v) => v.glass === found.object) : -1;
  }

  let dragging: { x: number; y: number; panX: number; panY: number; moved: boolean } | null = null;

  function onPointerMove(event: PointerEvent): void {
    if ((event.target as HTMLElement).closest(".zoom-controls")) return;
    if (dragging) {
      const perPixel = 2 * (camera.position.z - focusDepth) * Math.tan(camera.fov * Math.PI / 360) / Math.max(host.clientHeight, 1);
      const dx = (event.clientX - dragging.x) * perPixel, dy = (event.clientY - dragging.y) * perPixel;
      if (Math.abs(dx) + Math.abs(dy) > 0.02) dragging.moved = true;
      panTo = { x: clamp(dragging.panX - dx, bounds.x), y: centre + clamp(dragging.panY + dy - centre, bounds.y) };
      wake();
      return;
    }
    const index = hit(event);
    if (index !== hovered) {
      if (order[hovered]) order[hovered].tiltTo = { x: 0, y: 0 };
      hovered = index;
      host.style.cursor = index >= 0 ? "pointer" : "grab";
    }
    if (index >= 0) {   // tilt toward the pointer: the light slides across the glass
      const rect = host.getBoundingClientRect();
      const nx = (event.clientX - rect.left) / rect.width - 0.5, ny = (event.clientY - rect.top) / rect.height - 0.5;
      order[index].tiltTo = { x: ny * 0.18, y: nx * 0.3 };
    }
    wake();
  }

  function clamp(value: number, limit: number): number {
    return Math.max(-limit, Math.min(limit, value));
  }

  function onPointerDown(event: PointerEvent): void {
    if (event.button !== 0 || (event.target as HTMLElement).closest(".zoom-controls")) return;
    host.focus({ preventScroll: true });
    dragging = { x: event.clientX, y: event.clientY, panX: panTo.x, panY: panTo.y, moved: false };
    host.setPointerCapture(event.pointerId);
  }

  function onPointerUp(event: PointerEvent): void {
    if (!dragging) return;
    const moved = dragging?.moved;
    dragging = null;
    if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
    if (!moved) {
      const index = hit(event);
      if (index >= 0) onselect(index);
    }
  }

  function onWheel(event: WheelEvent): void {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? host.clientHeight : 1;
    if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
      panTo.x = clamp(panTo.x + (event.deltaX || event.deltaY) * unit * 0.006 * targetDistance / baseDistance, bounds.x);
      wake();
    } else {
      const rect = host.getBoundingClientRect();
      setZoom(zoom * Math.exp(-Math.max(-300, Math.min(300, event.deltaY * unit)) * 0.0014),
              (event.clientX - rect.left) / rect.width - 0.5, (event.clientY - rect.top) / rect.height - 0.5);
    }
  }

  function updateBounds(): void {
    const halfH = (targetDistance - focusDepth) * Math.tan(camera.fov * Math.PI / 360);
    const active = foregroundDistance ? {
      x: Math.max(0, ...order.filter((v) => v.tag.dataset.layer === "foreground").map((v) => Math.abs(v.to.x) + CELL_W * v.to.s / 2)),
      y: Math.max(0, ...order.filter((v) => v.tag.dataset.layer === "foreground").map((v) => Math.abs(v.to.y) + CELL_H * v.to.s / 2)),
    } : extent;
    bounds = { x: Math.max(active.x - halfH * camera.aspect * 0.75, 0), y: Math.max(active.y - halfH * 0.75, 0) };
    panTo = { x: clamp(panTo.x, bounds.x), y: centre + clamp(panTo.y - centre, bounds.y) };
  }

  function setZoom(value: number, x = 0, y = 0): void {
    if (!camera) return;
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
    const oldDistance = targetDistance;
    targetDistance = focusDepth + (baseDistance - focusDepth) / next;
    const halfH = (oldDistance - focusDepth) * Math.tan(camera.fov * Math.PI / 360);
    const shift = 1 - (targetDistance - focusDepth) / (oldDistance - focusDepth);
    panTo = { x: panTo.x + x * 2 * halfH * camera.aspect * shift, y: panTo.y - y * 2 * halfH * shift };
    zoom = next;
    updateBounds();
    wake();
  }

  function resetView(): void {
    setZoom(1);
    panTo = { x: clamp(middle, bounds.x), y: centre };
    wake();
  }

  function onKeyDown(event: KeyboardEvent): void {
    if (event.target !== host || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "+" || event.key === "=") setZoom(zoom * 1.2);
    else if (event.key === "-" || event.key === "_") setZoom(zoom / 1.2);
    else if (event.key === "0") resetView();
    else return;
    event.preventDefault();
    event.stopPropagation();
  }

  function fitDistance(): void {
    const tan = Math.tan(camera.fov * Math.PI / 360);
    baseDistance = Math.max(6.4, 6.4 * (1.6 / camera.aspect),
      Math.max(extent.x / (tan * camera.aspect), extent.y / tan) * 1.12 + 0.35);
    // Scale the whole group in world space rather than pulling the camera away from the rack.
    // Its apparent size is unchanged, and background context remains visible even with many matches.
    foregroundDistance = Math.min(foregroundDistance, baseDistance * .65);
    focusDepth = foregroundDistance ? baseDistance - foregroundDistance : 0;
    targetDistance = focusDepth + (baseDistance - focusDepth) / zoom;
  }

  function resize(): void {
    if (!active || !host.clientWidth || !host.clientHeight) return;
    const width = host.clientWidth, height = host.clientHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(height, 1);
    camera.updateProjectionMatrix();
    layout(); // repack for the real viewport shape, not just a change of camera aspect
    camera.position.z = targetDistance;
    wake();
  }

  onMount(() => {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.NoToneMapping;   // tone mapping would shade the floor too, off the page's color
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.prepend(renderer.domElement);
    scene = new THREE.Scene();
    floor();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    const environment = pmrem.fromScene(room, 0.04, 0.1, 100, { size: 64 });
    scene.environment = environment.texture;
    room.dispose(); pmrem.dispose();
    camera = new THREE.PerspectiveCamera(32, 1, 0.1, 500);
    camera.position.set(0, 0, 6.4);
    rack = new THREE.Group();
    scene.add(rack);
    pool = new THREE.Mesh(new THREE.PlaneGeometry(26, 14), new THREE.MeshBasicMaterial({ map: lightPool(), transparent: true,
                                                                                         depthWrite: false, toneMapped: false }));
    pool.position.set(0, -0.4, -3.2);
    scene.add(pool);
    glassGeometry = new RoundedBoxGeometry(1, 1, DEPTH, 4, 0.07);
    pictureGeometry = roundedPlane(CELL_W - 0.1, CELL_H - 0.1, 0.07);
    rimGeometry = roundedFrame(CELL_W, CELL_H, 0.07, 0.032);
    // Glass that reflects the room (its lights on the bevel) without transmission: a transmissive material makes
    // three.js draw the whole wall twice a frame, the second time into a buffer of another colour space, and every
    // case's picture then changed shader programs back and forth, frame after frame (most of the page's time while
    // typing, measured with a JavaScript CPU profile). What's behind the glass is the picture itself.
    glassMaterial = new THREE.MeshPhysicalMaterial({
      color: 0xffffff, metalness: 0, roughness: 0.03, transparent: true, opacity: 0.16, depthWrite: false,
      clearcoat: 1, clearcoatRoughness: 0.08, specularIntensity: 1, envMapIntensity: 1.1,
    });
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    const themed = new MutationObserver(() => { floor(); wake(); });
    themed.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    resize();
    // Let WebView2 compile the first gallery shaders in parallel instead of blocking the first draw
    // (and the search field) on getProgramParameter. Later visits reuse this prepared renderer.
    const begin = () => { if (!disposed) { shadersReady = true; wake(); } };
    renderer.compileAsync(scene, camera).then(begin, begin);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      themed.disconnect();
      for (const vitrine of [...vitrines.values()]) removeVitrine(vitrine);
      for (const { texture } of uploads.splice(0)) texture.dispose();
      glassGeometry.dispose();
      pictureGeometry.dispose(); rimGeometry.dispose();
      glassMaterial.dispose();
      environment.dispose();
      pool.geometry.dispose();
      (pool.material as THREE.MeshBasicMaterial).map?.dispose();
      (pool.material as THREE.MeshBasicMaterial).dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    };
  });

  $effect(() => {
    if (!active) { cancelAnimationFrame(frame); frame = 0; return; }
    void pieces; void collectionKeys; void searching; void clear;
    if (renderer) untrack(resize);
  });

  $effect(() => {
    if (!active) return;
    void selected; void pieces[selected]?.key;
    const vitrine = order[selected];
    if (vitrine && renderer) {   // keep the chosen one in view only when it is actually outside the viewport
      const halfH = (targetDistance - vitrine.to.z) * Math.tan(camera.fov * Math.PI / 360);
      if (Math.abs(vitrine.to.x - panTo.x) + CELL_W * vitrine.to.s / 2 < halfH * camera.aspect * 0.9
          && Math.abs(vitrine.to.y - panTo.y) + CELL_H * vitrine.to.s / 2 < halfH * 0.9) { wake(); return; }
      panTo = { x: clamp(vitrine.to.x, bounds.x), y: centre + clamp(vitrine.to.y - centre, bounds.y) };
      const scale = targetDistance / baseDistance;
      if (Math.abs(vitrine.to.x - middle) < 2.4 * scale && Math.abs(vitrine.to.y - centre) < 1.2 * scale) {
        panTo = { x: clamp(middle, bounds.x), y: centre };
      }
      wake();
    }
  });

  let pool: THREE.Mesh;

  /** A soft pool of light on the floor behind the rack: depth without a single drawn line. */
  function lightPool(): THREE.CanvasTexture {
    const canvas = document.createElement("canvas");
    canvas.width = 512; canvas.height = 288;
    const g = canvas.getContext("2d")!;
    const dark = document.documentElement.dataset.theme === "dark";
    // An ellipse that fades to nothing before every edge: a circle on this wide canvas was cut off at the top and
    // bottom while still half lit, and the pool showed as a rectangle (on the dark floor most)
    g.setTransform(1, 0, 0, 288 / 512, 0, 0);
    const light = g.createRadialGradient(256, 256, 8, 256, 256, 250);
    light.addColorStop(0, dark ? "rgba(140, 214, 204, 0.16)" : "rgba(255, 255, 255, 0.85)");
    light.addColorStop(0.6, dark ? "rgba(140, 214, 204, 0.06)" : "rgba(255, 255, 255, 0.32)");
    light.addColorStop(1, dark ? "rgba(140, 214, 204, 0)" : "rgba(255, 255, 255, 0)");
    g.fillStyle = light;
    g.fillRect(0, 0, 512, 512);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }

  function floor(): void {
    if (pool) {
      const material = pool.material as THREE.MeshBasicMaterial;
      material.map?.dispose();
      material.map = lightPool();
      material.needsUpdate = true;
    }
    const styles = getComputedStyle(document.documentElement);
    const color = styles.getPropertyValue("--floor").trim() || "#e4e9e7";
    scene.background = new THREE.Color(color);   // no tone mapping (below): the floor is exactly the page's
    matchColor.set(styles.getPropertyValue("--search-match").trim());
    closestColor.set(styles.getPropertyValue("--ink-3").trim());
    otherColor.set(styles.getPropertyValue("--search-other").trim());
    fogColor.set(color);
    focusColor.set(styles.getPropertyValue("--focus").trim());
  }

  $effect(() => {
    if (!active) return;
    void dowse.state?.theme.dark;
    if (renderer) {
      const themedFrame = requestAnimationFrame(() => { floor(); wake(); });   // after the page's colors switched
      return () => cancelAnimationFrame(themedFrame);

    }
  });
</script>

<div class="gallery" aria-busy={!prepared}>
  {#if !prepared}<div class="preparing" role="status">Preparing gallery…</div>{/if}
  <div class="relevance-key" aria-label={searching ? "Search relevance" : "Gallery navigation"}>
    {#if searching}
      {#if !pieces.some((piece) => !piece.back)}<span class="closest">No results · collection shown</span>
      {:else if clear}<span class="match">Matches · sharp</span>
      {:else}<span class="closest">{CLOSEST_LABEL}</span>{/if}
      {#if clear && pieces.some((piece) => piece.closest)}<span class="closest">Other candidates · sharp</span>{/if}
      <span class="other">Other files · softened</span>
      {#if clear && pieces.some((piece) => !piece.back)}<span class="size-note">Larger = stronger match</span>{/if}
    {:else}<span class="navigation">Scroll to zoom · drag to move</span>{/if}
  </div>
<!-- The interactive canvas needs its own focus target for gallery shortcuts. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div class="wall" bind:this={host} role="region" aria-label="Search gallery" aria-describedby="gallery-zoom-help" tabindex="0"
     title={hovered >= 0 ? pieces[hovered]?.name : undefined}
     onpointermove={onPointerMove} onpointerdown={onPointerDown} onpointerup={onPointerUp}
     onpointercancel={() => (dragging = null)} onkeydown={onKeyDown}
     onpointerleave={() => { hovered = -1; wake(); }} onwheel={onWheel}
     ondblclick={(event) => {
       if ((event.target as HTMLElement).closest(".zoom-controls")) return;
       const index = hit(event); if (index >= 0) onopen(index);
     }}>
  <div class="tags" bind:this={tagLayer} aria-hidden="true"></div>
  <span id="gallery-zoom-help" class="visually-hidden">Scroll to zoom toward the pointer, up to 800% for large collections. Drag to move, or Shift+scroll to move horizontally. Use + and - to zoom, and 0 to reset when the gallery is focused.</span>
  <div class="zoom-controls" role="group" aria-label="Gallery zoom">
    <button onclick={() => setZoom(zoom / 1.2)} disabled={zoom <= MIN_ZOOM} aria-label="Zoom out" title="Zoom out (−)">
      <Icon name="remove" size={18} />
    </button>
    <output class="data" aria-label="Zoom level">{Math.round(zoom * 100)}%</output>
    <button onclick={() => setZoom(zoom * 1.2)} disabled={zoom >= MAX_ZOOM} aria-label="Zoom in" title="Zoom in (+)">
      <Icon name="add" size={18} />
    </button>
    <button class="reset" onclick={resetView} aria-label="Reset gallery view" title="Reset view (0)">
      <Icon name="again" size={18} />
    </button>
  </div>
</div>
</div>

<style>
  .preparing { position: absolute; inset: 0; display: grid; place-content: center;
    color: var(--ink-2); pointer-events: none; z-index: 1; }
  .gallery { display: grid; grid-template-rows: auto minmax(0, 1fr); width: 100%; height: 100%; min-height: 0; }
  .wall { position: relative; width: 100%; height: 100%; overflow: hidden; cursor: grab; touch-action: none; }
  .wall :global(canvas) { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .tags { position: absolute; inset: 0; pointer-events: none; }
  .relevance-key { display: flex; align-items: center; flex-wrap: wrap; min-height: 56px;
                   gap: var(--s-3); padding: var(--s-2) var(--s-4); pointer-events: none;
                   font-size: var(--text-xs); color: var(--ink-2); }
  .relevance-key span { display: flex; align-items: center; gap: var(--s-2); }
  .relevance-key span::before { content: ""; width: 8px; height: 8px; border-radius: 2px; }
  .relevance-key .match::before { background: var(--search-match); }
  .relevance-key .closest::before { background: var(--ink-3); }
  .relevance-key .other::before { background: var(--search-other); }
  .relevance-key .match { color: var(--search-match-ink); }
  .relevance-key .other { color: var(--search-other-ink); }
  .relevance-key .size-note::before { display: none; }
  .relevance-key .navigation::before { display: none; }
  .zoom-controls { position: absolute; right: var(--s-4); bottom: var(--s-4); z-index: 1;
                   display: flex; align-items: center; padding: var(--s-1); border: 1px solid var(--hairline);
                   border-radius: var(--r-field); background: var(--floor-raised); box-shadow: var(--lift-1); cursor: default; }
  .zoom-controls button { display: grid; place-items: center; width: 32px; height: 32px; border-radius: var(--r-steel); }
  .zoom-controls button:hover:not(:disabled) { background: var(--floor-sunk); }
  .zoom-controls output { width: 48px; text-align: center; color: var(--ink-2); }
  .zoom-controls .reset { margin-left: var(--s-1); border-left: 1px solid var(--hairline); border-radius: 0; }
  .tags :global(.tag) {
    position: absolute;
    left: 0;
    top: 0;
    max-width: 240px;
    display: grid;
    gap: 1px;
    padding: 5px 9px 5px;
    background: var(--tag);
    color: var(--tag-ink);
    border-radius: var(--r-steel);
    box-shadow: var(--lift-1);
    opacity: 0;
    transition: opacity var(--t-rail) var(--rail);
    will-change: transform;
  }
  .tags :global(.tag b) {
    font: 600 var(--text-sm) / 1.25 var(--face-ui);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tags :global(.tag span) {
    font: 400 10.5px / 1.3 var(--face-data);
    font-variant-numeric: tabular-nums;
    color: color-mix(in srgb, var(--tag-ink) 72%, transparent);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .tags :global(.tag.big) { max-width: 340px; padding: 7px 12px; }
  .tags :global(.tag.big b) { font: 620 var(--text-lg) / 1.2 var(--face-sign); font-variation-settings: "wdth" 108; }
  .tags :global(.tag.on) { box-shadow: var(--lift-2), inset 0 -2px 0 var(--found); }
  .tags :global(.tag i) {
    font: 500 10px / 1.3 var(--face-data); font-style: normal; font-variant-numeric: tabular-nums;
    color: var(--tag-found); margin-right: 6px;
  }
  .tags :global(.tag.number) { padding: 2px 7px; border-radius: 3px; box-shadow: inset 0 -2px 0 var(--found); }
  .tags :global(.tag.number i) { margin: 0; color: var(--tag-ink); }
  .tags :global(.tag[data-relevance="match"]) {
    background: color-mix(in srgb, var(--search-match) 14%, var(--tag));
    box-shadow: var(--lift-1), inset 0 -2px 0 var(--search-match);
  }
  .tags :global(.tag[data-relevance="match"].on) { box-shadow: var(--lift-2), inset 0 -3px 0 var(--search-match); }
  .tags :global(.tag[data-relevance="match"] i) { color: color-mix(in srgb, var(--search-match) 55%, var(--tag-ink)); }
  .tags :global(.tag[data-relevance="closest"]) { box-shadow: var(--lift-1); }
  .tags :global(.tag[data-relevance="closest"].on) { box-shadow: var(--lift-2), inset 0 -2px 0 var(--focus); }
  .tags :global(.tag[data-relevance="closest"] i) { color: var(--tag-ink); }
</style>
