"use client";

import { airIndexColor, airRelativeSeverity, monitorIndexSystem } from "@/data/airQuality";
import type { AirQualityMonitor, FireIncident } from "@/data/contextSchema";
import { airClusterIdentity, airHoverLabel, airMarkerShape, clusterProjectedAirMonitors, compareAirMonitors, type AirHoverLabel, type AirMonitorCluster, type ProjectedAirMonitor } from "@/rendering/airClusters";
import { clusterProjectedIncidents, compareIncidents, incidentClusterIdentity, incidentHoverLabel, type IncidentCluster, type ProjectedIncident } from "@/rendering/incidentClusters";
import { placeHoverLabel } from "@/rendering/hoverLabelLayout";
import { lonLatToVector3 } from "@/rendering/projection";
import { airMarkerPointSize, markerEmphasis } from "@/rendering/markerVisuals";
import { Html } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import { Color, Vector3 } from "three";
import type { Camera, Intersection, Object3D, Vector2 } from "three";

const candidate = new Vector3();
const hoverPoint = new Vector3();
const publishClusterScreens = process.env.NODE_ENV !== "production" || process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1";

function closestIndex(hits: readonly Intersection[], object: Object3D, positions: Float32Array, camera: Camera, pointer: Vector2, viewport: { width: number; height: number }, maxDistancePx: number): number | null {
  let closest: number | null = null;
  let distance = maxDistancePx * maxDistancePx;
  for (const hit of hits) {
    if (hit.object !== object || hit.index == null) continue;
    candidate.fromArray(positions, hit.index * 3);
    if (candidate.dot(camera.position) <= candidate.lengthSq()) continue;
    candidate.project(camera);
    const dx = (candidate.x - pointer.x) * viewport.width / 2;
    const dy = (candidate.y - pointer.y) * viewport.height / 2;
    const next = dx * dx + dy * dy;
    if (next <= distance) { closest = hit.index; distance = next; }
  }
  return closest;
}

const vertexShader = /* glsl */ `
attribute float aSize;
attribute float aShape;
attribute float aEmphasis;
attribute vec3 aColor;
uniform float uPixelRatio;
varying float vShape;
varying vec3 vColor;
varying float vEmphasis;
void main() {
  vShape = aShape;
  vColor = aColor;
  vEmphasis = aEmphasis;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = clamp(aSize * uPixelRatio, 8.0, 22.0);
}
`;

const fragmentShader = /* glsl */ `
precision highp float;
varying float vShape;
varying vec3 vColor;
varying float vEmphasis;
void main() {
  vec2 p = gl_PointCoord - vec2(0.5);
  float radial = length(p);
  float diamond = abs(p.x) + abs(p.y);
  float distanceToEdge = vShape < 0.5 || vShape > 1.5 ? radial : diamond;
  if (radial > 0.5) discard;
  if (vShape > 1.5 && radial < 0.24) discard;
  float core = 1.0 - smoothstep(0.39, 0.44, distanceToEdge);
  float halo = (1.0 - smoothstep(0.42, 0.5, radial)) * (0.2 + vEmphasis * 0.22);
  float border = smoothstep(0.31, 0.39, distanceToEdge);
  vec3 color = mix(vColor, vec3(0.88, 0.95, 0.98), border);
  float alpha = max(core, halo);
  gl_FragColor = vec4(color, alpha);
  #include <colorspace_fragment>
}
`;

function projectToScreen(positions: Float32Array, index: number, camera: Camera, viewport: { width: number; height: number }) {
  hoverPoint.fromArray(positions, index * 3).project(camera);
  return { x: (hoverPoint.x + 1) * viewport.width / 2, y: (1 - hoverPoint.y) * viewport.height / 2 };
}

function HoverLabelOverlay({ positions, index, label }: { positions: Float32Array; index: number | null; label: AirHoverLabel | null }) {
  const { camera, size } = useThree();
  const layerRef = useRef<HTMLDivElement>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  const publish = useRef(0);
  useFrame(() => {
    const overlay = layerRef.current;
    if (!overlay) return;
    publish.current += 1;
    if (index == null) {
      if (!publishClusterScreens) return;
      if (publish.current !== 1 && publish.current % 8 !== 0) return;
      const screens = Array.from({ length: positions.length / 3 }, (_, point) => projectToScreen(positions, point, camera, size));
      overlay.dataset.clusterScreens = JSON.stringify(screens);
      return;
    }
    const element = labelRef.current;
    if (!element) return;
    const screen = projectToScreen(positions, index, camera, size);
    const { left, top } = placeHoverLabel(screen.x, screen.y, element.offsetWidth, element.offsetHeight, size.width, size.height);
    element.style.left = `${left.toFixed(2)}px`;
    element.style.top = `${top.toFixed(2)}px`;
    element.dataset.ready = "true";
  });
  return (
    <Html fullscreen zIndexRange={[12, 8]} style={{ pointerEvents: "none" }}>
      <div ref={layerRef} className="map-hover-layer" aria-hidden="true" data-testid="map-hover-layer">
        {index != null && label ? <div ref={labelRef} className="map-hover-label" data-testid="map-hover-label"><strong>{label.title}</strong><small>{label.detail}</small></div> : null}
      </div>
    </Html>
  );
}

function PointCloud({ positions, sizes, colors, shapes, emphasis, onPick, pickRadius, hoverLabels, hoverEnabled = false, suppressHover = false }: { positions: Float32Array; sizes: Float32Array; colors: Float32Array; shapes: Float32Array; emphasis?: Float32Array; onPick: (index: number) => void; pickRadius: number; hoverLabels?: AirHoverLabel[]; hoverEnabled?: boolean; suppressHover?: boolean }) {
  const { camera, gl, invalidate, size } = useThree();
  const [hovered, setHovered] = useState<number | null>(null);
  const pick = (event: ThreeEvent<PointerEvent | MouseEvent>) => closestIndex(event.intersections, event.object, positions, camera, event.pointer, size, pickRadius);
  const displayedEmphasis = useMemo(() => {
    const values = emphasis?.slice() ?? new Float32Array(sizes.length);
    if (hovered != null && hovered < values.length) values[hovered] = markerEmphasis({ selected: values[hovered] >= 1, hovered: true });
    return values;
  }, [emphasis, hovered, sizes.length]);
  const hoverLabel = hovered == null ? null : hoverLabels?.[hovered] ?? null;
  useEffect(() => {
    if (!suppressHover) return;
    setHovered(null);
    document.body.style.cursor = "default";
  }, [suppressHover]);
  useEffect(() => {
    setHovered(null);
    document.body.style.cursor = "default";
  }, [hoverEnabled, positions]);
  useEffect(() => invalidate(), [invalidate, positions]);
  useEffect(() => () => { document.body.style.cursor = "default"; }, []);
  if (!sizes.length) return null;
  return <><points
    renderOrder={8}
    onClick={(event) => { const index = pick(event); if (index == null) return; event.stopPropagation(); onPick(index); }}
    onPointerMove={(event) => { const index = pick(event); document.body.style.cursor = index == null ? "default" : "pointer"; if (hoverEnabled && !suppressHover) setHovered((current) => current === index ? current : index); }}
    onPointerOut={() => { document.body.style.cursor = "default"; setHovered(null); }}
  >
    <bufferGeometry>
      <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      <bufferAttribute attach="attributes-aSize" args={[sizes, 1]} />
      <bufferAttribute attach="attributes-aColor" args={[colors, 3]} />
      <bufferAttribute attach="attributes-aShape" args={[shapes, 1]} />
      <bufferAttribute attach="attributes-aEmphasis" args={[displayedEmphasis, 1]} />
    </bufferGeometry>
    <shaderMaterial vertexShader={vertexShader} fragmentShader={fragmentShader} transparent depthWrite={false} uniforms={{ uPixelRatio: { value: Math.min(1.5, gl.getPixelRatio()) } }} />
  </points>{hoverEnabled && !suppressHover ? <HoverLabelOverlay positions={positions} index={hovered} label={hoverLabel} /> : null}</>;
}

export function AirContextPoints({ items, mobile, selectedMonitorIds, suppressHover, onSelect }: { items: AirQualityMonitor[]; mobile: boolean; selectedMonitorIds: string[]; suppressHover: boolean; onSelect: (items: AirQualityMonitor[]) => void }) {
  const { camera, size } = useThree();
  const touch = mobile || (typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches);
  const prepared = useMemo(() => [...items].sort(compareAirMonitors).map((monitor) => {
    const world = lonLatToVector3(monitor.lon, monitor.lat, 1.02);
    return { world, lengthSquared: world.lengthSq(), projected: { monitor, x: 0, y: 0 } };
  }), [items]);
  const projectedRef = useRef<ProjectedAirMonitor[]>([]);
  const screenPoint = useMemo(() => new Vector3(), []);
  const [memberships, setMemberships] = useState<string[][]>([]);
  const identity = useRef("");
  useFrame(() => {
    const startedAt = process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1" ? performance.now() : 0;
    const projected = projectedRef.current;
    projected.length = 0;
    for (const item of prepared) {
      const point = item.world;
      if (point.x * camera.position.x + point.y * camera.position.y + point.z * camera.position.z <= item.lengthSquared) continue;
      screenPoint.copy(point).project(camera);
      if (Math.abs(screenPoint.x) > 1.1 || Math.abs(screenPoint.y) > 1.1 || screenPoint.z > 1) continue;
      item.projected.x = (screenPoint.x + 1) * size.width / 2;
      item.projected.y = (1 - screenPoint.y) * size.height / 2;
      projected.push(item.projected);
    }
    const next = clusterProjectedAirMonitors(projected, touch ? 44 : 40, true);
    if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
      const target = globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> };
      const previous = (target.__TITANSKIES_PERF__?.air ?? {}) as Record<string, number>;
      const duration = performance.now() - startedAt;
      target.__TITANSKIES_PERF__ = {
        ...target.__TITANSKIES_PERF__,
        air: {
          monitorCount: items.length,
          clusterCount: next.length,
          passes: Number(previous.passes ?? 0) + 1,
          durationMs: Number(previous.durationMs ?? 0) + duration,
          maxDurationMs: Math.max(Number(previous.maxDurationMs ?? 0), duration),
        },
      };
    }
    const nextIdentity = airClusterIdentity(next);
    if (nextIdentity !== identity.current) {
      identity.current = nextIdentity;
      setMemberships(next.map((cluster) => cluster.monitors.map((monitor) => monitor.id)));
    }
  });
  const clusters = useMemo<AirMonitorCluster[]>(() => {
    const byId = new Map(items.map((monitor) => [monitor.id, monitor]));
    return memberships.flatMap((ids) => {
      const monitors = ids.flatMap((id) => byId.get(id) ?? []).sort(compareAirMonitors);
      const representative = monitors[0];
      if (!representative) return [];
      return [{
        representative,
        monitors,
        x: 0,
        y: 0,
        mixed: monitors.some((monitor) => monitorIndexSystem(monitor) !== monitorIndexSystem(representative)),
      }];
    });
  }, [items, memberships]);
  const attributes = useMemo(() => {
    const positions = new Float32Array(clusters.length * 3);
    const sizes = new Float32Array(clusters.length);
    const colors = new Float32Array(clusters.length * 3);
    const shapes = new Float32Array(clusters.length);
    const emphasis = new Float32Array(clusters.length);
    const selected = new Set(selectedMonitorIds);
    const hoverLabels: AirHoverLabel[] = [];
    clusters.forEach((cluster, index) => {
      const monitor = cluster.representative;
      lonLatToVector3(monitor.lon, monitor.lat, 1.02).toArray(positions, index * 3);
      const isSelected = cluster.monitors.some((item) => selected.has(item.id));
      sizes[index] = airMarkerPointSize(airRelativeSeverity(monitor), cluster.monitors.length, { selected: isSelected, hovered: false });
      emphasis[index] = markerEmphasis({ selected: isSelected, hovered: false });
      new Color(airIndexColor(monitor)).toArray(colors, index * 3);
      shapes[index] = airMarkerShape(cluster) === "ring" ? 2 : airMarkerShape(cluster) === "diamond" ? 1 : 0;
      hoverLabels.push(airHoverLabel(cluster));
    });
    return { positions, sizes, colors, shapes, emphasis, hoverLabels };
  }, [clusters, selectedMonitorIds]);
  return <PointCloud {...attributes} hoverEnabled={!touch} suppressHover={suppressHover} pickRadius={touch ? 22 : 16} onPick={(index) => onSelect(clusters[index].monitors)} />;
}

export function IncidentContextPoints({ items, mobile, selectedIncidentIds, suppressHover, onSelect }: { items: FireIncident[]; mobile: boolean; selectedIncidentIds: string[]; suppressHover: boolean; onSelect: (items: FireIncident[]) => void }) {
  const { camera, size } = useThree();
  const touch = mobile || (typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches);
  const prepared = useMemo(() => [...items].sort(compareIncidents).map((incident) => {
    const world = lonLatToVector3(incident.lon, incident.lat, 1.024);
    return { world, lengthSquared: world.lengthSq(), projected: { incident, x: 0, y: 0 } };
  }), [items]);
  const projectedRef = useRef<ProjectedIncident[]>([]);
  const screenPoint = useMemo(() => new Vector3(), []);
  const [memberships, setMemberships] = useState<string[][]>([]);
  const identity = useRef("");
  useFrame(() => {
    const startedAt = process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1" ? performance.now() : 0;
    const projected = projectedRef.current;
    projected.length = 0;
    for (const item of prepared) {
      const point = item.world;
      if (point.x * camera.position.x + point.y * camera.position.y + point.z * camera.position.z <= item.lengthSquared) continue;
      screenPoint.copy(point).project(camera);
      if (Math.abs(screenPoint.x) > 1.1 || Math.abs(screenPoint.y) > 1.1 || screenPoint.z > 1) continue;
      item.projected.x = (screenPoint.x + 1) * size.width / 2;
      item.projected.y = (1 - screenPoint.y) * size.height / 2;
      projected.push(item.projected);
    }
    const next = clusterProjectedIncidents(projected, touch ? 44 : 40, true);
    if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
      const target = globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> };
      const previous = (target.__TITANSKIES_PERF__?.incidents ?? {}) as Record<string, number>;
      const duration = performance.now() - startedAt;
      target.__TITANSKIES_PERF__ = {
        ...target.__TITANSKIES_PERF__,
        incidents: {
          incidentCount: items.length,
          clusterCount: next.length,
          passes: Number(previous.passes ?? 0) + 1,
          durationMs: Number(previous.durationMs ?? 0) + duration,
          maxDurationMs: Math.max(Number(previous.maxDurationMs ?? 0), duration),
        },
      };
    }
    const nextIdentity = incidentClusterIdentity(next);
    if (nextIdentity !== identity.current) {
      identity.current = nextIdentity;
      setMemberships(next.map((cluster) => cluster.incidents.map((incident) => incident.id)));
    }
  });
  const preparedById = useMemo(() => new Map(prepared.map((item) => [item.projected.incident.id, item])), [prepared]);
  const clusters = useMemo<IncidentCluster[]>(() => memberships.flatMap((ids) => {
    const incidents = ids.flatMap((id) => preparedById.get(id)?.projected.incident ?? []).sort(compareIncidents);
    const representative = incidents[0];
    return representative ? [{ representative, incidents, x: 0, y: 0 }] : [];
  }), [memberships, preparedById]);
  const attributes = useMemo(() => {
    const positions = new Float32Array(clusters.length * 3);
    const sizes = new Float32Array(clusters.length);
    const colors = new Float32Array(clusters.length * 3);
    const shapes = new Float32Array(clusters.length).fill(1);
    const emphasis = new Float32Array(clusters.length);
    const selected = new Set(selectedIncidentIds);
    const hoverLabels: AirHoverLabel[] = [];
    clusters.forEach((cluster, index) => {
      const incident = cluster.representative;
      preparedById.get(incident.id)?.world.toArray(positions, index * 3);
      const isSelected = cluster.incidents.some((item) => selected.has(item.id));
      sizes[index] = 8 + Math.min(4, Math.log2(cluster.incidents.length + 1)) + (cluster.incidents.length === 1 ? Math.min(2, Math.sqrt(incident.areaHectares ?? 0) / 60) : 0);
      emphasis[index] = markerEmphasis({ selected: isSelected, hovered: false });
      new Color("#ffc45d").toArray(colors, index * 3);
      hoverLabels.push(incidentHoverLabel(cluster));
    });
    return { positions, sizes, colors, shapes, emphasis, hoverLabels };
  }, [clusters, preparedById, selectedIncidentIds]);
  return <PointCloud {...attributes} hoverEnabled={!touch} suppressHover={suppressHover} pickRadius={touch ? 24 : 20} onPick={(index) => onSelect(clusters[index].incidents)} />;
}
