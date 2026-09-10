"use client";

import type { GeoContext, MapLabelTier, MapPlaceLabel } from "@/data/ui";
import { cameraSafeInsets } from "@/rendering/camera";
import { frontSideOpacity, isScreenPointInSafeViewport, mapLabelLevelOpacity, placeMapLabels, type ScreenLabelCandidate } from "@/rendering/mapLabels";
import { lonLatToVector3 } from "@/rendering/projection";
import { Html, Line } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import {
  AdditiveBlending,
  BackSide,
  Color,
  SRGBColorSpace,
  Texture,
  Vector3,
} from "three";

const earthVertex = /* glsl */ `
varying vec3 vPosition;
varying vec3 vWorldNormal;
varying vec3 vWorldPosition;

void main() {
  vPosition = position;
  vWorldNormal = normalize(mat3(modelMatrix) * normal);
  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorldPosition = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

const earthFragment = /* glsl */ `
precision highp float;
uniform sampler2D uMap;
varying vec3 vPosition;
varying vec3 vWorldNormal;
varying vec3 vWorldPosition;

void main() {
  vec3 normal = normalize(vPosition);
  float lat = asin(clamp(normal.y, -1.0, 1.0));
  float lon = atan(normal.x, normal.z);
  // Three.js flips image textures on upload, so north is at v=1 in shader space.
  vec2 uv = vec2(lon / 6.28318530718 + 0.5, lat / 3.14159265359 + 0.5);
  vec3 base = texture2D(uMap, uv).rgb;
  vec3 lightDirection = normalize(vec3(-0.4, 0.7, 0.55));
  vec3 worldNormal = normalize(vWorldNormal);
  vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
  float facing = clamp(dot(worldNormal, viewDirection), 0.0, 1.0);
  float diffuse = max(dot(worldNormal, lightDirection), 0.0);
  float highlight = pow(facing, 4.0) * 0.055;
  float limb = mix(0.72, 1.0, smoothstep(0.05, 0.48, facing));
  vec3 lit = base * (0.69 + 0.27 * diffuse) + vec3(0.08, 0.13, 0.15) * highlight;
  gl_FragColor = vec4(lit * limb, 1.0);
  #include <colorspace_fragment>
}
`;

const atmosphereVertex = /* glsl */ `
varying vec3 vNormal;
varying vec3 vWorldPosition;

void main() {
  vNormal = normalize(mat3(modelMatrix) * normal);
  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorldPosition = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

const atmosphereFragment = /* glsl */ `
precision highp float;
varying vec3 vNormal;
varying vec3 vWorldPosition;

void main() {
  vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
  float rim = pow(1.0 - abs(dot(normalize(vNormal), viewDirection)), 3.2);
  gl_FragColor = vec4(0.16, 0.58, 0.76, rim * 0.31);
}
`;

function segmentPoints(lines: number[][][], radius: number): Vector3[] {
  const points: Vector3[] = [];
  for (const line of lines) {
    for (let index = 1; index < line.length; index += 1) {
      points.push(
        lonLatToVector3(line[index - 1][0], line[index - 1][1], radius),
        lonLatToVector3(line[index][0], line[index][1], radius),
      );
    }
  }
  return points;
}

type CentralLabel = {
  id: string;
  name: string;
  kind: "country" | MapPlaceLabel["kind"];
  tier: MapLabelTier | null;
  category?: "natural" | "park" | "cultural";
  country?: "CAN" | "USA" | "MEX";
  collisionRank: number;
  position: Vector3;
  normal: Vector3;
};

type LayoutState = {
  world: number[];
  projection: number[];
  width: number;
  height: number;
  compact: boolean;
  fontRevision: number;
};

const LANDMARK_GLYPHS = { natural: "△", park: "●", cultural: "◆" } as const;

function sameLayoutState(left: LayoutState | null, right: LayoutState): boolean {
  return Boolean(left
    && left.width === right.width
    && left.height === right.height
    && left.compact === right.compact
    && left.fontRevision === right.fontRevision
    && left.world.every((value, index) => value === right.world[index])
    && left.projection.every((value, index) => value === right.projection[index]));
}

function MapLabelLayer({ compact, geo }: { compact: boolean; geo: GeoContext }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const elementsRef = useRef(new Map<string, HTMLSpanElement>());
  const removalTimersRef = useRef(new Map<string, number>());
  const measuredWidthsRef = useRef(new Map<string, number>());
  const canvasContextRef = useRef<CanvasRenderingContext2D | null>(null);
  const acceptedIdsRef = useRef(new Set<string>());
  const lastStateRef = useRef<LayoutState | null>(null);
  const fontRevisionRef = useRef(0);
  const layoutRevisionRef = useRef(0);
  const cameraDirection = useMemo(() => new Vector3(), []);
  const projected = useMemo(() => new Vector3(), []);
  const { camera, invalidate, size } = useThree();
  const labels = useMemo<CentralLabel[]>(() => {
    const places = geo.mapLabels.map((label) => {
      const position = lonLatToVector3(label.lon, label.lat, 1.024);
      return {
        ...label,
        position,
        normal: position.clone().normalize(),
      };
    });
    const countries = geo.countryLabels.map((label, index) => {
      const position = lonLatToVector3(label.lon, label.lat, 1.018);
      return {
        id: `country:${label.name}`,
        name: label.name,
        kind: "country" as const,
        tier: null,
        collisionRank: index,
        position,
        normal: position.clone().normalize(),
      };
    });
    return [...places, ...countries];
  }, [geo]);

  useEffect(() => {
    lastStateRef.current = null;
    const canvas = document.createElement("canvas");
    canvasContextRef.current = canvas.getContext("2d");
    let cancelled = false;
    document.fonts?.ready.then(() => {
      if (cancelled) return;
      fontRevisionRef.current += 1;
      measuredWidthsRef.current.clear();
      lastStateRef.current = null;
      invalidate();
    });
    invalidate();
    return () => { cancelled = true; };
  }, [geo, invalidate]);

  useEffect(() => () => {
    removalTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    removalTimersRef.current.clear();
    elementsRef.current.clear();
  }, []);

  const updateLayout = useCallback(() => {
    const overlay = overlayRef.current;
    const context = canvasContextRef.current;
    if (!overlay || !context) return;
    const nextState: LayoutState = {
      world: [...camera.matrixWorld.elements],
      projection: [...camera.projectionMatrix.elements],
      width: size.width,
      height: size.height,
      compact,
      fontRevision: fontRevisionRef.current,
    };
    if (sameLayoutState(lastStateRef.current, nextState)) return;
    lastStateRef.current = nextState;

    const distance = camera.position.length();
    cameraDirection.copy(camera.position).normalize();
    const shortLandscape = size.width > size.height && size.height <= 500;
    const chromeInsets = cameraSafeInsets(size.width, compact, shortLandscape);
    const insets = shortLandscape ? { ...chromeInsets, bottom: Math.max(chromeInsets.bottom, 80) } : chromeInsets;
    const limit = shortLandscape ? 20 : compact ? 24 : 80;
    const padding = compact ? 7 : 4;
    const fontFamily = getComputedStyle(overlay).fontFamily;
    const visible = new Map<string, { label: CentralLabel; opacity: number; x: number; y: number; text: string; fontSize: number }>();
    const candidates: ScreenLabelCandidate[] = [];

    for (const label of labels) {
      const frontOpacity = frontSideOpacity(label.normal.dot(cameraDirection), distance);
      if (frontOpacity <= 0) continue;
      const levelOpacity = label.kind === "country"
        ? Math.max(0, Math.min(1, (distance - 1.5) / 0.28))
        : mapLabelLevelOpacity(label.tier as MapLabelTier, distance, compact);
      const opacity = frontOpacity * levelOpacity;
      if (opacity <= 0) continue;
      projected.copy(label.position).project(camera);
      const x = (projected.x + 1) * size.width / 2;
      const y = (1 - projected.y) * size.height / 2;
      if (!isScreenPointInSafeViewport(x, y, projected.z, size.width, size.height, insets)) continue;
      const glyph = label.kind === "landmark" && label.category ? `${LANDMARK_GLYPHS[label.category]} ` : "";
      const text = `${glyph}${label.name}`;
      const fontSize = compact ? label.kind === "landmark" ? 9 : 10 : label.kind === "landmark" ? 8 : 9;
      const fontWeight = label.kind === "country" ? 700 : 600;
      const font = `${fontWeight} ${fontSize}px ${fontFamily}`;
      const measureKey = `${font}:${text}`;
      let width = measuredWidthsRef.current.get(measureKey);
      if (width === undefined) {
        context.font = font;
        width = Math.ceil(context.measureText(text).width) + (label.kind === "city" ? 8 : 2);
        measuredWidthsRef.current.set(measureKey, width);
      }
      const tierPriority = label.tier === "overview" ? 0 : label.kind === "country" ? 1 : 2;
      candidates.push({
        id: label.id,
        x,
        y,
        width,
        height: fontSize + 4,
        semanticPriority: [tierPriority, label.collisionRank],
        countsTowardLimit: label.kind !== "country",
      });
      visible.set(label.id, { label, opacity, x, y, text, fontSize });
    }

    const accepted = placeMapLabels(candidates, {
      padding,
      limit,
      previouslyAccepted: acceptedIdsRef.current,
    });
    const acceptedIds = new Set(accepted.map((label) => label.id));
    acceptedIdsRef.current = acceptedIds;

    for (const rect of accepted) {
      const item = visible.get(rect.id);
      if (!item) continue;
      const pendingRemoval = removalTimersRef.current.get(rect.id);
      if (pendingRemoval !== undefined) {
        window.clearTimeout(pendingRemoval);
        removalTimersRef.current.delete(rect.id);
      }
      let element = elementsRef.current.get(rect.id);
      if (!element) {
        element = document.createElement("span");
        element.ariaHidden = "true";
        element.className = `map-label ${item.label.kind}${item.label.tier ? ` tier-${item.label.tier}` : ""}${item.label.category ? ` category-${item.label.category}` : ""}`;
        element.dataset.labelId = rect.id;
        element.dataset.labelName = item.label.name;
        element.dataset.labelKind = item.label.kind;
        if (item.label.country) element.dataset.labelCountry = item.label.country;
        if (item.label.tier) element.dataset.labelTier = item.label.tier;
        if (item.label.category) element.dataset.labelCategory = item.label.category;
        if (item.label.kind === "country") element.dataset.testid = "country-label";
        element.textContent = item.text;
        element.style.opacity = "0";
        overlay.appendChild(element);
        elementsRef.current.set(rect.id, element);
      }
      element.style.left = `${item.x.toFixed(2)}px`;
      element.style.top = `${item.y.toFixed(2)}px`;
      element.style.fontSize = `${item.fontSize}px`;
      element.style.opacity = item.opacity.toFixed(3);
      element.style.transform = `translate(-50%, calc(-50% + ${(1 - item.opacity) * 3}px))`;
    }

    elementsRef.current.forEach((element, id) => {
      if (acceptedIds.has(id) || removalTimersRef.current.has(id)) return;
      element.style.opacity = "0";
      const timer = window.setTimeout(() => {
        elementsRef.current.get(id)?.remove();
        elementsRef.current.delete(id);
        removalTimersRef.current.delete(id);
      }, 180);
      removalTimersRef.current.set(id, timer);
    });

    layoutRevisionRef.current += 1;
    overlay.dataset.labelCatalogCount = String(geo.mapLabels.length);
    overlay.dataset.labelCandidateCount = String(candidates.length);
    overlay.dataset.labelAcceptedCount = String(accepted.length);
    overlay.dataset.labelLayoutRevision = String(layoutRevisionRef.current);
    overlay.dataset.visibleLabelIds = JSON.stringify(accepted.map((label) => label.id));
  }, [camera, cameraDirection, compact, geo.mapLabels.length, labels, projected, size.height, size.width]);

  useFrame(() => updateLayout());

  return (
    <Html fullscreen zIndexRange={[4, 1]} style={{ pointerEvents: "none" }}>
      <div ref={overlayRef} aria-hidden="true" className="map-label-layer" data-testid="map-label-layer" />
    </Html>
  );
}

export const EarthGlobe = memo(function EarthGlobe({ mobile, geo, image, onReady }: { mobile: boolean; geo: GeoContext | null; image: ImageBitmap | HTMLImageElement | null; onReady: () => void }) {
  const { gl, invalidate } = useThree();
  const texture = useMemo(() => {
    if (!image) return null;
    const value = new Texture(image);
    value.colorSpace = SRGBColorSpace;
    value.anisotropy = gl.capabilities.getMaxAnisotropy();
    value.needsUpdate = true;
    return value;
  }, [gl, image]);
  useEffect(() => () => texture?.dispose(), [texture]);

  useEffect(() => {
    if (!geo || !texture) return;
    invalidate();
    onReady();
  }, [geo, invalidate, onReady, texture]);

  const coastlines = useMemo(() => segmentPoints(geo?.coastlines ?? [], 1.008), [geo]);
  const countryBorders = useMemo(() => segmentPoints(geo?.countryBorders ?? [], 1.011), [geo]);
  const regionBorders = useMemo(() => segmentPoints(geo?.regionBorders ?? [], 1.009), [geo]);

  if (!texture) return null;
  return (
    <group>
      <mesh renderOrder={0}>
        <sphereGeometry args={[1, 128, 72]} />
        <shaderMaterial
          vertexShader={earthVertex}
          fragmentShader={earthFragment}
          uniforms={{ uMap: { value: texture } }}
        />
      </mesh>
      <mesh renderOrder={5}>
        <sphereGeometry args={[1.045, 96, 64]} />
        <shaderMaterial
          vertexShader={atmosphereVertex}
          fragmentShader={atmosphereFragment}
          transparent
          depthWrite={false}
          side={BackSide}
          blending={AdditiveBlending}
        />
      </mesh>
      {coastlines.length > 0 ? (
        <Line points={coastlines} segments color={new Color("#94adb4")} lineWidth={0.75} transparent opacity={0.5} />
      ) : null}
      {regionBorders.length > 0 ? (
        <Line points={regionBorders} segments color={new Color("#8ca0a6")} lineWidth={0.5} transparent opacity={0.12} />
      ) : null}
      {countryBorders.length > 0 ? (
        <Line points={countryBorders} segments color={new Color("#b5c6ca")} lineWidth={0.85} transparent opacity={0.42} />
      ) : null}
      {geo ? <MapLabelLayer compact={mobile} geo={geo} /> : null}
    </group>
  );
});
