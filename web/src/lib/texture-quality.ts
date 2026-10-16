// Stable levels share the picture cache and avoid a new request for every wheel tick.
export const TEXTURE_LEVELS = [96, 256, 512, 1024, 2048] as const;

/** The source's long side needed to cover a case, including portrait/panorama cropping and display density. */
export function texturePixels(width: number, height: number, density: number, aspect: number): number {
  const ratio = Math.max(aspect, 0.01);
  const needed = Math.max(width * Math.max(1, 1 / ratio), height * Math.max(1, ratio)) * density;
  return TEXTURE_LEVELS.find((level) => level >= needed) ?? TEXTURE_LEVELS[TEXTURE_LEVELS.length - 1];
}
