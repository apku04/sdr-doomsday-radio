"""Diagnostics: GND pour islands and how they are linked (vias, through-hole pads, tracks)."""
import pcbnew


def island_groups(pcb_path, net="GND"):
    b = pcbnew.LoadBoard(str(pcb_path))
    isl = []   # (layer, polyset)
    for z in b.Zones():
        if z.GetNetname() != net:
            continue
        L = z.GetLayer(); ps = z.GetFilledPolysList(L)
        for i in range(ps.OutlineCount()):
            one = pcbnew.SHAPE_POLY_SET(); one.AddOutline(ps.Outline(i))
            isl.append((L, one))
    parent = list(range(len(isl)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a

    def union(a, b_):
        parent[find(a)] = find(b_)

    def hits(pt, layers):
        return [k for k, (L, ps) in enumerate(isl) if L in layers and ps.Contains(pt)]

    items = {k: [] for k in range(len(isl))}
    both = (pcbnew.F_Cu, pcbnew.B_Cu)
    for t in b.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.GetClass() == "PCB_VIA":
            h = hits(t.GetPosition(), both)
        else:
            h = hits(t.GetStart(), (t.GetLayer(),)) + hits(t.GetEnd(), (t.GetLayer(),))
        for k in h:
            items[k].append(t.GetClass())
        for k in h[1:]:
            union(h[0], k)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != net:
                continue
            h = hits(p.GetPosition(), [L for L in both if p.IsOnLayer(L)])
            for k in h:
                items[k].append(f"{fp.GetReference()}.{p.GetNumber()}")
            for k in h[1:]:
                union(h[0], k)
    groups = {}
    for k in range(len(isl)):
        if items[k]:
            groups.setdefault(find(k), []).append(k)
    out = []
    for g, ks in groups.items():
        out.append([(pcbnew.LayerName(isl[k][0]), [round(pcbnew.ToMM(v), 1) for v in
                     (isl[k][1].BBox().GetLeft(), isl[k][1].BBox().GetTop(), isl[k][1].BBox().GetRight(),
                      isl[k][1].BBox().GetBottom())], items[k][:6]) for k in ks])
    return sorted(out, key=len, reverse=True)
