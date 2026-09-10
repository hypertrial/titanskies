"use client";

import { DISPLAY_BOUNDS, inDisplayBounds, type AirQualityMonitor, type DetailGridShape, type FireIncident } from "@/data/contextSchema";
import type { GeoContext, LayerVisibility, MapSelection } from "@/data/ui";
import { INSPECT_SHELL_RADIUS, lonLatToVector3, projectToScreen, vector3ToLonLat } from "@/rendering/projection";
import { inspectLabelInsets, placeHoverLabel } from "@/rendering/hoverLabelLayout";
import { keyboardInspectSelection, shouldInspectMapClick } from "@/data/inspectPoint";
import { inspectPointerFromEvent, isInspectClick, type InspectPointer, type InspectPointerEvent } from "@/rendering/inspectClick";
import { adaptiveDpr, cameraGestureProfile, CAMERA_FOV, cameraSafeInsets, fitOverviewDistance, locationCameraPosition, MIN_CAMERA_DISTANCE, overviewResetDistance, visibleDetailTiles } from "@/rendering/camera";
import { Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Color, MathUtils, Vector3 } from "three";
import type { Camera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { AirContextPoints, IncidentContextPoints } from "./ContextPoints";
import { ContextSurface, LegacyContextSurface } from "./ContextSurface";
import type { ResidentForecastRaster } from "@/rendering/forecastRasterCache";
import { EarthGlobe } from "./EarthGlobe";

const CAMERA_LON = -100;
const CAMERA_LAT = 52;
const initialCamera = () => lonLatToVector3(CAMERA_LON, CAMERA_LAT, 3).toArray();
const radians = (degrees: number) => degrees * Math.PI / 180;

function syncInspectLabel(
  element: HTMLDivElement,
  position: Vector3,
  camera: Camera,
  viewport: { width: number; height: number },
  insets: ReturnType<typeof inspectLabelInsets>,
) {
  const facing = position.dot(camera.position) > position.lengthSq();
  element.hidden = !facing;
  if (!facing) return;
  const screen = projectToScreen(position, camera, viewport);
  const { left, top } = placeHoverLabel(screen.x, screen.y, element.offsetWidth, element.offsetHeight, viewport.width, viewport.height, insets);
  element.style.left = `${left.toFixed(2)}px`;
  element.style.top = `${top.toFixed(2)}px`;
  element.dataset.ready = "true";
}

function syncProjectedPoint(element: HTMLElement, position: Vector3, camera: Camera, viewport: { width: number; height: number }) {
  const facing = position.dot(camera.position) > position.lengthSq();
  element.hidden = !facing;
  if (!facing) return;
  const screen = projectToScreen(position, camera, viewport);
  element.style.left = `${screen.x.toFixed(2)}px`;
  element.style.top = `${screen.y.toFixed(2)}px`;
  element.dataset.ready = "true";
}

function GlobeCursorOverlay({ position, kind }: { position: Vector3; kind: "cursor" | "pin" }) {
  const { camera, size, invalidate } = useThree();
  const cursorRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = cursorRef.current;
    if (element) syncProjectedPoint(element, position, camera, size);
    invalidate();
  }, [camera, invalidate, position, size]);
  useFrame(() => {
    const element = cursorRef.current;
    if (element) syncProjectedPoint(element, position, camera, size);
  });
  return (
    <Html fullscreen zIndexRange={[5, 1]} style={{ pointerEvents: "none" }}>
      <div ref={cursorRef} className="globe-cursor" data-kind={kind} data-testid={kind === "cursor" ? "keyboard-cursor" : "inspect-pin"} />
    </Html>
  );
}

function InspectLabelOverlay({ position, title, value, mobile, compactLabels }: { position: Vector3; title: string; value: string; mobile: boolean; compactLabels: boolean }) {
  const { camera, size, invalidate } = useThree();
  const labelRef = useRef<HTMLDivElement>(null);
  const insets = useMemo(() => inspectLabelInsets(size.width, mobile, compactLabels), [compactLabels, mobile, size.width]);
  useLayoutEffect(() => {
    const element = labelRef.current;
    if (element) syncInspectLabel(element, position, camera, size, insets);
    invalidate();
  }, [camera, insets, invalidate, position, size, title, value]);
  useFrame(() => {
    const element = labelRef.current;
    if (element) syncInspectLabel(element, position, camera, size, insets);
  });
  return (
    <Html fullscreen zIndexRange={[6, 2]} style={{ pointerEvents: "none" }}>
      <div ref={labelRef} className="inspect-marker-label" data-testid="inspect-marker">
        <small className="sr-only">{title}</small>
        <strong data-testid="inspect-marker-value" aria-live="polite">{value}</strong>
      </div>
    </Html>
  );
}

function SelectionMarker({ lon, lat, label, mobile, compactLabels }: { lon: number; lat: number; label?: { title: string; value: string } | null; mobile: boolean; compactLabels: boolean }) {
  const position = useMemo(() => lonLatToVector3(lon, lat, INSPECT_SHELL_RADIUS), [lat, lon]);
  return <>
    <GlobeCursorOverlay position={position} kind="pin" />
    {label ? <InspectLabelOverlay position={position} title={label.title} value={label.value} mobile={mobile} compactLabels={compactLabels} /> : null}
  </>;
}

type SmokeSceneProps = {
  layers: LayerVisibility;
  geo: GeoContext | null;
  mobile: boolean;
  compactLabels: boolean;
  monitors: AirQualityMonitor[];
  selectedMonitorIds: string[];
  incidents: FireIncident[];
  selectedIncidentIds: string[];
  contextImageA: ImageBitmap | HTMLImageElement | null;
  contextImageB: ImageBitmap | HTMLImageElement | null;
  legacyForecast: boolean;
  residentRasters: ResidentForecastRaster[];
  detailImages: import("./ContextSurface").DetailSurfaceImages[];
  detailTileIds: number[];
  detailGrid: DetailGridShape;
  renderedFromIndex: number;
  renderedToIndex: number;
  contextMixRef: React.MutableRefObject<number>;
  smokeOpacityRef: React.MutableRefObject<number>;
  renderRequestRef: React.MutableRefObject<() => void>;
  renderMix: number;
  renderOpacity: number;
  playing: boolean;
  detailPlaybackMode: "visible" | "fading-out" | "base-only";
  reducedMotion: boolean;
  resetSignal: number;
  focusLocation: { lon: number; lat: number; requestId: number } | null;
  inspectLocation: { lon: number; lat: number } | null;
  inspectLabel: { title: string; value: string } | null;
  onReady: () => void;
  onLayerReady: (ready: boolean) => void;
  onDetailsHidden: () => void;
  onPairCommitted: (fromIndex: number, toIndex: number) => void;
  onSelect: (selection: MapSelection | null) => void;
  onMapClick: (payload: { lon: number; lat: number }) => void;
  onDetailTiles: (tiles: number[]) => void;
};

function CameraControls({ resetSignal, focusLocation, zoomSignal, controlsRef, mobile, reducedMotion, detailGrid, onInteraction, onDetailTiles }: { resetSignal: number; focusLocation: { lon: number; lat: number; requestId: number } | null; zoomSignal: number; controlsRef: React.RefObject<OrbitControlsImpl | null>; mobile: boolean; reducedMotion: boolean; detailGrid: DetailGridShape; onInteraction: (active: boolean) => void; onDetailTiles: (tiles: number[]) => void }) {
  const { camera, gl, size, invalidate } = useThree();
  const shortLandscape = size.width > size.height && size.height <= 500;
  const overview = useMemo(() => fitOverviewDistance(size.width, size.height, CAMERA_FOV, cameraSafeInsets(size.width, mobile, shortLandscape)), [mobile, shortLandscape, size.height, size.width]);
  const overviewRef = useRef(overview);
  overviewRef.current = overview;
  const shortLandscapeRef = useRef(shortLandscape);
  shortLandscapeRef.current = shortLandscape;
  const reset = useRef<{ from: Vector3; to: Vector3; elapsed: number } | null>(null);
  const initialized = useRef(false);
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;
  const syncCamera = useCallback(() => {
    const distance = camera.position.length();
    const profile = cameraGestureProfile(distance, shortLandscapeRef.current);
    const controls = controlsRef.current;
    if (controls) {
      controls.zoomSpeed = profile.zoomSpeed;
      controls.rotateSpeed = profile.rotateSpeed;
      controls.dampingFactor = profile.dampingFactor;
    }
    const root = gl.domElement.closest<HTMLElement>('[data-testid="interactive-globe"]');
    if (root) {
      root.dataset.cameraDistance = distance.toFixed(4);
      root.dataset.cameraZoomSpeed = profile.zoomSpeed.toFixed(4);
      root.dataset.cameraRotateSpeed = profile.rotateSpeed.toFixed(4);
      root.dataset.cameraDampingFactor = profile.dampingFactor.toFixed(4);
    }
    onDetailTiles(distance < 1.8 ? visibleDetailTiles(camera, detailGrid) : []);
  }, [camera, controlsRef, detailGrid, gl, onDetailTiles]);

  useEffect(() => {
    const to = lonLatToVector3(CAMERA_LON, CAMERA_LAT, overviewResetDistance(overviewRef.current, shortLandscapeRef.current));
    if (!initialized.current || reducedMotionRef.current) camera.position.copy(to);
    else reset.current = { from: camera.position.clone(), to, elapsed: 0 };
    initialized.current = true;
    controlsRef.current?.target.set(0, 0, 0);
    controlsRef.current?.update();
    syncCamera();
    invalidate();
  }, [camera, controlsRef, invalidate, resetSignal, syncCamera]);

  useEffect(() => {
    syncCamera();
    invalidate();
  }, [invalidate, shortLandscape, syncCamera]);

  useEffect(() => {
    if (!focusLocation || !initialized.current) return;
    const to = locationCameraPosition(focusLocation.lon, focusLocation.lat);
    if (reducedMotionRef.current) camera.position.copy(to);
    else reset.current = { from: camera.position.clone(), to, elapsed: 0 };
    controlsRef.current?.target.set(0, 0, 0);
    controlsRef.current?.update();
    syncCamera();
    invalidate();
  }, [camera, controlsRef, focusLocation, invalidate, syncCamera]);

  useEffect(() => {
    if (!initialized.current || camera.position.length() <= overview) return;
    camera.position.setLength(overview);
    controlsRef.current?.update();
    syncCamera();
    invalidate();
  }, [camera, controlsRef, invalidate, overview, syncCamera]);

  useEffect(() => {
    if (!zoomSignal) return;
    const factor = cameraGestureProfile(camera.position.length(), shortLandscape).keyboardZoomFactor;
    camera.position.setLength(MathUtils.clamp(camera.position.length() / (zoomSignal > 0 ? factor : 1 / factor), MIN_CAMERA_DISTANCE, overview));
    controlsRef.current?.update();
    syncCamera();
    invalidate();
  }, [camera, controlsRef, invalidate, overview, shortLandscape, syncCamera, zoomSignal]);

  useFrame((_, delta) => {
    if (!reset.current) return;
    reset.current.elapsed += delta;
    const t = Math.min(1, reset.current.elapsed / 0.35);
    const eased = 1 - (1 - t) ** 3;
    camera.position.lerpVectors(reset.current.from, reset.current.to, eased);
    controlsRef.current?.update();
    syncCamera();
    if (t >= 1) reset.current = null;
    else invalidate();
  });

  return (
    <OrbitControls
      ref={controlsRef}
      enablePan={false}
      enableDamping
      dampingFactor={cameraGestureProfile(camera.position.length(), shortLandscape).dampingFactor}
      rotateSpeed={cameraGestureProfile(camera.position.length(), shortLandscape).rotateSpeed}
      zoomSpeed={cameraGestureProfile(camera.position.length(), shortLandscape).zoomSpeed}
      target={[0, 0, 0]}
      minDistance={MIN_CAMERA_DISTANCE}
      maxDistance={overview}
      minAzimuthAngle={radians(DISPLAY_BOUNDS.west)}
      maxAzimuthAngle={radians(DISPLAY_BOUNDS.east)}
      minPolarAngle={radians(6)}
      maxPolarAngle={radians(80)}
      onStart={() => onInteraction(true)}
      onEnd={() => { onInteraction(false); syncCamera(); }}
      onChange={() => { syncCamera(); invalidate(); }}
    />
  );
}

function ForecastFrame({ mix, opacity, mixRef, fromIndex, toIndex, onPairCommitted }: { mix: number; opacity: number; mixRef: React.MutableRefObject<number>; fromIndex: number; toIndex: number; onPairCommitted: (fromIndex: number, toIndex: number) => void }) {
  const { gl, invalidate } = useThree();
  useLayoutEffect(() => onPairCommitted(fromIndex, toIndex), [fromIndex, onPairCommitted, toIndex]);
  useEffect(() => invalidate(), [invalidate, mix, opacity]);
  useFrame(() => {
    if (process.env.NODE_ENV !== "production") gl.domElement.dataset.renderedForecastMix = mixRef.current.toFixed(6);
  });
  return null;
}

export function SmokeScene({
  layers,
  geo,
  mobile,
  compactLabels,
  monitors,
  selectedMonitorIds,
  incidents,
  selectedIncidentIds,
  contextImageA,
  contextImageB,
  legacyForecast,
  residentRasters,
  detailImages,
  detailTileIds,
  detailGrid,
  renderedFromIndex,
  renderedToIndex,
  contextMixRef,
  smokeOpacityRef,
  renderRequestRef,
  renderMix,
  renderOpacity,
  playing,
  detailPlaybackMode,
  reducedMotion,
  resetSignal,
  focusLocation,
  inspectLocation,
  inspectLabel,
  onReady,
  onLayerReady,
  onDetailsHidden,
  onPairCommitted,
  onSelect,
  onMapClick,
  onDetailTiles,
}: SmokeSceneProps) {
  const [keyboardActive, setKeyboardActive] = useState(false);
  const keyboardCursor = useRef({ lon: CAMERA_LON, lat: CAMERA_LAT });
  const keyboardCursorWorld = useRef(lonLatToVector3(CAMERA_LON, CAMERA_LAT, INSPECT_SHELL_RADIUS));
  const invalidateScene = useRef(() => {});
  const controlsRef = useRef<OrbitControlsImpl>(null);
  const pointerDownRef = useRef<InspectPointer | null>(null);
  const [renderingBusy, setRenderingBusy] = useState(false);
  const dprTimer = useRef<number | null>(null);
  const [zoomSignal, setZoomSignal] = useState(0);
  const markerLocation = inspectLocation ?? (focusLocation ? { lon: focusLocation.lon, lat: focusLocation.lat } : null);
  const dpr = adaptiveDpr(typeof window === "undefined" ? 1.25 : window.devicePixelRatio, renderingBusy, playing);
  const setInteraction = useCallback((active: boolean) => {
    if (dprTimer.current !== null) window.clearTimeout(dprTimer.current);
    if (active) setRenderingBusy(true);
    else dprTimer.current = window.setTimeout(() => setRenderingBusy(false), 250);
  }, []);
  const recordPointerDown = (event: InspectPointerEvent) => {
    const pointer = inspectPointerFromEvent(event);
    if (pointer) pointerDownRef.current = pointer;
  };
  const acceptInspectPointer = (event: InspectPointerEvent) => {
    const pointer = inspectPointerFromEvent(event);
    if (!pointer) return false;
    const down = pointerDownRef.current;
    return isInspectClick(down?.point, pointer.point, down?.pointerType || pointer.pointerType);
  };
  useEffect(() => () => { if (dprTimer.current !== null) window.clearTimeout(dprTimer.current); }, []);
  useEffect(() => setKeyboardActive(false), [resetSignal, layers]);
  useEffect(() => {
    if (keyboardActive || inspectLocation) invalidateScene.current();
  }, [inspectLocation, keyboardActive]);
  const inspectKeyboardCursor = () => {
    const selection = keyboardInspectSelection({
      air: layers.air,
      monitors,
      cursor: keyboardCursor.current,
    });
    if (!selection) return;
    if (selection.kind === "air-monitor") {
      onSelect({ kind: "air-monitor", monitor: selection.monitor });
      return;
    }
    onMapClick(keyboardCursor.current);
  };
  return (
    <Canvas
      role="region"
      tabIndex={0}
      data-testid="interactive-globe"
      data-map-keyboard
      data-keyboard-cursor-visible={keyboardActive ? "true" : "false"}
      data-visible-detail-tiles={detailTileIds.join(",")}
      data-focus-location={focusLocation ? `${focusLocation.lon},${focusLocation.lat}` : ""}
      data-inspect-location={inspectLocation ? `${inspectLocation.lon},${inspectLocation.lat}` : ""}
      data-detail-playback-mode={detailPlaybackMode}
      data-air-representation={layers.air ? "points" : "none"}
      data-selected-incident-count={selectedIncidentIds.length}
      aria-label="Interactive globe"
      aria-describedby="globe-help"
      aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Enter Escape + -"
      onBlur={() => setKeyboardActive(false)}
      onPointerDown={(event) => {
        setKeyboardActive(false);
        recordPointerDown(event);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") { setKeyboardActive(false); onSelect(null); return; }
        if (event.key === "+" || event.key === "=") { event.preventDefault(); setZoomSignal((value) => Math.abs(value) + 1); return; }
        if (event.key === "-" || event.key === "_") { event.preventDefault(); setZoomSignal((value) => -(Math.abs(value) + 1)); return; }
        if (event.key === "Enter") { event.preventDefault(); setKeyboardActive(true); inspectKeyboardCursor(); return; }
        const moves = { ArrowLeft: [-2, 0], ArrowRight: [2, 0], ArrowUp: [0, 2], ArrowDown: [0, -2] } as const;
        const move = moves[event.key as keyof typeof moves];
        if (!move) return;
        event.preventDefault();
        setKeyboardActive(true);
        keyboardCursor.current = {
          lon: Math.max(DISPLAY_BOUNDS.west, Math.min(DISPLAY_BOUNDS.east, keyboardCursor.current.lon + move[0])),
          lat: Math.max(DISPLAY_BOUNDS.south, Math.min(DISPLAY_BOUNDS.north, keyboardCursor.current.lat + move[1])),
        };
        keyboardCursorWorld.current.copy(lonLatToVector3(keyboardCursor.current.lon, keyboardCursor.current.lat, INSPECT_SHELL_RADIUS));
        invalidateScene.current();
      }}
      camera={{ position: initialCamera(), fov: CAMERA_FOV, near: 0.01, far: 20 }}
      dpr={dpr}
      frameloop="demand"
      gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
      raycaster={{
        params: {
          Mesh: {},
          Line: { threshold: 1 },
          LOD: {},
          Points: { threshold: 0.035 },
          Sprite: {},
        },
      }}
      onPointerMissed={(event) => {
        if (!acceptInspectPointer(event)) return;
        onSelect(null);
      }}
      onCreated={({ gl, scene, invalidate }) => {
        invalidateScene.current = invalidate;
        renderRequestRef.current = invalidate;
        gl.setClearColor(new Color("#03080e"), 1);
        scene.background = new Color("#03080e");
      }}
    >
      <Suspense fallback={null}>
        <EarthGlobe mobile={mobile || compactLabels} geo={geo} onReady={onReady} />
        {(legacyForecast ? contextImageA : residentRasters.length > 0) && layers.forecast ? legacyForecast ? (
          <LegacyContextSurface imageA={contextImageA!} imageB={contextImageB} mixRef={contextMixRef} opacityRef={smokeOpacityRef} onReady={onLayerReady} />
        ) : (
          <ContextSurface resident={residentRasters} fromIndex={renderedFromIndex} toIndex={renderedToIndex} details={detailImages} visibleDetailIds={detailTileIds} detailGrid={detailGrid} mixRef={contextMixRef} opacityRef={smokeOpacityRef} detailEnabled={detailPlaybackMode === "visible"} reducedMotion={reducedMotion} onReady={onLayerReady} onDetailsHidden={onDetailsHidden} />
        ) : null}
        {layers.air ? <AirContextPoints items={monitors} mobile={mobile} selectedMonitorIds={selectedMonitorIds} suppressHover={renderingBusy} onSelect={(members) => onSelect(members.length === 1 ? { kind: "air-monitor", monitor: members[0] } : { kind: "air-cluster", monitors: members })} /> : null}
        {layers.incidents ? <IncidentContextPoints items={incidents} mobile={mobile} selectedIncidentIds={selectedIncidentIds} suppressHover={renderingBusy} onSelect={(members) => onSelect(members.length === 1 ? { kind: "incident", incident: members[0] } : { kind: "incident-cluster", incidents: members })} /> : null}
        {keyboardActive ? <GlobeCursorOverlay position={keyboardCursorWorld.current} kind="cursor" /> : null}
        {markerLocation ? <SelectionMarker lon={markerLocation.lon} lat={markerLocation.lat} label={inspectLocation ? inspectLabel : null} mobile={mobile} compactLabels={compactLabels} /> : null}
        <mesh
          onPointerDown={(event) => recordPointerDown(event)}
          onClick={(event) => {
            if (!shouldInspectMapClick(layers) || !acceptInspectPointer(event)) return;
            const { lon, lat } = vector3ToLonLat(event.point as Vector3);
            if (inDisplayBounds(lon, lat)) {
              onMapClick({ lon, lat });
            }
          }}
        >
          <sphereGeometry args={[INSPECT_SHELL_RADIUS, 96, 64]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      </Suspense>
      <CameraControls resetSignal={resetSignal} focusLocation={focusLocation} zoomSignal={zoomSignal} controlsRef={controlsRef} mobile={mobile} reducedMotion={reducedMotion} detailGrid={detailGrid} onInteraction={setInteraction} onDetailTiles={onDetailTiles} />
      <ForecastFrame mix={renderMix} opacity={renderOpacity} mixRef={contextMixRef} fromIndex={renderedFromIndex} toIndex={renderedToIndex} onPairCommitted={onPairCommitted} />
    </Canvas>
  );
}
