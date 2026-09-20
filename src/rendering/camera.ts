import { Frustum, Matrix4, PerspectiveCamera, Sphere, Vector3, type Camera } from "three";
import { detailTileBounds, DISPLAY_BOUNDS, type DetailGridShape } from "@/data/contextSchema";
import { lonLatToVector3 } from "./projection";

export const MIN_CAMERA_DISTANCE = 1.3;
export const LOCATION_CAMERA_DISTANCE = 1.45;
export const CAMERA_FOV = 42;
export const SHORT_LANDSCAPE_RESET_DISTANCE = 4.45;

export type CameraGestureProfile = {
  zoomSpeed: number;
  rotateSpeed: number;
  keyboardZoomFactor: number;
  dampingFactor: number;
};

export function cameraGestureProfile(distance: number, shortLandscape = false): CameraGestureProfile {
  const progress = Math.max(0, Math.min(1, (distance - MIN_CAMERA_DISTANCE) / (1.8 - MIN_CAMERA_DISTANCE)));
  const eased = progress * progress * (3 - 2 * progress);
  return {
    zoomSpeed: 0.22 + (0.72 - 0.22) * eased,
    rotateSpeed: (0.24 + (0.45 - 0.24) * eased) * (shortLandscape ? 0.85 : 1),
    keyboardZoomFactor: 1.06 + (1.18 - 1.06) * eased,
    dampingFactor: 0.14 + (0.08 - 0.14) * eased,
  };
}

export function overviewResetDistance(overview: number, shortLandscape = false): number {
  const resetDistance = overview * 0.9;
  return shortLandscape ? Math.min(resetDistance, SHORT_LANDSCAPE_RESET_DISTANCE) : resetDistance;
}

export type CameraSafeInsets = { top: number; right: number; bottom: number; left: number };

export const locationCameraPosition = (lon: number, lat: number) => lonLatToVector3(lon, lat, LOCATION_CAMERA_DISTANCE);

export function adaptiveDpr(devicePixelRatio: number, interacting: boolean, playing: boolean): number {
  const idle = Math.min(devicePixelRatio, 2);
  return interacting || playing ? Math.min(idle, 1.25) : idle;
}

const CAMERA_LON = -100;
const CAMERA_LAT = 52;
export function cameraSafeInsets(width: number, mobile: boolean, shortLandscape = false): CameraSafeInsets {
  if (shortLandscape) return { top: 52, right: 16, bottom: 66, left: 16 };
  if (mobile || width < 900) return { top: 108, right: 18, bottom: 140, left: 18 };
  return { top: 64, right: 24, bottom: 84, left: 24 };
}

function displaySamples() {
  const samples = [];
  for (let row = 0; row <= 4; row += 1) {
    const lat = DISPLAY_BOUNDS.south + (DISPLAY_BOUNDS.north - DISPLAY_BOUNDS.south) * row / 4;
    for (let column = 0; column <= 6; column += 1) {
      const lon = DISPLAY_BOUNDS.west + (DISPLAY_BOUNDS.east - DISPLAY_BOUNDS.west) * column / 6;
      samples.push(lonLatToVector3(lon, lat, 1.01));
    }
  }
  return samples;
}

const SAMPLES = displaySamples();

const DETAIL_FRUSTUM = new Frustum();
const DETAIL_PROJECTION = new Matrix4();
const DETAIL_DIRECTION = new Vector3();
const DETAIL_CENTER = new Vector3();
const DETAIL_CENTER_NORMAL = new Vector3();
const DETAIL_CORNER = new Vector3();
const DETAIL_CORNER_NORMAL = new Vector3();
const DETAIL_PROJECTED = new Vector3();
const DETAIL_SPHERE = new Sphere();

export function fitOverviewDistance(
  width: number,
  height: number,
  fov = CAMERA_FOV,
  insets: CameraSafeInsets = { top: 0, right: 0, bottom: 0, left: 0 },
): number {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  const camera = new PerspectiveCamera(fov, safeWidth / safeHeight, 0.01, 20);
  const direction = lonLatToVector3(CAMERA_LON, CAMERA_LAT, 1).normalize();
  const fits = (distance: number) => {
    camera.position.copy(direction).multiplyScalar(distance);
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);
    camera.updateProjectionMatrix();
    return SAMPLES.every((sample) => {
      const projected = sample.clone().project(camera);
      const x = (projected.x + 1) * safeWidth / 2;
      const y = (1 - projected.y) * safeHeight / 2;
      return projected.z <= 1
        && x >= insets.left && x <= safeWidth - insets.right
        && y >= insets.top && y <= safeHeight - insets.bottom;
    });
  };
  let low = 1.8;
  let high = 7;
  if (!fits(high)) return high;
  for (let iteration = 0; iteration < 24; iteration += 1) {
    const middle = (low + high) / 2;
    if (fits(middle)) high = middle;
    else low = middle;
  }
  return Math.max(2.2, Math.min(7, high * 1.025));
}

export function visibleDetailTiles(camera: Camera, grid: DetailGridShape): number[] {
  DETAIL_DIRECTION.copy(camera.position).normalize();
  const horizon = 1 / camera.position.length() - 0.05;
  DETAIL_FRUSTUM.setFromProjectionMatrix(DETAIL_PROJECTION.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  const result: Array<{ id: number; distance: number }> = [];
  for (let row = 0; row < grid.rows; row += 1) for (let column = 0; column < grid.columns; column += 1) {
    const { west, east, south, north } = detailTileBounds(grid, column, row);
    lonLatToVector3((west + east) / 2, (south + north) / 2, 1.008, DETAIL_CENTER);
    DETAIL_CENTER_NORMAL.copy(DETAIL_CENTER).normalize();
    let angularRadius = 0;
    let sphereRadius = 0;
    const corners: Array<[number, number]> = [[west, south], [east, south], [west, north], [east, north]];
    for (const [lon, lat] of corners) {
      lonLatToVector3(lon, lat, 1.008, DETAIL_CORNER);
      DETAIL_CORNER_NORMAL.copy(DETAIL_CORNER).normalize();
      angularRadius = Math.max(angularRadius, Math.acos(Math.min(1, Math.max(-1, DETAIL_CORNER_NORMAL.dot(DETAIL_CENTER_NORMAL)))));
      sphereRadius = Math.max(sphereRadius, DETAIL_CORNER.distanceTo(DETAIL_CENTER));
    }
    const centerAngle = Math.acos(Math.min(1, Math.max(-1, DETAIL_CENTER_NORMAL.dot(DETAIL_DIRECTION))));
    if (Math.cos(Math.max(0, centerAngle - angularRadius)) <= horizon) continue;
    DETAIL_SPHERE.center.copy(DETAIL_CENTER);
    DETAIL_SPHERE.radius = sphereRadius;
    if (!DETAIL_FRUSTUM.intersectsSphere(DETAIL_SPHERE)) continue;
    DETAIL_PROJECTED.copy(DETAIL_CENTER).project(camera);
    result.push({ id: row * grid.columns + column, distance: DETAIL_PROJECTED.x ** 2 + DETAIL_PROJECTED.y ** 2 });
  }
  return result.sort((left, right) => left.distance - right.distance || left.id - right.id).map((item) => item.id);
}
