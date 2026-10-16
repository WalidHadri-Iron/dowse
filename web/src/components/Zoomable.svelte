<script lang="ts">
  // A picture you can look into: the wheel zooms toward the pointer, dragging pans, a double click goes from the
  // whole picture to a close look at that spot and back. It never leaves its frame: at the whole picture it's
  // centred, and zoomed in, its edges stop at the frame's.

  let { src, alt, onstate, onerror }: {
    src: string;
    alt: string;
    onstate?: (zoom: number) => void;   // how far in (1: the whole picture)
    onerror?: () => void;               // the picture couldn't be had (a page past the last)
  } = $props();

  const MOST = 8;
  let frame = $state<HTMLDivElement | null>(null);
  let picture = $state<HTMLImageElement | null>(null);
  let zoom = $state(1), x = $state(0), y = $state(0);
  let drag: { x: number; y: number; fromX: number; fromY: number; moved: boolean } | null = null;

  $effect(() => { void src; reset(); });
  $effect(() => { onstate?.(zoom); });

  export function reset(): void { zoom = 1; x = 0; y = 0; }
  export function zoomBy(factor: number, at?: { x: number; y: number }): void {
    if (!frame) return;
    const box = frame.getBoundingClientRect();
    const px = (at?.x ?? box.left + box.width / 2) - box.left - box.width / 2;
    const py = (at?.y ?? box.top + box.height / 2) - box.top - box.height / 2;
    const next = Math.min(Math.max(zoom * factor, 1), MOST);
    // keep the point under the pointer where it is
    x = px - (px - x) * (next / zoom);
    y = py - (py - y) * (next / zoom);
    zoom = next;
    keepInside();
  }

  function keepInside(): void {
    if (!frame || !picture) return;
    if (zoom <= 1.0001) { x = 0; y = 0; zoom = 1; return; }
    const box = frame.getBoundingClientRect();
    const w = picture.offsetWidth * zoom, h = picture.offsetHeight * zoom;
    const spareX = Math.max((w - box.width) / 2, 0), spareY = Math.max((h - box.height) / 2, 0);
    x = Math.min(Math.max(x, -spareX), spareX);
    y = Math.min(Math.max(y, -spareY), spareY);
  }

  function wheel(event: WheelEvent): void {
    event.preventDefault();
    zoomBy(Math.exp(-event.deltaY * 0.0016), { x: event.clientX, y: event.clientY });
  }
  function down(event: PointerEvent): void {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, fromX: x, fromY: y, moved: false };
    frame?.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent): void {
    if (!drag) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    x = drag.fromX + dx;
    y = drag.fromY + dy;
    keepInside();
  }
  function up(event: PointerEvent): void {
    drag = null;
    frame?.releasePointerCapture(event.pointerId);
  }
  function double(event: MouseEvent): void {
    if (zoom > 1.05) reset();
    else zoomBy(3, { x: event.clientX, y: event.clientY });
  }
</script>

<div class="frame" bind:this={frame} class:zoomed={zoom > 1.01} role="img" aria-label={alt}
     onwheel={wheel} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
     ondblclick={double}>
  <img bind:this={picture} {src} {alt} draggable="false" {onerror}
       style:transform="translate({x}px, {y}px) scale({zoom})" />
</div>

<style>
  .frame {
    position: relative;
    width: 100%;
    height: 100%;
    display: grid;
    grid-template: minmax(0, 100%) / minmax(0, 100%);   /* a definite area: the picture fits it, tall or wide */
    place-items: center;
    overflow: hidden;
    cursor: zoom-in;
    touch-action: none;
    user-select: none;
  }
  .frame.zoomed { cursor: grab; }
  .frame.zoomed:active { cursor: grabbing; }
  img {
    max-width: 100%;
    max-height: 100%;
    transform-origin: center;
    border-radius: 4px;
    box-shadow: var(--lift-3);
    background: var(--paper);   /* (no will-change: the picture is drawn again at its zoom, sharp) */
  }
  @media (prefers-reduced-motion: no-preference) {
    .frame:not(:active) img { transition: transform 80ms linear; }
  }
</style>
