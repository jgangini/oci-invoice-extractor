"""Embed the local OCI toolkit outlines in a disposable Archify renderer."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parents[1]
SVG = "{http://www.w3.org/2000/svg}"
ORACLE = "https://docs.oracle.com/en-us/iaas/Content/General/Reference/graphicsfordiagrams.htm"


def load_mark(asset, title):
    svg = ElementTree.fromstring(asset.read_bytes())
    expected = {
        "svg": {"viewBox", "width", "height", "preserveAspectRatio"},
        "g": {"transform"},
        "path": {"d", "fill", "stroke", "stroke-width", "transform"},
    }
    for element in svg.iter():
        tag = element.tag.removeprefix(SVG)
        if element.tag != SVG + tag or tag not in expected or set(element.attrib) - expected[tag]:
            raise ValueError(f"Unexpected SVG markup in {asset.name}")
        element.tag = tag
        if tag == "path":
            if element.get("fill") != "#2d5967" or element.get("stroke") != "none":
                raise ValueError(f"Unexpected paint in {asset.name}")
            element.set("fill", "currentColor")
    x, y, width, height = map(float, svg.attrib["viewBox"].split())
    scale = 20 / max(width, height)
    offset_x = (20 - width * scale) / 2 - x * scale
    offset_y = (20 - height * scale) / 2 - y * scale
    outline = ElementTree.Element("g", {
        "class": "oci-outline",
        "transform": f"translate({offset_x:g} {offset_y:g}) scale({scale:g})",
    })
    outline.extend(svg)
    return {
        "id": asset.stem,
        "title": title,
        "category": "cloud",
        "aliases": [],
        "domains": [],
        "provenance": {"provider": "Oracle", "providerVersion": "24.2", "source": ORACLE},
        "outline": ElementTree.tostring(outline, encoding="unicode"),
    }


def replace_once(path, original, replacement):
    text = path.read_text(encoding="utf-8")
    if text.count(original) != 1:
        raise ValueError(f"Archify renderer changed at {path.name}")
    path.write_text(text.replace(original, replacement), encoding="utf-8")


def main():
    installed = Path(sys.argv[1]).resolve()
    candidate = DOCS / "oci.architecture.json"
    diagram = json.loads(candidate.read_text(encoding="utf-8"))
    output = diagram["meta"]["output"]
    marks = [
        load_mark(DOCS / "assets" / f"{node['brand']}.svg", node["label"])
        for node in diagram["components"] if "brand" in node
    ]
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="archify-oci-", dir=build) as temp:
        runtime = Path(temp) / "archify"
        shutil.copytree(installed, runtime)
        shared = runtime / "renderers" / "shared"
        catalog = shared / "generated-brand-marks.mjs"
        replace_once(catalog, "Object.freeze([", "Object.freeze([" + json.dumps(marks)[1:-1] + ",")
        replace_once(
            shared / "brand-marks.mjs", "  const inset = 3;",
            """  if (mark.outline) {
    return `<g aria-hidden="true" ${markAttrs(mark)} class="brand-mark" transform="translate(${x - 4} ${y})">${mark.outline}</g>`;
  }
  const inset = 3;""",
        )
        replace_once(
            runtime / "assets" / "template.html", "    svg .brand-mark { pointer-events: none; }",
            """    svg .brand-mark { pointer-events: none; }
    :root, [data-theme="dark"] { --oci-outline-ink: #fff; }
    [data-theme="light"] { --oci-outline-ink: #243649; }
    svg .oci-outline { color: var(--oci-outline-ink, #fff); }""",
        )
        architecture = runtime / "renderers" / "architecture" / "render-architecture.mjs"
        replace_once(architecture, "sublabelPreferred: 9,", "sublabelPreferred: 10,")
        replace_once(architecture, "brandLabelFitWidth(c, c.width), 11, 8)", "brandLabelFitWidth(c, c.width), 13, 8)")
        replace_once(architecture, "textUnits(conn.label) * 4.8 + 10", "textUnits(conn.label) * 5.7 + 10")
        replace_once(architecture, "y: ly - 10, width, height: 14", "y: ly - 11, width, height: 16")
        replace_once(architecture, 'font-size="8" text-anchor="middle">${esc(conn.label)}', 'font-size="9.5" text-anchor="middle">${esc(conn.label)}')
        return subprocess.call([
            "node", str(runtime / "bin" / "archify.mjs"), "finalize", "architecture",
            str(candidate.relative_to(ROOT)), output, "--repo-root", str(ROOT),
            "--quality", diagram["meta"]["quality_profile"], "--json", *sys.argv[2:],
        ], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
