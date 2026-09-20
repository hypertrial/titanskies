"use client";

import { CONTEXT_BOUNDS, detailTileBounds, type DetailGridShape } from "@/data/contextSchema";
import type { ForecastRaster } from "@/rendering/forecastRaster";
import type { ResidentForecastRaster } from "@/rendering/forecastRasterCache";
import { geoBoundsGeometry } from "@/rendering/geoPatch";
import { INSPECT_SHELL_RADIUS } from "@/rendering/projection";
import { useFrame, useThree } from "@react-three/fiber";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { DataTexture, LinearFilter, NearestFilter, RGBAFormat, ShaderMaterial, SRGBColorSpace, Texture, UnsignedByteType } from "three";

type ImageSource = ImageBitmap | HTMLImageElement;
export type DetailSurfaceImages = { tileId: number; column: number; row: number; fromIndex: number; toIndex: number; resident: ResidentForecastRaster[] };
export const DETAIL_FADE_SECONDS = 0.18;

export function advanceDetailMix(current: number, target: number, delta: number, reducedMotion = false): number {
  if (reducedMotion || current === target) return target;
  const step = Math.max(0, delta) / DETAIL_FADE_SECONDS;
  return current < target ? Math.min(target, current + step) : Math.max(target, current - step);
}

const vertexShader = /* glsl */ `
varying vec3 vPosition;
void main() {
  vPosition = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

export const concentrationFragmentShader = /* glsl */ `
precision highp float;
uniform sampler2D uScalarA;
uniform sampler2D uScalarB;
uniform sampler2D uWeightA;
uniform sampler2D uWeightB;
uniform float uMix;
uniform float uOpacity;
uniform float uLayerOpacity;
uniform float uBaseLayer;
uniform vec4 uBounds;
uniform float uConcentrationScale;
uniform sampler2D uDetailMixMap;
uniform vec2 uDetailGrid;
varying vec3 vPosition;

vec4 rangeColor(float value, float low, float high, vec4 start, vec4 end) {
  return mix(start, end, clamp((value - low) / (high - low), 0.0, 1.0));
}

vec4 paletteColor(float value) {
  if (value < 1.0) return vec4(77.0,163.0,255.0,40.0 * clamp(value, 0.0, 1.0))/255.0;
  if (value < 5.0) return rangeColor(value, 1.0, 5.0, vec4(77.0,163.0,255.0,40.0)/255.0, vec4(39.0,120.0,232.0,90.0)/255.0);
  if (value < 10.0) return rangeColor(value, 5.0, 10.0, vec4(39.0,120.0,232.0,90.0)/255.0, vec4(0.0,191.0,255.0,135.0)/255.0);
  if (value < 20.0) return rangeColor(value, 10.0, 20.0, vec4(0.0,191.0,255.0,135.0)/255.0, vec4(0.0,181.0,164.0,170.0)/255.0);
  if (value < 40.0) return rangeColor(value, 20.0, 40.0, vec4(0.0,181.0,164.0,170.0)/255.0, vec4(245.0,185.0,66.0,200.0)/255.0);
  if (value < 60.0) return rangeColor(value, 40.0, 60.0, vec4(245.0,185.0,66.0,200.0)/255.0, vec4(255.0,128.0,32.0,220.0)/255.0);
  if (value < 100.0) return rangeColor(value, 60.0, 100.0, vec4(255.0,128.0,32.0,220.0)/255.0, vec4(239.0,60.0,52.0,238.0)/255.0);
  if (value < 150.0) return rangeColor(value, 100.0, 150.0, vec4(239.0,60.0,52.0,238.0)/255.0, vec4(184.0,30.0,45.0,247.0)/255.0);
  if (value < 250.0) return rangeColor(value, 150.0, 250.0, vec4(184.0,30.0,45.0,247.0)/255.0, vec4(122.0,22.0,45.0,252.0)/255.0);
  if (value < 500.0) return rangeColor(value, 250.0, 500.0, vec4(122.0,22.0,45.0,252.0)/255.0, vec4(102.0,18.0,73.0,254.0)/255.0);
  if (value < 1000.0) return rangeColor(value, 500.0, 1000.0, vec4(102.0,18.0,73.0,254.0)/255.0, vec4(82.0,16.0,105.0,255.0)/255.0);
  return vec4(82.0,16.0,105.0,255.0) / 255.0;
}

vec3 scalarSample(sampler2D scalarValues, sampler2D weights, vec2 uv) {
  // Hardware LOD operates on concentration premultiplied by binary validity.
  // Dividing by the filtered validity weight renormalizes partial neighbours.
  float weight = texture2D(weights, uv).r;
  if (weight <= 0.00001) return vec3(-1.0, 0.0, 0.0);
  float concentration = texture2D(scalarValues, uv).r * uConcentrationScale / weight;
  return vec3(concentration, 1.0, clamp(weight, 0.0, 1.0));
}

vec3 srgbToLinear(vec3 value) {
  bvec3 cutoff = lessThanEqual(value, vec3(0.04045));
  return mix(pow((value + 0.055) / 1.055, vec3(2.4)), value / 12.92, vec3(cutoff));
}

void main() {
  vec3 normal = normalize(vPosition);
  float lat = degrees(asin(clamp(normal.y, -1.0, 1.0)));
  float lon = degrees(atan(normal.x, normal.z));
  vec2 uv = vec2((lon - uBounds.x) / (uBounds.z - uBounds.x), (uBounds.w - lat) / (uBounds.w - uBounds.y));
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
  vec3 a = scalarSample(uScalarA, uWeightA, uv);
  vec3 b = scalarSample(uScalarB, uWeightB, uv);
  if (a.y < 0.5 && b.y < 0.5) discard;
  float localMix = clamp(uMix, 0.0, 1.0);
  float value = a.y > 0.5 && b.y > 0.5 ? mix(a.x, b.x, localMix) : a.y > 0.5 ? a.x : b.x;
  float spatialCoverage = a.y > 0.5 && b.y > 0.5 ? mix(a.z, b.z, localMix) : a.y > 0.5 ? a.z : b.z;
  vec4 color = paletteColor(value);
  if (color.a <= 0.0) discard;
  float opacity = uOpacity;
  if (uBaseLayer > 0.5) {
    vec2 tileUv = (floor(clamp(uv, 0.0, 0.999999) * uDetailGrid) + 0.5) / uDetailGrid;
    float detail = texture2D(uDetailMixMap, tileUv).r;
    opacity *= 1.0 - detail;
  }
  color.a *= spatialCoverage * opacity * uLayerOpacity;
  if (color.a <= 0.0) discard;
  gl_FragColor = vec4(srgbToLinear(color.rgb), color.a);
  #include <colorspace_fragment>
}
`;

const legacyFragmentShader = /* glsl */ `
precision highp float;
uniform sampler2D uTexA;
uniform sampler2D uTexB;
uniform float uMix;
uniform float uLayerOpacity;
uniform vec4 uBounds;
varying vec3 vPosition;
void main() {
  vec3 normal = normalize(vPosition);
  float lat = degrees(asin(clamp(normal.y, -1.0, 1.0)));
  float lon = degrees(atan(normal.x, normal.z));
  vec2 uv = vec2((lon - uBounds.x) / (uBounds.z - uBounds.x), (uBounds.w - lat) / (uBounds.w - uBounds.y));
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
  vec4 color = mix(texture2D(uTexA, uv), texture2D(uTexB, uv), uMix);
  color.a *= uLayerOpacity;
  if (color.a <= 0.0) discard;
  gl_FragColor = color;
  #include <colorspace_fragment>
}
`;

function imageTexture(image: ImageSource): Texture {
  const texture = new Texture(image);
  texture.flipY = false;
  texture.generateMipmaps = false;
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.colorSpace = SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

export function LegacyContextSurface({ imageA, imageB, mixRef, opacityRef, onReady }: { imageA: ImageSource; imageB?: ImageSource | null; mixRef: React.MutableRefObject<number>; opacityRef: React.MutableRefObject<number>; onReady: (ready: boolean) => void }) {
  const materialRef = useRef<ShaderMaterial>(null);
  const geometry = useMemo(() => geoBoundsGeometry(CONTEXT_BOUNDS, INSPECT_SHELL_RADIUS), []);
  const textures = useMemo(() => {
    const first = imageTexture(imageA);
    return [first, imageB && imageB !== imageA ? imageTexture(imageB) : first] as const;
  }, [imageA, imageB]);
  const uniforms = useMemo(() => ({
    uTexA: { value: textures[0] }, uTexB: { value: textures[1] }, uMix: { value: mixRef.current }, uLayerOpacity: { value: opacityRef.current },
    uBounds: { value: [CONTEXT_BOUNDS.west, CONTEXT_BOUNDS.south, CONTEXT_BOUNDS.east, CONTEXT_BOUNDS.north] },
  }), [mixRef, opacityRef, textures]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useEffect(() => {
    onReady(true);
    return () => {
      onReady(false);
      textures[0].dispose();
      if (textures[1] !== textures[0]) textures[1].dispose();
    };
  }, [onReady, textures]);
  useFrame(() => {
    if (!materialRef.current) return;
    materialRef.current.uniforms.uMix.value = Math.min(1, mixRef.current);
    materialRef.current.uniforms.uLayerOpacity.value = opacityRef.current;
  });
  return <mesh renderOrder={3} geometry={geometry}><shaderMaterial ref={materialRef} vertexShader={vertexShader} fragmentShader={legacyFragmentShader} uniforms={uniforms} transparent depthWrite={false} /></mesh>;
}

function Surface({ rasterA, rasterB, bounds, radius, mixRef, opacityRef, detailMix, detailMixMap, detailGrid, detailIndex }: { rasterA: ForecastRaster; rasterB: ForecastRaster; bounds: typeof CONTEXT_BOUNDS; radius: number; mixRef: React.MutableRefObject<number>; opacityRef: React.MutableRefObject<number>; detailMix: Float32Array; detailMixMap: DataTexture; detailGrid: DetailGridShape; detailIndex?: number }) {
  const materialRef = useRef<ShaderMaterial>(null);
  const geometry = useMemo(() => geoBoundsGeometry(bounds, radius), [bounds, radius]);
  const uniforms = useMemo(() => ({
    uScalarA: { value: rasterA.scalarTexture }, uScalarB: { value: rasterB.scalarTexture },
    uWeightA: { value: rasterA.weightTexture }, uWeightB: { value: rasterB.weightTexture },
    uMix: { value: mixRef.current },
    uOpacity: { value: detailIndex === undefined ? 1 : detailMix[detailIndex] }, uLayerOpacity: { value: opacityRef.current }, uBaseLayer: { value: detailIndex === undefined ? 1 : 0 },
    uBounds: { value: [bounds.west, bounds.south, bounds.east, bounds.north] },
    uConcentrationScale: { value: rasterA.concentrationScale }, uDetailMixMap: { value: detailMixMap }, uDetailGrid: { value: [detailGrid.columns, detailGrid.rows] },
  }), [bounds, detailGrid.columns, detailGrid.rows, detailIndex, detailMix, detailMixMap, mixRef, opacityRef, rasterA, rasterB]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  useFrame(() => {
    if (!materialRef.current) return;
    materialRef.current.uniforms.uMix.value = mixRef.current;
    materialRef.current.uniforms.uLayerOpacity.value = opacityRef.current;
    if (detailIndex !== undefined) materialRef.current.uniforms.uOpacity.value = detailMix[detailIndex];
  });
  return <mesh renderOrder={detailIndex === undefined ? 3 : 4} geometry={geometry}><shaderMaterial ref={materialRef} vertexShader={vertexShader} fragmentShader={concentrationFragmentShader} uniforms={uniforms} transparent depthWrite={false} /></mesh>;
}

function DetailSurface({ images, detailGrid, mixRef, opacityRef, detailMix, detailMixMap, onReady }: { images: DetailSurfaceImages; detailGrid: DetailGridShape; mixRef: React.MutableRefObject<number>; opacityRef: React.MutableRefObject<number>; detailMix: Float32Array; detailMixMap: DataTexture; onReady: (index: number, ready: boolean) => void }) {
  const rasterA = images.resident.find((source) => source.frameIndex === images.fromIndex)?.raster ?? null;
  const rasterB = images.resident.find((source) => source.frameIndex === images.toIndex)?.raster ?? rasterA;
  const bounds = useMemo(() => detailTileBounds(detailGrid, images.column, images.row), [detailGrid, images.column, images.row]);
  const index = images.tileId;
  useEffect(() => {
    if (!rasterA || !rasterB) return;
    onReady(index, true);
    return () => onReady(index, false);
  }, [index, onReady, rasterA, rasterB]);
  return rasterA && rasterB ? <Surface rasterA={rasterA} rasterB={rasterB} bounds={bounds} radius={1.008} mixRef={mixRef} opacityRef={opacityRef} detailMix={detailMix} detailMixMap={detailMixMap} detailGrid={detailGrid} detailIndex={index} /> : null;
}

export function ContextSurface({ resident, fromIndex, toIndex, details, visibleDetailIds, detailGrid, mixRef, opacityRef, detailEnabled, reducedMotion, onReady, onDetailsHidden }: { resident: ResidentForecastRaster[]; fromIndex: number; toIndex: number; details: DetailSurfaceImages[]; visibleDetailIds: number[]; detailGrid: DetailGridShape; mixRef: React.MutableRefObject<number>; opacityRef: React.MutableRefObject<number>; detailEnabled: boolean; reducedMotion: boolean; onReady: (ready: boolean) => void; onDetailsHidden: () => void }) {
  const rasterA = resident.find((source) => source.frameIndex === fromIndex)?.raster ?? null;
  const rasterB = resident.find((source) => source.frameIndex === toIndex)?.raster ?? rasterA;
  const invalidate = useThree((state) => state.invalidate);
  const gl = useThree((state) => state.gl);
  const detailMix = useMemo(() => new Float32Array(detailGrid.columns * detailGrid.rows), [detailGrid.columns, detailGrid.rows]);
  const detailMixBytes = useMemo(() => new Uint8Array(detailGrid.columns * detailGrid.rows * 4), [detailGrid.columns, detailGrid.rows]);
  const detailMixMap = useMemo(() => {
    const texture = new DataTexture(detailMixBytes, detailGrid.columns, detailGrid.rows, RGBAFormat, UnsignedByteType);
    texture.flipY = false;
    texture.minFilter = NearestFilter;
    texture.magFilter = NearestFilter;
    texture.needsUpdate = true;
    return texture;
  }, [detailGrid.columns, detailGrid.rows, detailMixBytes]);
  const visibleDetailIdSet = useMemo(() => new Set(visibleDetailIds), [visibleDetailIds]);
  const readyDetails = useRef(new Set<number>());
  const hiddenReported = useRef(false);
  const setDetailReady = useCallback((index: number, ready: boolean) => {
    if (ready) readyDetails.current.add(index);
    else readyDetails.current.delete(index);
  }, []);
  useEffect(() => {
    const ready = Boolean(rasterA && rasterB);
    onReady(ready);
    return () => { if (ready) onReady(false); };
  }, [onReady, rasterA, rasterB]);
  useEffect(() => () => detailMixMap.dispose(), [detailMixMap]);
  useFrame((_, delta) => {
    let changed = false;
    let maximum = 0;
    for (let index = 0; index < detailMix.length; index += 1) {
      const target = detailEnabled && readyDetails.current.has(index) && visibleDetailIdSet.has(index) ? 1 : 0;
      const current = detailMix[index];
      const next = advanceDetailMix(current, target, delta, reducedMotion);
      detailMix[index] = next;
      const byte = Math.round(next * 255);
      changed ||= detailMixBytes[index * 4] !== byte;
      detailMixBytes[index * 4] = byte;
      detailMixBytes[index * 4 + 3] = 255;
      maximum = Math.max(maximum, next);
      if (next !== target) invalidate();
    }
    if (changed) detailMixMap.needsUpdate = true;
    gl.domElement.dataset.readyDetailTiles = String(readyDetails.current.size);
    gl.domElement.dataset.detailMaxOpacity = maximum.toFixed(3);
    if (detailEnabled) hiddenReported.current = false;
    else if (maximum === 0 && !hiddenReported.current) {
      hiddenReported.current = true;
      onDetailsHidden();
    }
  });
  return rasterA && rasterB ? <><Surface rasterA={rasterA} rasterB={rasterB} bounds={CONTEXT_BOUNDS} radius={INSPECT_SHELL_RADIUS} mixRef={mixRef} opacityRef={opacityRef} detailMix={detailMix} detailMixMap={detailMixMap} detailGrid={detailGrid} />{details.map((images) => <DetailSurface key={images.tileId} images={images} detailGrid={detailGrid} mixRef={mixRef} opacityRef={opacityRef} detailMix={detailMix} detailMixMap={detailMixMap} onReady={setDetailReady} />)}</> : null;
}
