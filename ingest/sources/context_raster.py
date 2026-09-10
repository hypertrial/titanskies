from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageDraw

from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_HEIGHT, CONTEXT_WIDTH

def _clip_ring(points: list[list[float]]) -> list[list[float]]:
    clipped = points
    for axis, limit, keep_greater in (
        (0, CONTEXT_BOUNDS["west"], True),
        (0, CONTEXT_BOUNDS["east"], False),
        (1, CONTEXT_BOUNDS["south"], True),
        (1, CONTEXT_BOUNDS["north"], False),
    ):
        if not clipped:
            break
        output: list[list[float]] = []
        previous = clipped[-1]
        previous_inside = previous[axis] >= limit if keep_greater else previous[axis] <= limit
        for current in clipped:
            current_inside = current[axis] >= limit if keep_greater else current[axis] <= limit
            if current_inside != previous_inside:
                ratio = (limit - previous[axis]) / (current[axis] - previous[axis])
                intersection = [
                    previous[0] + ratio * (current[0] - previous[0]),
                    previous[1] + ratio * (current[1] - previous[1]),
                ]
                intersection[axis] = limit
                output.append(intersection)
            if current_inside:
                output.append(current)
            previous, previous_inside = current, current_inside
        clipped = output
    return clipped

def lon_lat_to_pixel(lon: float, lat: float) -> tuple[int, int]:
    x = round((lon - CONTEXT_BOUNDS["west"]) / (CONTEXT_BOUNDS["east"] - CONTEXT_BOUNDS["west"]) * (CONTEXT_WIDTH - 1))
    y = round((CONTEXT_BOUNDS["north"] - lat) / (CONTEXT_BOUNDS["north"] - CONTEXT_BOUNDS["south"]) * (CONTEXT_HEIGHT - 1))
    return x, y


def image_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def rasterize_perimeters(rings: Iterable[list[list[float]]]) -> bytes:
    image = Image.new("RGBA", (CONTEXT_WIDTH, CONTEXT_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for ring in rings:
        points = [lon_lat_to_pixel(lon, lat) for lon, lat in ring]
        if len(points) >= 3:
            draw.line([*points, points[0]], fill=(255, 188, 87, 210), width=2, joint="curve")
    return image_png(image)

def _validated_png(data: bytes, label: str, expected_size: tuple[int, int] | None = None) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or expected_size and image.size != expected_size:
                raise ValueError
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid {label} image") from exc
    return data
