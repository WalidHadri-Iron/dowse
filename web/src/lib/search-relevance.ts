import * as THREE from "three";

/** An empty result set still means a search, not the neutral collection. Rank zero remains a candidate. */
export function relevance(path: string, found: ReadonlyMap<string, number> | null, clear = true): "neutral" | "match" | "closest" | "other" {
  return found === null ? "neutral" : !found.has(path) ? "other" : clear ? "match" : "closest";
}

/** Returned candidates are only matches when the answer has a clear match; keep membership/rank untouched. */
export function wallRelevance(searching: boolean, back: boolean | undefined, clear: boolean): "neutral" | "match" | "closest" | "other" {
  return !searching ? "neutral" : back ? "other" : clear ? "match" : "closest";
}

/** Shared by Search and Explore: soften only background previews, with no postprocessing or idle pass. */
export function softenBackground(material: THREE.MeshBasicMaterial, fogColor: THREE.Color, fogAmount = .18) {
  const softness = { value: 0 }, texel = { value: new THREE.Vector2(1 / 680, 1 / 500) };
  material.onBeforeCompile = (shader) => {
    shader.uniforms.rackSoftness = softness;
    shader.uniforms.rackTexel = texel;
    shader.uniforms.rackFog = { value: fogColor };
    shader.uniforms.rackFade = { value: fogAmount };
    shader.fragmentShader = `uniform float rackSoftness;\nuniform float rackFade;\nuniform vec2 rackTexel;\nuniform vec3 rackFog;\n${shader.fragmentShader}`
      .replace("#include <map_fragment>", `
        #ifdef USE_MAP
          vec4 sampledDiffuseColor;
          if (rackSoftness > 0.001) {
            vec2 d = rackTexel * 4.0 * rackSoftness;
            sampledDiffuseColor = texture2D(map, vMapUv) * 0.25;
            sampledDiffuseColor += (texture2D(map, vMapUv + vec2(d.x, 0.0)) + texture2D(map, vMapUv - vec2(d.x, 0.0))
              + texture2D(map, vMapUv + vec2(0.0, d.y)) + texture2D(map, vMapUv - vec2(0.0, d.y))) * 0.125;
            sampledDiffuseColor += (texture2D(map, vMapUv + d) + texture2D(map, vMapUv - d)
              + texture2D(map, vMapUv + vec2(d.x, -d.y)) + texture2D(map, vMapUv + vec2(-d.x, d.y))) * 0.0625;
          } else sampledDiffuseColor = texture2D(map, vMapUv);
          diffuseColor *= sampledDiffuseColor;
        #endif
      `)
      .replace("#include <opaque_fragment>", `
        outgoingLight = mix(outgoingLight, rackFog, rackSoftness * rackFade);
        #include <opaque_fragment>
      `);
  };
  material.customProgramCacheKey = () => "search-relevance-v2";
  return { softness, texel };
}
