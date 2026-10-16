// What every vitrine is made of: its picture's rounded plate, and the face of a file that isn't a picture.

import * as THREE from "three";
import { runs } from "./app.svelte";

export interface Face {
  path: string;
  kind: string;
  name: string;
  excerpt: string | null;
}

function roundedShape(width: number, height: number, radius: number): THREE.Shape {
  const shape = new THREE.Shape();
  const w = width / 2, h = height / 2, r = Math.min(radius, w, h);
  shape.moveTo(-w + r, -h);
  shape.lineTo(w - r, -h); shape.quadraticCurveTo(w, -h, w, -h + r);
  shape.lineTo(w, h - r); shape.quadraticCurveTo(w, h, w - r, h);
  shape.lineTo(-w + r, h); shape.quadraticCurveTo(-w, h, -w, h - r);
  shape.lineTo(-w, -h + r); shape.quadraticCurveTo(-w, -h, -w + r, -h);
  return shape;
}

/** A thin rim on the face of the glass; its open centre leaves the file's picture untouched. */
export function roundedFrame(width: number, height: number, radius: number, thickness: number): THREE.BufferGeometry {
  const shape = roundedShape(width, height, radius);
  shape.holes.push(roundedShape(width - thickness * 2, height - thickness * 2, Math.max(radius - thickness, 0)));
  return new THREE.ShapeGeometry(shape, 6);
}

export function roundedPlane(width: number, height: number, radius: number): THREE.BufferGeometry {
  const shape = roundedShape(width, height, radius);
  const geometry = new THREE.ShapeGeometry(shape, 6);
  const position = geometry.attributes.position, uv = geometry.attributes.uv;
  for (let i = 0; i < position.count; i++) uv.setXY(i, position.getX(i) / width + 0.5, position.getY(i) / height + 0.5);
  return geometry;
}

/** A face for files that aren't pictures: a document's opening words, a recording's sound. */
export function cardTexture(piece: Face, dark: boolean, pixels = 680): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = pixels; canvas.height = Math.round(pixels * 500 / 680);
  const g = canvas.getContext("2d")!;
  g.scale(pixels / 680, pixels / 680);   // same layout, crisp glyphs at the case's actual screen resolution
  if (piece.kind === "audio" || piece.kind === "video") {
    g.fillStyle = dark ? "#16282a" : "#1d3335";
    g.fillRect(0, 0, 680, 500);
    let seed = [...piece.path].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
    const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    g.fillStyle = "#12b5a6";
    for (let x = 40; x < 640; x += 10) {
      const h = 30 + random() * 160 * Math.sin((x / 640) * Math.PI);
      g.globalAlpha = 0.55 + random() * 0.45;
      g.fillRect(x, 250 - h / 2, 6, h);
    }
    g.globalAlpha = 1;   // (the name is on the tag)
  } else {
    // The page itself: its opening words (or the matching ones, marked), no title: the tag names it
    g.fillStyle = dark ? "#d9dedc" : "#fbfcfb";   // paper under the night hall's light, not a lamp
    g.fillRect(0, 0, 680, 500);
    g.fillStyle = "#141821";
    g.font = "400 25px 'Segoe UI Variable Text', 'Segoe UI', sans-serif";
    const text = runs(piece.excerpt ?? piece.name);
    let x = 40, y = 64;
    for (const run of text) {
      for (const word of run.text.split(/(\s+)/)) {
        const width = g.measureText(word).width;
        if (x + width > 640) { x = 40; y += 36; if (word.trim() === "") continue; }
        if (y > 470) break;
        if (run.marked && word.trim()) {
          g.fillStyle = "rgba(255,214,10,0.6)";
          g.fillRect(x - 2, y - 24, width + 4, 32);
          g.fillStyle = "#141821";
        }
        g.fillText(word, x, y);
        x += width;
      }
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}
