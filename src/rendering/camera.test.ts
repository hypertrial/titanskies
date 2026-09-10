import { PerspectiveCamera } from "three";
import { describe, expect, it } from "vitest";
import { lonLatToVector3 } from "./projection";
import { adaptiveDpr, cameraGestureProfile, cameraSafeInsets, fitOverviewDistance, locationCameraPosition, LOCATION_CAMERA_DISTANCE, MIN_CAMERA_DISTANCE, overviewResetDistance, SHORT_LANDSCAPE_RESET_DISTANCE, visibleDetailTiles } from "./camera";

describe("forecast camera fit", () => {
  it("targets searched locations at the close-detail distance", () => {
    expect(locationCameraPosition(-122.33, 47.61).length()).toBeCloseTo(LOCATION_CAMERA_DISTANCE, 8);
    expect(LOCATION_CAMERA_DISTANCE).toBeLessThan(1.8);
    expect(LOCATION_CAMERA_DISTANCE).toBeGreaterThan(MIN_CAMERA_DISTANCE);
  });
  it("keeps DPR capped throughout playback and interaction", () => {
    expect(adaptiveDpr(3, false, false)).toBe(2);
    expect(adaptiveDpr(3, true, false)).toBe(1.25);
    expect(adaptiveDpr(3, false, true)).toBe(1.25);
    expect(adaptiveDpr(1, true, true)).toBe(1);
  });

  it("smoothly reduces gesture sensitivity near the globe", () => {
    expect(cameraGestureProfile(1)).toEqual({ zoomSpeed: 0.22, rotateSpeed: 0.24, keyboardZoomFactor: 1.06, dampingFactor: 0.14 });
    expect(cameraGestureProfile(MIN_CAMERA_DISTANCE)).toEqual({ zoomSpeed: 0.22, rotateSpeed: 0.24, keyboardZoomFactor: 1.06, dampingFactor: 0.14 });
    const middle = cameraGestureProfile(1.55);
    expect(middle.zoomSpeed).toBeCloseTo(0.47);
    expect(middle.rotateSpeed).toBeCloseTo(0.345);
    expect(middle.keyboardZoomFactor).toBeCloseTo(1.12);
    expect(middle.dampingFactor).toBeCloseTo(0.11);
    expect(cameraGestureProfile(1.8)).toEqual({ zoomSpeed: 0.72, rotateSpeed: 0.45, keyboardZoomFactor: 1.18, dampingFactor: 0.08 });
    expect(cameraGestureProfile(9)).toEqual({ zoomSpeed: 0.72, rotateSpeed: 0.45, keyboardZoomFactor: 1.18, dampingFactor: 0.08 });
    expect(cameraGestureProfile(1.8, true).rotateSpeed).toBeCloseTo(0.45 * 0.85);
    const samples = [1.3, 1.4, 1.5, 1.6, 1.7, 1.8].map((distance) => cameraGestureProfile(distance));
    for (let index = 1; index < samples.length; index += 1) {
      expect(samples[index].zoomSpeed).toBeGreaterThan(samples[index - 1].zoomSpeed);
      expect(samples[index].rotateSpeed).toBeGreaterThan(samples[index - 1].rotateSpeed);
      expect(samples[index].keyboardZoomFactor).toBeGreaterThan(samples[index - 1].keyboardZoomFactor);
      expect(samples[index].dampingFactor).toBeLessThan(samples[index - 1].dampingFactor);
    }
  });

  it("starts short landscape close enough to keep the globe useful", () => {
    const resetDistance = overviewResetDistance(7, true);
    const projectedDiameterRatio = 1 / (Math.sqrt(resetDistance ** 2 - 1) * Math.tan(42 * Math.PI / 360));
    expect(resetDistance).toBe(SHORT_LANDSCAPE_RESET_DISTANCE);
    expect(projectedDiameterRatio).toBeGreaterThanOrEqual(0.6);
    expect(overviewResetDistance(3, true)).toBeCloseTo(2.7);
    expect(overviewResetDistance(7, false)).toBeCloseTo(6.3);
  });

  it.each([
    [390, 844, true],
    [768, 1024, false],
    [1280, 720, false],
    [1440, 900, false],
    [2560, 1080, false],
  ])("frames the forecast domain at %d×%d", (width, height, mobile) => {
    const distance = fitOverviewDistance(width, height, 42, cameraSafeInsets(width, mobile));
    expect(distance).toBeGreaterThan(MIN_CAMERA_DISTANCE);
    expect(distance).toBeLessThanOrEqual(7);
  });

  it("backs out farther for a portrait viewport and for UI-safe insets", () => {
    const portrait = fitOverviewDistance(390, 844, 42, cameraSafeInsets(390, true));
    const desktop = fitOverviewDistance(1440, 900, 42, cameraSafeInsets(1440, false));
    const unobstructed = fitOverviewDistance(1440, 900);
    expect(portrait).toBeGreaterThan(desktop);
    expect(desktop).toBeGreaterThan(unobstructed);
  });

  it("keeps the short-landscape globe centered while reserving compact controls", () => {
    const landscape = cameraSafeInsets(468, false, true);
    const fitted = fitOverviewDistance(468, 390, 42, landscape);
    const portraitInsets = fitOverviewDistance(468, 390, 42, { top: 84, right: 18, bottom: 168, left: 18 });
    expect(landscape.right).toBe(16);
    expect(landscape.left).toBe(16);
    expect(landscape.bottom).toBe(66);
    expect(fitted).toBeLessThan(portraitInsets);
  });

  it("conservatively admits a detail tile intersecting a narrow portrait frustum", () => {
    const camera = new PerspectiveCamera(42, 360 / 800, 0.01, 20);
    camera.position.copy(lonLatToVector3(-141.5, 10, 1.3));
    camera.lookAt(0, 0, 0);
    camera.updateMatrixWorld(true);
    camera.updateProjectionMatrix();
    const grid = { width: 4093, height: 2537, columns: 4, rows: 4, tileWidth: 1024, tileHeight: 635, sharedBoundaryPixels: 1 };
    const visible = visibleDetailTiles(camera, grid);
    expect(visible).toContain(12);
    expect(visible.length).toBeLessThan(16);
  });
});
