import { PerspectiveCamera, Ray, Sphere, Vector3 } from "three";
import { describe, expect, it } from "vitest";
import { INSPECT_SHELL_RADIUS, lonLatToVector3, projectToScreen, vector3ToLonLat } from "./projection";

const VIEWPORT = { width: 1280, height: 720 };
const OFF_AXIS = { lon: -116.4, lat: 49.13 };
const OFF_AXIS_CLICK = { x: 540, y: 280 };
const VIEW_CENTER_CLICK = { x: VIEWPORT.width / 2, y: VIEWPORT.height / 2 };
const OLD_PIN_RADIUS = 1.03;

function lookingAtOrigin() {
  const camera = new PerspectiveCamera(42, VIEWPORT.width / VIEWPORT.height, 0.01, 20);
  camera.position.copy(lonLatToVector3(-100, 52, 3));
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  return camera;
}

function distanceFromCenter(screen: { x: number; y: number }) {
  return Math.hypot(screen.x - VIEWPORT.width / 2, screen.y - VIEWPORT.height / 2);
}

function rayThroughScreen(camera: PerspectiveCamera, screen: { x: number; y: number }) {
  const ndc = new Vector3((screen.x / VIEWPORT.width) * 2 - 1, 1 - (screen.y / VIEWPORT.height) * 2, 0.5);
  ndc.unproject(camera);
  return new Ray(camera.position.clone(), ndc.sub(camera.position).normalize());
}

function pinForClick(camera: PerspectiveCamera, click: { x: number; y: number }, hitRadius: number, pinRadius: number) {
  const hit = rayThroughScreen(camera, click).intersectSphere(new Sphere(new Vector3(), hitRadius), new Vector3());
  expect(hit).toBeTruthy();
  const { lon, lat } = vector3ToLonLat(hit!);
  const pin = projectToScreen(lonLatToVector3(lon, lat, pinRadius), camera, VIEWPORT);
  return { pin, error: Math.hypot(pin.x - click.x, pin.y - click.y) };
}

describe("inspect projection", () => {
  it("uses the expected globe axes", () => {
    const equator = lonLatToVector3(0, 0, 1);
    expect(equator.x).toBeCloseTo(0);
    expect(equator.y).toBeCloseTo(0);
    expect(equator.z).toBeCloseTo(1);
    const east = lonLatToVector3(90, 0, 1);
    expect(east.x).toBeCloseTo(1);
    expect(east.z).toBeCloseTo(0);
  });

  it("round-trips a Seattle coordinate", () => {
    const seattle = vector3ToLonLat(lonLatToVector3(-122.3321, 47.6062));
    expect(seattle.lon).toBeCloseTo(-122.3321, 5);
    expect(seattle.lat).toBeCloseTo(47.6062, 5);
  });

  it("round-trips an off-axis inspect point at the shared shell radius", () => {
    const camera = lookingAtOrigin();
    const world = lonLatToVector3(OFF_AXIS.lon, OFF_AXIS.lat, INSPECT_SHELL_RADIUS);
    const screen = projectToScreen(world, camera, VIEWPORT);
    const { lon, lat } = vector3ToLonLat(world);
    const again = projectToScreen(lonLatToVector3(lon, lat, INSPECT_SHELL_RADIUS), camera, VIEWPORT);
    expect(Math.hypot(again.x - screen.x, again.y - screen.y)).toBeLessThan(0.5);
  });

  it("projects a larger radius limbward from the same lon/lat", () => {
    const camera = lookingAtOrigin();
    const near = projectToScreen(lonLatToVector3(OFF_AXIS.lon, OFF_AXIS.lat, 1.001), camera, VIEWPORT);
    const far = projectToScreen(lonLatToVector3(OFF_AXIS.lon, OFF_AXIS.lat, 1.03), camera, VIEWPORT);
    expect(distanceFromCenter(far)).toBeGreaterThan(distanceFromCenter(near));
  });

  it("shifts lon/lat limbward when the same ray hits a smaller shell", () => {
    const camera = lookingAtOrigin();
    const visual = lonLatToVector3(OFF_AXIS.lon, OFF_AXIS.lat, INSPECT_SHELL_RADIUS);
    const direction = visual.clone().sub(camera.position).normalize();
    const ray = new Ray(camera.position.clone(), direction);
    const hitPick = ray.intersectSphere(new Sphere(new Vector3(), 1.001), new Vector3());
    const hitVisual = ray.intersectSphere(new Sphere(new Vector3(), INSPECT_SHELL_RADIUS), new Vector3());
    expect(hitPick).toBeTruthy();
    expect(hitVisual).toBeTruthy();
    const picked = vector3ToLonLat(hitPick!);
    const shown = vector3ToLonLat(hitVisual!);
    expect(Math.hypot(picked.lon - shown.lon, picked.lat - shown.lat)).toBeGreaterThan(0.01);
    const cameraLonLat = vector3ToLonLat(camera.position);
    const pickToCamera = Math.hypot(picked.lon - cameraLonLat.lon, picked.lat - cameraLonLat.lat);
    const visualToCamera = Math.hypot(shown.lon - cameraLonLat.lon, shown.lat - cameraLonLat.lat);
    expect(camera.position.distanceTo(hitPick!)).toBeGreaterThan(camera.position.distanceTo(hitVisual!));
    expect(pickToCamera).toBeGreaterThan(visualToCamera);
  });

  it("reprojects an off-axis click on the shared shell to the same pixel", () => {
    const camera = lookingAtOrigin();
    expect(pinForClick(camera, OFF_AXIS_CLICK, INSPECT_SHELL_RADIUS, INSPECT_SHELL_RADIUS).error).toBeLessThan(0.5);
  });

  it("leaves a 1.03 pin limbward of an off-axis shared-shell click", () => {
    const camera = lookingAtOrigin();
    const mismatched = pinForClick(camera, OFF_AXIS_CLICK, INSPECT_SHELL_RADIUS, OLD_PIN_RADIUS);
    const defaultRadius = pinForClick(camera, OFF_AXIS_CLICK, INSPECT_SHELL_RADIUS, 1);
    expect(mismatched.error).toBeGreaterThan(3);
    expect(distanceFromCenter(mismatched.pin)).toBeGreaterThan(distanceFromCenter(OFF_AXIS_CLICK));
    expect(defaultRadius.error).toBeGreaterThan(0.5);
    expect(pinForClick(camera, VIEW_CENTER_CLICK, INSPECT_SHELL_RADIUS, OLD_PIN_RADIUS).error).toBeLessThan(0.5);
  });

  it("does not mutate the world position it projects", () => {
    const camera = lookingAtOrigin();
    const world = lonLatToVector3(OFF_AXIS.lon, OFF_AXIS.lat, INSPECT_SHELL_RADIUS);
    const before = world.clone();
    projectToScreen(world, camera, VIEWPORT);
    expect(world.distanceTo(before)).toBe(0);
  });
});
