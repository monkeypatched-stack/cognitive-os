import math
import os
import shutil
import shlex
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import HTTPException, UploadFile, status


def _read_dxf_pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    return [
        (lines[index].strip(), lines[index + 1].strip())
        for index in range(0, len(lines) - 1, 2)
    ]


def _entity_fields(
    pairs: list[tuple[str, str]],
    start: int,
) -> tuple[dict[str, list[str]], int]:
    fields: dict[str, list[str]] = {}
    index = start
    while index < len(pairs):
        code, value = pairs[index]
        if code == "0":
            break
        fields.setdefault(code, []).append(value)
        index += 1
    return fields, index


def _float(fields: dict[str, list[str]], code: str, default: float = 0.0) -> float:
    values = fields.get(code)
    if not values:
        return default
    try:
        return float(values[-1])
    except ValueError:
        return default


def _float_list(fields: dict[str, list[str]], code: str) -> list[float]:
    values = []
    for value in fields.get(code, []):
        try:
            values.append(float(value))
        except ValueError:
            continue
    return values


def _transform(
    point: tuple[float, float], min_x: float, max_y: float
) -> tuple[float, float]:
    return point[0] - min_x, max_y - point[1]


def _line(
    start: tuple[float, float],
    end: tuple[float, float],
    min_x: float,
    max_y: float,
) -> str:
    x1, y1 = _transform(start, min_x, max_y)
    x2, y2 = _transform(end, min_x, max_y)
    return f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" />'


def _polyline(
    vertices: list[tuple[float, float]],
    min_x: float,
    max_y: float,
    close: bool = False,
) -> str:
    point_text = " ".join(
        f"{x:g},{y:g}"
        for x, y in (_transform(vertex, min_x, max_y) for vertex in vertices)
    )
    tag = "polygon" if close else "polyline"
    return f'<{tag} points="{point_text}" />'


def _arc_points(
    center: tuple[float, float],
    radius: float,
    start_deg: float,
    end_deg: float,
) -> tuple[tuple[float, float], tuple[float, float], int]:
    start_angle = math.radians(start_deg)
    end_angle = math.radians(end_deg)
    start = (
        center[0] + radius * math.cos(start_angle),
        center[1] + radius * math.sin(start_angle),
    )
    end = (
        center[0] + radius * math.cos(end_angle),
        center[1] + radius * math.sin(end_angle),
    )
    sweep_degrees = (end_deg - start_deg) % 360
    large_arc = 1 if sweep_degrees > 180 else 0
    return start, end, large_arc


def _svg_document(
    shapes: list[dict],
    bounds: list[tuple[float, float]],
    title: str = "CAD Preview",
) -> str:
    if not shapes or not bounds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No drawable DXF entities found. Supported entities: LINE, CIRCLE, ARC, LWPOLYLINE.",
        )

    min_x = min(point[0] for point in bounds)
    max_x = max(point[0] for point in bounds)
    min_y = min(point[1] for point in bounds)
    max_y = max(point[1] for point in bounds)
    width = max(max_x - min_x, 1)
    height = max(max_y - min_y, 1)

    elements = []
    for shape in shapes:
        kind = shape["kind"]
        if kind == "line":
            elements.append(_line(shape["start"], shape["end"], min_x, max_y))
        elif kind == "polyline":
            elements.append(
                _polyline(shape["vertices"], min_x, max_y, close=shape["close"])
            )
        elif kind == "circle":
            cx, cy = _transform(shape["center"], min_x, max_y)
            elements.append(
                f'<circle cx="{cx:g}" cy="{cy:g}" r="{shape["radius"]:g}" />'
            )
        elif kind == "arc":
            start, end, large_arc = _arc_points(
                shape["center"],
                shape["radius"],
                shape["start_deg"],
                shape["end_deg"],
            )
            sx, sy = _transform(start, min_x, max_y)
            ex, ey = _transform(end, min_x, max_y)
            elements.append(
                f'<path d="M {sx:g} {sy:g} A {shape["radius"]:g} {shape["radius"]:g} '
                f'0 {large_arc} 0 {ex:g} {ey:g}" />'
            )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" '
        'fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" '
        'stroke-linejoin="round">\n'
        f"<title>{escape(title)}</title>\n" + "\n".join(elements) + "\n</svg>\n"
    )


def convert_dxf_text_to_svg(text: str, title: str = "CAD Preview") -> str:
    pairs = _read_dxf_pairs(text)
    shapes: list[dict] = []
    bounds: list[tuple[float, float]] = []
    index = 0

    while index < len(pairs):
        code, value = pairs[index]
        if code != "0":
            index += 1
            continue

        entity_type = value.upper()
        fields, next_index = _entity_fields(pairs, index + 1)

        if entity_type == "LINE":
            start = (_float(fields, "10"), _float(fields, "20"))
            end = (_float(fields, "11"), _float(fields, "21"))
            shapes.append({"kind": "line", "start": start, "end": end})
            bounds.extend([start, end])
        elif entity_type == "CIRCLE":
            center = (_float(fields, "10"), _float(fields, "20"))
            radius = abs(_float(fields, "40"))
            shapes.append({"kind": "circle", "center": center, "radius": radius})
            bounds.extend(
                [
                    (center[0] - radius, center[1] - radius),
                    (center[0] + radius, center[1] + radius),
                ]
            )
        elif entity_type == "ARC":
            center = (_float(fields, "10"), _float(fields, "20"))
            radius = abs(_float(fields, "40"))
            start_deg = _float(fields, "50")
            end_deg = _float(fields, "51")
            shapes.append(
                {
                    "kind": "arc",
                    "center": center,
                    "radius": radius,
                    "start_deg": start_deg,
                    "end_deg": end_deg,
                }
            )
            bounds.extend(
                [
                    (center[0] - radius, center[1] - radius),
                    (center[0] + radius, center[1] + radius),
                ]
            )
        elif entity_type == "LWPOLYLINE":
            xs = _float_list(fields, "10")
            ys = _float_list(fields, "20")
            vertices = list(zip(xs, ys))
            if len(vertices) >= 2:
                flags = int(_float(fields, "70", 0))
                shapes.append(
                    {"kind": "polyline", "vertices": vertices, "close": bool(flags & 1)}
                )
                bounds.extend(vertices)

        index = max(next_index, index + 1)

    return _svg_document(shapes, bounds, title=title)


def _convert_dwg_with_external_tool(input_path: Path, output_path: Path) -> bool:
    command_template = os.getenv("DWG_TO_SVG_COMMAND") or os.getenv(
        "CAD_TO_SVG_COMMAND"
    )
    if not command_template:
        return False
    command = command_template.format(
        input=shlex.quote(str(input_path)),
        output=shlex.quote(str(output_path)),
    )
    subprocess.run(
        command, shell=True, check=True, timeout=60, capture_output=True, text=True
    )
    return output_path.exists()


def _convert_dwg_to_dxf_with_external_tool(input_path: Path, output_path: Path) -> bool:
    command_template = os.getenv("DWG_TO_DXF_COMMAND")
    if not command_template:
        return False
    command = command_template.format(
        input=shlex.quote(str(input_path)),
        output=shlex.quote(str(output_path)),
    )
    subprocess.run(
        command, shell=True, check=True, timeout=60, capture_output=True, text=True
    )
    return output_path.exists()


def _convert_dwg_to_dxf_with_libredwg(input_path: Path, output_path: Path) -> bool:
    binary = shutil.which("dwg2dxf")
    if binary:
        result = subprocess.run(
            [binary, "-o", str(output_path), str(input_path)],
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )
        if output_path.exists():
            return True

        if result.stdout.strip():
            output_path.write_text(result.stdout, encoding="utf-8")
            return True

    binary = shutil.which("dwgread")
    if not binary:
        return False

    subprocess.run(
        [binary, "-O", "DXF", "-o", str(output_path), str(input_path)],
        check=False,
        timeout=60,
        capture_output=True,
        text=True,
    )
    return output_path.exists()


async def convert_cad_upload_to_svg(file: UploadFile) -> str:
    filename = file.filename or "drawing"
    suffix = Path(filename).suffix.lower()
    content = await file.read()

    if suffix == ".svg":
        return content.decode("utf-8", errors="replace")

    if suffix == ".dxf":
        return convert_dxf_text_to_svg(
            content.decode("utf-8", errors="ignore"),
            title=filename,
        )

    if suffix == ".dwg":
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / filename
            output_svg_path = Path(tmp) / f"{Path(filename).stem}.svg"
            output_dxf_path = Path(tmp) / f"{Path(filename).stem}.dxf"
            input_path.write_bytes(content)
            try:
                if _convert_dwg_with_external_tool(input_path, output_svg_path):
                    return output_svg_path.read_text(encoding="utf-8", errors="replace")

                if _convert_dwg_to_dxf_with_external_tool(input_path, output_dxf_path):
                    return convert_dxf_text_to_svg(
                        output_dxf_path.read_text(encoding="utf-8", errors="ignore"),
                        title=filename,
                    )

                if _convert_dwg_to_dxf_with_libredwg(input_path, output_dxf_path):
                    return convert_dxf_text_to_svg(
                        output_dxf_path.read_text(encoding="utf-8", errors="ignore"),
                        title=filename,
                    )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"DWG converter failed: {exc}",
                ) from exc

        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "DWG conversion requires LibreDWG's dwg2dxf/dwgread or an external "
                "converter. Install libredwg-tools, configure DWG_TO_SVG_COMMAND or "
                "CAD_TO_SVG_COMMAND for SVG output, configure DWG_TO_DXF_COMMAND for "
                "DXF output, or upload DXF/SVG."
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Unsupported CAD file type. Upload DWG, DXF, or SVG.",
    )


import re as _re

_UNSAFE_FILENAME = _re.compile(r"[^A-Za-z0-9._-]")


def safe_filename_stem(filename: str | None) -> str:
    """Sanitise an attacker-controlled upload filename for use in a response header.

    `file.filename` was interpolated raw into Content-Disposition, so a quote or newline in
    the filename broke out of the quoted header value (header injection).
    """
    stem = (filename or "drawing").rsplit(".", 1)[0]
    stem = _UNSAFE_FILENAME.sub("_", stem)[:100]
    return stem or "drawing"
