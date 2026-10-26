"""Small 2-layer grid autorouter for low-density boards (written because running an external
autorouter was not permitted). KiCad DRC is the judge of its output.

Model: two copper layers rasterised on a square grid. For each net, pads are joined one at a
time (nearest unconnected pad first) by an A* search from that pad to any centreline/pad cell
already belonging to the net. Copper of other nets is inflated by clearance + this track's
half width; vias need their own clearance on both layers. Routed tracks are stamped back so
later nets avoid them. F.Cu prefers horizontal, B.Cu vertical moves.
"""
import heapq
import math

import numpy as np
import pcbnew

from .kicad_pcb import RULES, mm

FREE = -1
KEEPOUT = -2
DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]


class RouteError(RuntimeError):
    pass


def _disk(r):
    k = int(math.ceil(r))
    return [(dx, dy) for dx in range(-k, k + 1) for dy in range(-k, k + 1) if dx * dx + dy * dy <= r * r + 0.25]


def _dilate(mask, r):
    if r <= 0:
        return mask.copy()
    out = np.zeros_like(mask)
    h, w = mask.shape
    for dx, dy in _disk(r):
        ys, ye = max(0, dy), h + min(0, dy)
        xs, xe = max(0, dx), w + min(0, dx)
        out[ys:ye, xs:xe] |= mask[ys - dy:ye - dy, xs - dx:xe - dx]
    return out


class Grid:
    def __init__(self, board, pitch):
        self.board = board
        self.pitch = pitch
        bb = board.GetBoardEdgesBoundingBox()
        self.x0, self.y0 = pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY())
        self.w = int(pcbnew.ToMM(bb.GetWidth()) / pitch) + 1
        self.h = int(pcbnew.ToMM(bb.GetHeight()) / pitch) + 1
        self.copper = [np.full((self.h, self.w), FREE, np.int32) for _ in range(2)]   # net code
        self.anchor = [np.full((self.h, self.w), FREE, np.int32) for _ in range(2)]   # connectable cells

    def cell(self, x_mm, y_mm):
        return int(round((y_mm - self.y0) / self.pitch)), int(round((x_mm - self.x0) / self.pitch))

    def pos(self, r, c):
        return self.x0 + c * self.pitch, self.y0 + r * self.pitch


LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)


def _stamp_pads(g):
    pads = []
    for fp in g.board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            r0, c0 = g.cell(pcbnew.ToMM(bb.GetX()), pcbnew.ToMM(bb.GetY()))
            r1, c1 = g.cell(pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom()))
            code = pad.GetNetCode() if pad.GetNetCode() > 0 else KEEPOUT
            cells = []
            for r in range(max(0, r0), min(g.h, r1 + 1)):
                for c in range(max(0, c0), min(g.w, c1 + 1)):
                    x, y = g.pos(r, c)
                    if pad.HitTest(pcbnew.VECTOR2I(mm(x), mm(y))):
                        cells.append((r, c))
            if not cells:  # tiny pad: use its centre
                p = pad.GetPosition()
                cells = [g.cell(pcbnew.ToMM(p.x), pcbnew.ToMM(p.y))]
            on = [i for i, L in enumerate(LAYERS) if pad.IsOnLayer(L)]
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                on, code = [0, 1], KEEPOUT
            for i in on:
                for r, c in cells:
                    g.copper[i][r, c] = code
                    if code > 0:
                        g.anchor[i][r, c] = code
            if code > 0:
                pads.append((pad, code, on, cells))
    return pads


def _edge_keepout(g, clearance):
    k = int(math.ceil(clearance / g.pitch)) + 1
    for i in range(2):
        for arr in (g.copper[i],):
            arr[:k, :] = KEEPOUT; arr[-k:, :] = KEEPOUT; arr[:, :k] = KEEPOUT; arr[:, -k:] = KEEPOUT


def _astar(g, net, sources, goal_pt, blocked, via_blocked, via_cost):
    """sources: [(layer, r, c)]. Goal: any anchor cell of `net` not in the source pad."""
    h_, w_ = g.h, g.w
    src_set = set(sources)
    gr, gc = goal_pt
    dist = {}
    prev = {}
    heap = []
    for s in sources:
        dist[s] = 0.0
        heapq.heappush(heap, (math.hypot(s[1] - gr, s[2] - gc), 0.0, s))
    anchors = g.anchor
    while heap:
        f, d, st = heapq.heappop(heap)
        if d > dist.get(st, 1e18):
            continue
        L, r, c = st
        if st not in src_set and anchors[L][r, c] == net:
            path = [st]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        for dr, dc, cost in DIRS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h_ and 0 <= nc < w_) or blocked[L][nr, nc]:
                continue
            pref = 0.0
            if (L == 0 and dr != 0 and dc == 0) or (L == 1 and dc != 0 and dr == 0):
                pref = 0.35
            nd = d + cost + pref
            ns = (L, nr, nc)
            if nd < dist.get(ns, 1e18):
                dist[ns] = nd; prev[ns] = st
                heapq.heappush(heap, (nd + math.hypot(nr - gr, nc - gc), nd, ns))
        if not via_blocked[r, c]:
            ns = (1 - L, r, c)
            nd = d + via_cost
            if not blocked[1 - L][r, c] and nd < dist.get(ns, 1e18):
                dist[ns] = nd; prev[ns] = st
                heapq.heappush(heap, (nd + math.hypot(r - gr, c - gc), nd, ns))
    return None


def _simplify(path):
    """Cell path -> list of (layer, [points]) runs and via positions."""
    runs, vias = [], []
    cur = [path[0]]
    for st in path[1:]:
        if st[0] != cur[-1][0]:
            runs.append(cur); vias.append(st[1:]); cur = [st]
        else:
            cur.append(st)
    runs.append(cur)
    out = []
    for run in runs:
        pts = [run[0][1:]]
        for i in range(1, len(run) - 1):
            a, b, c = run[i - 1][1:], run[i][1:], run[i + 1][1:]
            if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
                pts.append(b)
        if len(run) > 1:
            pts.append(run[-1][1:])
        out.append((run[0][0], pts))
    return out, vias


def autoroute(pcb_path, circuit=None, pitch=0.1, rules=RULES, net_widths=None, order=None, via_cost=12.0,
              skip_nets=()):
    """Route every net of the board in place. net_widths: net -> track width (mm)."""
    board = pcbnew.LoadBoard(str(pcb_path))
    net_widths = net_widths or {}
    g = Grid(board, pitch)
    _edge_keepout(g, rules["edge_clear"])
    pads = _stamp_pads(g)
    by_net = {}
    for pad, code, on, cells in pads:
        by_net.setdefault(code, []).append((pad, on, cells))
    names = {code: board.FindNet(code).GetNetname() for code in by_net}
    nets = sorted(by_net, key=lambda n: (-(net_widths.get(names[n], rules["track"])), len(by_net[n])))
    if order:
        nets.sort(key=lambda n: order.index(names[n]) if names[n] in order else len(order))
    via_r = rules["via_d"] / 2
    failures = []
    for net in nets:
        name = names[net]
        if name in skip_nets or len(by_net[net]) < 2:
            continue
        width = net_widths.get(name, rules["track"])
        hw = width / 2
        others = [(g.copper[i] != FREE) & (g.copper[i] != net) for i in range(2)]
        margin = 1.5 * pitch  # rasterisation error of pads and diagonal tracks
        blocked = [_dilate(others[i], (rules["clearance"] + hw + margin) / pitch) for i in range(2)]
        via_blk = _dilate(others[0] | others[1], (rules["clearance"] + via_r + margin) / pitch)
        # keep vias out of pads of this net too (no via-in-pad for hand soldering)
        own_pads = np.zeros((g.h, g.w), bool)
        for pad, on, cells in by_net[net]:
            for r, c in cells:
                own_pads[r, c] = True
        via_blk |= _dilate(own_pads, via_r / pitch)
        plist = by_net[net]
        # reset this net's anchors to pad cells only for the first pad; others join as connected
        connected = [plist[0]]
        todo = plist[1:]
        for i in range(2):
            g.anchor[i][g.anchor[i] == net] = FREE
        for i in plist[0][1]:
            for r, c in plist[0][2]:
                g.anchor[i][r, c] = net
        while todo:
            def pc(p):
                q = p[0].GetPosition(); return pcbnew.ToMM(q.x), pcbnew.ToMM(q.y)
            best = min(todo, key=lambda p: min(math.dist(pc(p), pc(q)) for q in connected))
            target = min(connected, key=lambda q: math.dist(pc(best), pc(q)))
            todo.remove(best)
            centre = g.cell(*pc(best))
            sources = [(L, *centre) for L in best[1]]
            path = _astar(g, net, sources, g.cell(*pc(target)), blocked, via_blk, via_cost)
            if path is None:
                failures.append(f"{name}: {best[0].GetParentFootprint().GetReference()}.{best[0].GetNumber()}")
                continue
            _commit(board, g, net, path, width, via_r, best[0], blocked, via_blk, rules, pitch)
            connected.append(best)
            for L in best[1]:
                for r, c in best[2]:
                    g.anchor[L][r, c] = net
    pcbnew.SaveBoard(str(pcb_path), board)
    if failures:
        raise RouteError("could not route: " + ", ".join(failures))
    return pcb_path


def _commit(board, g, net, path, width, via_r, src_pad, blocked, via_blk, rules, pitch):
    netinfo = board.FindNet(net)
    runs, vias = _simplify(path)
    hw_cells = (width / 2) / pitch
    for L, pts in runs:
        layer = LAYERS[L]
        coords = [g.pos(r, c) for r, c in pts]
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0))); t.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
            t.SetWidth(mm(width)); t.SetLayer(layer); t.SetNet(netinfo); board.Add(t)
        line = np.zeros((g.h, g.w), bool)
        for (r0, c0), (r1, c1) in zip(pts, pts[1:]):
            n = max(abs(r1 - r0), abs(c1 - c0))
            for k in range(n + 1):
                line[r0 + (r1 - r0) * k // max(n, 1), c0 + (c1 - c0) * k // max(n, 1)] = True
        if len(pts) == 1:
            line[pts[0]] = True
        g.copper[L][_dilate(line, hw_cells) & (g.copper[L] == FREE)] = net
        g.anchor[L][line] = net
    # join the first grid point to the exact pad centre
    first_L, (fr, fc) = runs[0][0], runs[0][1][0]
    p = src_pad.GetPosition()
    fx, fy = g.pos(fr, fc)
    if abs(pcbnew.ToMM(p.x) - fx) > 1e-4 or abs(pcbnew.ToMM(p.y) - fy) > 1e-4:
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(p); t.SetEnd(pcbnew.VECTOR2I(mm(fx), mm(fy)))
        t.SetWidth(mm(width)); t.SetLayer(LAYERS[first_L]); t.SetNet(netinfo); board.Add(t)
    for r, c in vias:
        x, y = g.pos(r, c)
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        v.SetWidth(mm(rules["via_d"])); v.SetDrill(mm(rules["via_drill"])); v.SetNet(netinfo)
        board.Add(v)
        disk = np.zeros((g.h, g.w), bool); disk[r, c] = True
        d = _dilate(disk, via_r / pitch)
        for i in range(2):
            g.copper[i][d & (g.copper[i] == FREE)] = net
            g.anchor[i][r, c] = net
