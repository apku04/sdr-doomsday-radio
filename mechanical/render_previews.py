"""Shaded PNG previews of the STLs:  uv run --with numpy --with matplotlib mechanical/render_previews.py"""
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

OUT = Path(__file__).resolve().parent / "out"


def load(name):
    d = (OUT / name).read_bytes()
    n = struct.unpack_from("<I", d, 80)[0]
    a = np.frombuffer(d, dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]), count=n, offset=84)
    return a["v"].reshape(-1, 3, 3).astype(float)


def view(tris, elev, azim, path, title, color=(0.25, 0.27, 0.3), extra=None):
    fig = plt.figure(figsize=(9, 6), dpi=130)
    ax = fig.add_subplot(projection="3d", proj_type="ortho")
    light = np.array([0.4, -0.5, 0.75]); light /= np.linalg.norm(light)
    groups = [(tris, color)] + ([extra] if extra else [])
    allpts = np.concatenate([g[0].reshape(-1, 3) for g in groups])
    for t, col in groups:
        nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        shade = 0.35 + 0.65 * np.clip(np.abs(nrm @ light), 0, 1)
        fc = np.clip(np.outer(shade, col), 0, 1)
        ax.add_collection3d(Poly3DCollection(t, facecolors=fc, edgecolors="none"))
    lo, hi = allpts.min(0), allpts.max(0)
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(hi - lo)
    ax.view_init(elev=elev, azim=azim); ax.set_axis_off(); ax.set_title(title)
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)


panel, labels = load("panel.stl"), load("panel_labels.stl")
tray, lid = load("tray.stl"), load("lid.stl")
# panel is stored face-down; flip back so the front faces the viewer (+Z)
def flip(t):
    t = t.copy(); t[..., 1] = 90 - t[..., 1]; t[..., 2] = -t[..., 2]; return t
view(flip(panel), 90, -90, OUT / "preview_panel_front_v3.png", "Front panel (front, labels inlaid)",
     extra=(flip(labels) + [0, 0, 0.05], (0.95, 0.95, 0.95)))
# tray as printed = as used (bottom down); Y = depth (back at max Y)
view(tray, 35, -60, OUT / "preview_tray.png", "Tray (open top + front): Pi standoffs, lid columns, ribs")
view(tray, 20, 115, OUT / "preview_tray_back.png", "Tray back wall: USB/LAN window, USB-C, LNA jack")
lid_up = lid.copy(); lid_up[..., 2] = -lid_up[..., 2]
view(lid_up, -35, -60, OUT / "preview_lid.png", "Lid (underside): lip, panel-screw bosses, vents")
print("ok")
