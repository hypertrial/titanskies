import { Vector3, type Camera } from "three";

export const INSPECT_SHELL_RADIUS = 1.007;

const projectedScreen = new Vector3();

export function lonLatToVector3(lon: number, lat: number, radius = 1, target = new Vector3()): Vector3 {
  const lonRad = (lon * Math.PI) / 180;
  const latRad = (lat * Math.PI) / 180;
  const cosLat = Math.cos(latRad);
  return target.set(cosLat * Math.sin(lonRad), Math.sin(latRad), cosLat * Math.cos(lonRad)).multiplyScalar(radius);
}

export function vector3ToLonLat(position: Vector3): { lon: number; lat: number } {
  const n = position.clone().normalize();
  const lat = (Math.asin(Math.min(1, Math.max(-1, n.y))) * 180) / Math.PI;
  const lon = (Math.atan2(n.x, n.z) * 180) / Math.PI;
  return { lon, lat };
}

export function projectToScreen(position: Vector3, camera: Camera, viewport: { width: number; height: number }) {
  projectedScreen.copy(position).project(camera);
  return { x: (projectedScreen.x + 1) * viewport.width / 2, y: (1 - projectedScreen.y) * viewport.height / 2 };
}
