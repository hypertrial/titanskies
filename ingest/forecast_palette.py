from __future__ import annotations

import io
import zlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_OFFICIAL_PALETTE = json.loads((Path(__file__).resolve().parent.parent / "shared" / "smoke-palette-v3.json").read_text(encoding="utf-8"))
_PALETTE_V1 = json.loads((Path(__file__).resolve().parent.parent / "shared" / "smoke-display-palette-v1.json").read_text(encoding="utf-8"))
_PALETTE_V2 = json.loads((Path(__file__).resolve().parent.parent / "shared" / "smoke-display-palette-v2.json").read_text(encoding="utf-8"))
_PALETTES = {str(item["processingVersion"]): item for item in (_PALETTE_V1, _PALETTE_V2)}
_PALETTE = _PALETTE_V1

PALETTE_VERSION = str(_PALETTE["processingVersion"])
V8_PALETTE_VERSION = str(_PALETTE_V2["processingVersion"])
PALETTE_UNITS = str(_PALETTE["units"])
KG_PER_UG = 1e-9
MASK_NONE = 0
MASK_FIREWORK = 1
MASK_HRRR = 2
MASK_COMBINED = 3
MASK_LABELS = {MASK_NONE: "none", MASK_FIREWORK: "firework", MASK_HRRR: "hrrr", MASK_COMBINED: "combined"}
LUT_STEPS_PER_INTERVAL = int(_PALETTE.get("lutStepsPerInterval", 24))


def _threshold_colors(palette: dict[str, Any]) -> list[tuple[float, float, tuple[int, int, int, int], tuple[int, int, int, int]]]:
    rows = []
    for item in palette["thresholds"]:
        start = tuple(int(value) for value in item["rgba"])
        end = tuple(int(value) for value in item.get("rgbaEnd", item["rgba"]))
        rows.append((float(item["min"]), float(item["max"]), start, end))
    return rows


def threshold_colors() -> list[tuple[float, float, tuple[int, int, int, int], tuple[int, int, int, int]]]:
    return _threshold_colors(_OFFICIAL_PALETTE)


def display_palette(version: str = PALETTE_VERSION) -> dict[str, Any]:
    try:
        return _PALETTES[version]
    except KeyError as exc:
        raise ValueError("unsupported smoke display palette") from exc


def display_threshold_colors(version: str = PALETTE_VERSION) -> list[tuple[float, float, tuple[int, int, int, int], tuple[int, int, int, int]]]:
    return _threshold_colors(display_palette(version))


def texture_scale_max(version: str = PALETTE_VERSION) -> float:
    return float(display_palette(version).get("textureScaleMax", 250))


def mask_rgba(code: int) -> tuple[int, int, int, int]:
    label = MASK_LABELS[code]
    return tuple(int(value) for value in _PALETTE["mask"][label])


def _lerp(start: tuple[int, int, int, int], end: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    t = min(1.0, max(0.0, float(t)))
    return tuple(int(round(s + (e - s) * t)) for s, e in zip(start, end))


def _interval_steps(start: tuple[int, int, int, int], end: tuple[int, int, int, int], steps: int = LUT_STEPS_PER_INTERVAL) -> int:
    if start[3] == 0:
        return 0
    if start == end:
        return 1
    return steps


def _color_lut(palette: dict[str, Any]) -> list[tuple[int, int, int, int]]:
    lut = [(0, 0, 0, 0)]
    steps_per_interval = int(palette.get("lutStepsPerInterval", 24))
    for low, high, start, end in _threshold_colors(palette):
        del low, high
        steps = _interval_steps(start, end, steps_per_interval)
        if steps == 0:
            continue
        if steps == 1:
            lut.append(start)
            continue
        for index in range(steps):
            lut.append(_lerp(start, end, index / (steps - 1)))
    if len(lut) > 256:
        raise ValueError("FireWork color LUT exceeds 256 indexed-PNG entries")
    return lut


def color_lut(version: str = PALETTE_VERSION) -> list[tuple[int, int, int, int]]:
    return _color_lut(display_palette(version))


def official_color_lut() -> list[tuple[int, int, int, int]]:
    return _color_lut(_OFFICIAL_PALETTE)


_COLOR_LUT = color_lut()
_COLOR_LUTS = {version: color_lut(version) for version in _PALETTES}
_OFFICIAL_COLOR_LUT = official_color_lut()
_LUT_RGB = np.array([color[:3] for color in _COLOR_LUT], dtype=np.uint8)
_LUT_ALPHA = np.array([color[3] for color in _COLOR_LUT], dtype=np.uint8)


def interpolate_color(value: float, palette_version: str = PALETTE_VERSION) -> tuple[int, int, int, int]:
    if not np.isfinite(value) or value < 0:
        return (0, 0, 0, 0)
    return _COLOR_LUTS[palette_version][int(concentration_index(np.array([value], dtype=np.float32), np.array([True]), palette_version)[0])]


def official_interpolate_color(value: float) -> tuple[int, int, int, int]:
    if not np.isfinite(value) or value < 0:
        return (0, 0, 0, 0)
    return _OFFICIAL_COLOR_LUT[int(official_concentration_index(np.array([value], dtype=np.float32), np.array([True]))[0])]


def concentration_index(values: np.ndarray, valid: np.ndarray, palette_version: str = PALETTE_VERSION) -> np.ndarray:
    indexes = np.zeros(values.shape, dtype=np.uint8)
    finite = valid & np.isfinite(values) & (values >= 0)
    cursor = 1
    stops = display_threshold_colors(palette_version)
    for index, (low, high, start, end) in enumerate(stops):
        steps = _interval_steps(start, end)
        if steps == 0:
            continue
        if index == len(stops) - 1:
            selected = finite & (values >= low)
        else:
            selected = finite & (values >= low) & (values < high)
        if steps == 1:
            indexes[selected] = cursor
            cursor += 1
            continue
        span = high - low
        t = np.zeros(values.shape, dtype=np.float32)
        t[selected] = np.clip((values[selected] - low) / span, 0.0, 1.0)
        indexes[selected] = cursor + np.rint(t[selected] * (steps - 1)).astype(np.uint8)
        cursor += steps
    return indexes


def colorize_concentration(values: np.ndarray, valid: np.ndarray, palette_version: str = PALETTE_VERSION) -> np.ndarray:
    indexes = concentration_index(values, valid, palette_version)
    lut = _COLOR_LUTS[palette_version]
    lut_rgb = np.array([color[:3] for color in lut], dtype=np.uint8)
    lut_alpha = np.array([color[3] for color in lut], dtype=np.uint8)
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut_rgb[indexes]
    rgba[..., 3] = lut_alpha[indexes]
    return rgba


def official_colorize_concentration(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    indexes = official_concentration_index(values, valid)
    lut_rgb = np.array([color[:3] for color in _OFFICIAL_COLOR_LUT], dtype=np.uint8)
    lut_alpha = np.array([color[3] for color in _OFFICIAL_COLOR_LUT], dtype=np.uint8)
    rgba = np.zeros(values.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut_rgb[indexes]
    rgba[..., 3] = lut_alpha[indexes]
    return rgba


def encode_indexes(indexes: np.ndarray, lut: list[tuple[int, int, int, int]] = _COLOR_LUT) -> bytes:
    image = Image.fromarray(np.asarray(indexes, dtype=np.uint8), mode="P")
    palette_bytes = bytearray(768)
    for index, color in enumerate(lut):
        palette_bytes[index * 3:index * 3 + 3] = color[:3]
    image.putpalette(bytes(palette_bytes))
    image.info["transparency"] = bytes(color[3] for color in lut)
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        optimize=False,
        compress_level=6,
        compress_type=zlib.Z_RLE,
    )
    return output.getvalue()


def encode_concentration(values: np.ndarray, valid: np.ndarray, palette_version: str = PALETTE_VERSION) -> bytes:
    return encode_indexes(concentration_index(values, valid, palette_version), _COLOR_LUTS[palette_version])


def encode_official_concentration(values: np.ndarray, valid: np.ndarray) -> bytes:
    return encode_indexes(official_concentration_index(values, valid), _OFFICIAL_COLOR_LUT)


def official_concentration_index(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    indexes = np.zeros(values.shape, dtype=np.uint8)
    finite = valid & np.isfinite(values) & (values >= 0)
    cursor = 1
    stops = threshold_colors()
    official_steps = int(_OFFICIAL_PALETTE.get("lutStepsPerInterval", 24))
    for index, (low, high, start, end) in enumerate(stops):
        steps = _interval_steps(start, end, official_steps)
        if steps == 0:
            continue
        selected = finite & (values >= low) if index == len(stops) - 1 else finite & (values >= low) & (values < high)
        if steps == 1:
            indexes[selected] = cursor
            cursor += 1
            continue
        t = np.zeros(values.shape, dtype=np.float32)
        t[selected] = np.clip((values[selected] - low) / (high - low), 0.0, 1.0)
        indexes[selected] = cursor + np.rint(t[selected] * (steps - 1)).astype(np.uint8)
        cursor += steps
    return indexes


def encode_indexed_png(rgba: np.ndarray, palette_version: str = PALETTE_VERSION) -> bytes:
    packed = (
        rgba[..., 0].astype(np.uint32) << 24
        | rgba[..., 1].astype(np.uint32) << 16
        | rgba[..., 2].astype(np.uint32) << 8
        | rgba[..., 3].astype(np.uint32)
    )
    lut = _COLOR_LUTS[palette_version]
    lut_packed = np.array(
        [(color[0] << 24) | (color[1] << 16) | (color[2] << 8) | color[3] for color in lut],
        dtype=np.uint32,
    )
    order = np.argsort(lut_packed, kind="stable")
    sorted_keys = lut_packed[order]
    positions = np.clip(np.searchsorted(sorted_keys, packed), 0, len(sorted_keys) - 1)
    matched = sorted_keys[positions] == packed
    indexes = np.zeros(packed.shape, dtype=np.uint8)
    indexes[matched] = order[positions[matched]].astype(np.uint8)
    return encode_indexes(indexes, lut)


def encode_mask_png(codes: np.ndarray, *, legacy: bool = False) -> bytes:
    values = np.asarray(codes, dtype=np.uint8)
    if legacy:
        values = np.where(values == MASK_COMBINED, MASK_HRRR, values).astype(np.uint8)
    image = Image.fromarray(values, mode="P")
    palette_bytes = bytearray(768)
    for code in MASK_LABELS:
        color = mask_rgba(code)
        palette_bytes[code * 3:code * 3 + 3] = color[:3]
    image.putpalette(bytes(palette_bytes))
    image.info["transparency"] = bytes(mask_rgba(code)[3] for code in MASK_LABELS)
    output = io.BytesIO()
    image.save(
        output,
        format="PNG",
        optimize=False,
        compress_level=3,
        compress_type=zlib.Z_RLE,
    )
    return output.getvalue()


def decode_mask_png(data: bytes) -> np.ndarray:
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    pixels = np.asarray(image)
    codes = np.zeros(pixels.shape[:2], dtype=np.uint8)
    for code in (MASK_FIREWORK, MASK_HRRR, MASK_COMBINED):
        color = np.array(mask_rgba(code), dtype=np.uint8)
        codes[np.all(pixels == color, axis=2)] = code
    return codes


def legend_png(width: int = 360, height: int = 12, palette_version: str = PALETTE_VERSION) -> bytes:
    ramp = np.linspace(1.0, texture_scale_max(palette_version), width, dtype=np.float32)
    valid = np.ones(width, dtype=bool)
    indexes = concentration_index(ramp, valid, palette_version)
    image = np.broadcast_to(indexes[None, :], (height, width)).copy()
    return encode_indexes(image, _COLOR_LUTS[palette_version])


def official_legend_png(width: int = 360, height: int = 12) -> bytes:
    ramp = np.linspace(1.0, 250.0, width, dtype=np.float32)
    valid = np.ones(width, dtype=bool)
    indexes = official_concentration_index(ramp, valid)
    image = np.broadcast_to(indexes[None, :], (height, width)).copy()
    return encode_indexes(image, _OFFICIAL_COLOR_LUT)


def firework_sld(layer: str) -> str:
    entries = []
    official_steps = int(_OFFICIAL_PALETTE.get("lutStepsPerInterval", 24))
    for low, high, start, end in threshold_colors():
        steps = _interval_steps(start, end, official_steps)
        if steps <= 1:
            hex_color = f"#{start[0]:02x}{start[1]:02x}{start[2]:02x}"
            opacity = f"{start[3] / 255:.3f}"
            entries.append(
                f'<ColorMapEntry color="{hex_color}" quantity="{low * KG_PER_UG:g}" opacity="{opacity}"/>'
            )
            continue
        for index in range(steps):
            t = index / (steps - 1)
            color = _lerp(start, end, t)
            quantity = (low + t * (high - low)) * KG_PER_UG
            hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
            opacity = f"{color[3] / 255:.3f}"
            entries.append(
                f'<ColorMapEntry color="{hex_color}" quantity="{quantity:g}" opacity="{opacity}"/>'
            )
    body = "\n              ".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld" xmlns:ogc="http://www.opengis.net/ogc" xmlns:xlink="http://www.w3.org/1999/xlink">
  <NamedLayer>
    <Name>{layer}</Name>
    <UserStyle>
      <Title>ECCC FireWork FW-SFC-PM-DIFF</Title>
      <FeatureTypeStyle>
        <Rule>
          <RasterSymbolizer>
            <ColorMap type="intervals">
              {body}
            </ColorMap>
          </RasterSymbolizer>
        </Rule>
      </FeatureTypeStyle>
    </UserStyle>
  </NamedLayer>
</StyledLayerDescriptor>
"""
