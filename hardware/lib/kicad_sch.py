"""Write a KiCad 9 schematic from a Circuit: library symbols placed on a grid, one net label
on every connected pin end (no wires), no-connect flags on NC pins, PWR_FLAGs where needed.
Connectivity is checked by exporting the netlist with kicad-cli."""
import copy
import json
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from .circuit import NC
from .sexpr import Sym, dump, find, find_all, parse

KICAD = Path(r"C:\Program Files\KiCad\9.0")
KICAD_CLI = str(KICAD / "bin" / "kicad-cli.exe")
SYMBOL_DIR = KICAD / "share" / "kicad" / "symbols"
GRID = 2.54

_lib_cache = {}


def _u():
    return str(uuid.uuid4())


def _lib(name):
    if name not in _lib_cache:
        tree = parse((SYMBOL_DIR / f"{name}.kicad_sym").read_text(encoding="utf-8"))
        _lib_cache[name] = {s[1]: s for s in find_all(tree, "symbol")}
    return _lib_cache[name]


def _flat_symbol(lib, name):
    """Symbol definition with 'extends' resolved, top name still the bare library name."""
    sym = copy.deepcopy(_lib(lib)[name])
    ext = find(sym, "extends")
    if not ext:
        return sym
    parent = _flat_symbol(lib, ext[1])
    child_props = {p[1]: p for p in find_all(sym, "property")}
    out = [Sym("symbol"), name]
    for item in parent[2:]:
        if isinstance(item, list) and item and item[0] == "property" and item[1] in child_props:
            out.append(child_props.pop(item[1]))
        elif isinstance(item, list) and item and item[0] == "symbol":
            sub = copy.deepcopy(item)
            sub[1] = name + sub[1][len(ext[1]):]
            out.append(sub)
        else:
            out.append(item)
    out.extend(child_props.values())
    return out


class SymbolInfo:
    def __init__(self, lib_id):
        lib, name = lib_id.split(":", 1)
        self.lib_id = lib_id
        flat = _flat_symbol(lib, name)
        self.power = find(flat, "power") is not None
        self.pins = {}   # number -> (x, y, rot, etype)
        xs, ys = [0.0], [0.0]
        for sub in find_all(flat, "symbol"):
            m = re.match(r".*_(\d+)_(\d+)$", sub[1])
            unit, body = int(m.group(1)), int(m.group(2))
            if unit not in (0, 1) or body not in (0, 1):
                continue
            for pin in find_all(sub, "pin"):
                at = find(pin, "at")
                x, y, rot = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
                num = find(pin, "number")[1]
                self.pins[str(num)] = (x, y, rot, str(pin[1]))
                xs.append(x); ys.append(y)
            for shape in find_all(sub, "rectangle"):
                for k in ("start", "end"):
                    p = find(shape, k); xs.append(float(p[1])); ys.append(float(p[2]))
        self.bbox = (min(xs), min(ys), max(xs), max(ys))   # symbol coords, y up
        flat[1] = lib_id
        self.definition = flat


def _snap(v):
    return round(v / GRID) * GRID


def _prop(name, value, x, y, hide=False, justify=None):
    eff = [Sym("effects"), [Sym("font"), [Sym("size"), 1.27, 1.27]]]
    if justify:
        eff.append([Sym("justify"), Sym(justify)])
    if hide:
        eff.append([Sym("hide"), Sym("yes")])
    return [Sym("property"), name, value, [Sym("at"), x, y, 0], eff]


def _label(net, x, y, angle):
    just = {0: "left", 180: "right", 90: "left", 270: "right"}[angle]
    return [Sym("label"), net, [Sym("at"), x, y, angle],
            [Sym("effects"), [Sym("font"), [Sym("size"), 1.0, 1.0]], [Sym("justify"), Sym(just), Sym("bottom")]],
            [Sym("uuid"), _u()]]


def _instance(sym_info, ref, value, footprint, x, y, part_uuid, project, root, extra_props=()):
    bx0, by0, bx1, by1 = sym_info.bbox
    node = [Sym("symbol"), [Sym("lib_id"), sym_info.lib_id], [Sym("at"), x, y, 0], [Sym("unit"), 1],
            [Sym("exclude_from_sim"), Sym("no")], [Sym("in_bom"), Sym("no" if ref.startswith("#") else "yes")],
            [Sym("on_board"), Sym("no" if ref.startswith("#") else "yes")], [Sym("dnp"), Sym("no")],
            [Sym("uuid"), part_uuid],
            _prop("Reference", ref, x + bx0, y - by1 - 1.5, hide=ref.startswith("#"), justify="left"),
            _prop("Value", value, x + bx0, y - by0 + 2.5, justify="left"),
            _prop("Footprint", footprint, x, y, hide=True),
            _prop("Datasheet", "", x, y, hide=True),
            _prop("Description", "", x, y, hide=True)]
    for k, v in extra_props:
        node.append(_prop(k, v, x, y, hide=True))
    for num in sym_info.pins:
        node.append([Sym("pin"), num, [Sym("uuid"), _u()]])
    node.append([Sym("instances"), [Sym("project"), project,
                                    [Sym("path"), f"/{root}", [Sym("reference"), ref], [Sym("unit"), 1]]]])
    return node


def _pin_point(x, y, pin):
    px, py, rot, _ = pin
    return round(x + px, 4), round(y - py, 4), int(round(rot)) % 360


def write_schematic(circuit, path, title=""):
    root = _u()
    project = Path(path).stem
    infos = {}
    for p in circuit.parts:
        infos.setdefault(p.symbol, SymbolInfo(p.symbol))
    flag_info = SymbolInfo("power:PWR_FLAG")

    # which nets need a PWR_FLAG: they feed a power_in pin but nothing drives them
    types = {}
    for p in circuit.parts:
        for num, net in p.pins.items():
            if net is NC:
                continue
            pin = infos[p.symbol].pins.get(str(num))
            if pin is None:
                raise ValueError(f"{p.ref}: symbol {p.symbol} has no pin {num}")
            types.setdefault(net, set()).add(pin[3])
    flag_nets = [n for n, t in types.items() if "power_in" in t and "power_out" not in t]

    items, x, y, row_h = [], 25.4, 30.48, 0.0
    page_w = 560.0
    groups = []
    for p in circuit.parts:
        if p.group not in groups:
            groups.append(p.group)
    order = [p for g in groups for p in circuit.parts if p.group == g]
    last_group = None
    for p in order:
        info = infos[p.symbol]
        bx0, by0, bx1, by1 = info.bbox
        w, h = bx1 - bx0 + 30, by1 - by0 + 14
        if (last_group is not None and p.group != last_group) or x + w > page_w:
            x, y, row_h = 25.4, y + row_h + 10, 0.0
        last_group = p.group
        sx, sy = _snap(x - bx0 + 12), _snap(y + by1 + 6)
        extra = [("LCSC", p.lcsc)] if p.lcsc else []
        items.append(_instance(info, p.ref, p.value, p.footprint, sx, sy, p.uuid, project, root, extra))
        for num, net in p.pins.items():
            px, py, rot = _pin_point(sx, sy, info.pins[str(num)])
            if net is NC:
                items.append([Sym("no_connect"), [Sym("at"), px, py], [Sym("uuid"), _u()]])
            else:
                items.append(_label(net, px, py, (rot + 180) % 360))
        x += w
        row_h = max(row_h, h)

    x, y = 25.4, y + row_h + 15
    for i, net in enumerate(flag_nets):
        sx, sy = _snap(x + i * 25.4), _snap(y)
        items.append(_instance(flag_info, f"#FLG{i + 1:02d}", "PWR_FLAG", "", sx, sy, _u(), project, root))
        px, py, rot = _pin_point(sx, sy, flag_info.pins["1"])
        items.append(_label(net, px, py, 270))

    libs = [i.definition for i in infos.values()] + ([flag_info.definition] if flag_nets else [])
    doc = [Sym("kicad_sch"), [Sym("version"), Sym("20250114")], [Sym("generator"), "eeschema"],
           [Sym("generator_version"), "9.0"], [Sym("uuid"), root], [Sym("paper"), "A2"],
           [Sym("title_block"), [Sym("title"), title or circuit.name]],
           [Sym("lib_symbols")] + libs] + items + [
           [Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]], [Sym("embedded_fonts"), Sym("no")]]
    Path(path).write_text(dump(doc) + "\n", encoding="utf-8")
    return path


def _cli(*args):
    r = subprocess.run([KICAD_CLI, *map(str, args)], capture_output=True, text=True)
    if r.returncode not in (0, 5):  # 5 = violations found (erc/drc --exit-code-violations not used)
        raise RuntimeError(f"kicad-cli {' '.join(map(str, args))} failed: {r.stderr or r.stdout}")
    return r


def sch_netlist(path):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        _cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, path)
        tree = parse(out.read_text(encoding="utf-8"))
    nets = {}
    for net in find_all(find(tree, "nets"), "net"):
        name = find(net, "name")[1].lstrip("/")
        if name.startswith("unconnected-"):
            continue
        nodes = sorted((find(n, "ref")[1], find(n, "pin")[1]) for n in find_all(net, "node")
                       if not find(n, "ref")[1].startswith("#"))
        if nodes:
            nets[name] = nodes
    return dict(sorted(nets.items()))


def run_erc(path, include_warnings=False):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "erc.json"
        _cli("sch", "erc", "--format", "json", "--units", "mm", "-o", out, path)
        rep = json.loads(out.read_text(encoding="utf-8"))
    probs = []
    for sheet in rep.get("sheets", []):
        for v in sheet.get("violations", []):
            if v["severity"] == "error" or include_warnings:
                where = "; ".join(i.get("description", "") for i in v.get("items", []))
                probs.append(f"{v['severity']}: {v['type']}: {v['description']} [{where}]")
    return probs
