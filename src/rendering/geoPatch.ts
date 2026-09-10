import { lonLatToVector3 } from "@/rendering/projection";
import { BufferGeometry, Float32BufferAttribute } from "three";

export function geoBoundsGeometry(
  bounds: { west: number; south: number; east: number; north: number },
  radius: number,
  longitudeSegments = 64,
  latitudeSegments = 40,
  margin = 0,
): BufferGeometry {
  const geometry = new BufferGeometry();
  const west = bounds.west - margin;
  const east = bounds.east + margin;
  const south = bounds.south - margin;
  const north = bounds.north + margin;
  const positions: number[] = [];
  const normals: number[] = [];
  const indices: number[] = [];

  for (let y = 0; y <= latitudeSegments; y += 1) {
    const lat = south + (north - south) * y / latitudeSegments;
    for (let x = 0; x <= longitudeSegments; x += 1) {
      const lon = west + (east - west) * x / longitudeSegments;
      const point = lonLatToVector3(lon, lat, radius);
      positions.push(point.x, point.y, point.z);
      point.normalize();
      normals.push(point.x, point.y, point.z);
    }
  }

  const stride = longitudeSegments + 1;
  for (let y = 0; y < latitudeSegments; y += 1) {
    for (let x = 0; x < longitudeSegments; x += 1) {
      const a = y * stride + x;
      const b = a + 1;
      const c = a + stride;
      const d = c + 1;
      indices.push(a, b, c, b, d, c);
    }
  }

  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new Float32BufferAttribute(normals, 3));
  geometry.setIndex(indices);
  geometry.computeBoundingSphere();
  return geometry;
}
