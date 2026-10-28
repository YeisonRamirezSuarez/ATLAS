"""Bakes qml/face.npz (the HUD's talking head, head3d.py) from ICT-FaceKit's light model
(https://github.com/USC-ICT/ICT-FaceKit, MIT): python tools/make_face.py <ICT-FaceKit>/FaceXModel

Keeps the skin, teeth, gums/tongue and eyeballs (drops the transparent eye helper meshes and lashes),
triangulates, and stores per part: positions, normals, triangles, polygon edges, and position deltas for the
expressions the HUD animates (left/right ARKit shapes summed where the HUD moves both together)."""
import sys
from pathlib import Path

import numpy as np

SRC = Path(sys.argv[1])
OUT = Path(__file__).resolve().parent.parent / "qml" / "face.npz"
PARTS = {"skin": ["M_Face", "M_BackHead"], "mouth": ["M_GumsTongue", "M_Teeth"],
         "sclera": ["M_ScleraLeft", "M_ScleraRight"], "iris": ["M_IrisLeft", "M_IrisRight"]}
SHAPES = {   # HUD name: ARKit shapes summed
    "jawOpen": ["jawOpen"], "funnel": ["mouthFunnel"], "pucker": ["mouthPucker"],
    "stretch": ["mouthStretch_L", "mouthStretch_R"], "upperUp": ["mouthUpperUp_L", "mouthUpperUp_R"],
    "lowerDown": ["mouthLowerDown_L", "mouthLowerDown_R"], "smile": ["mouthSmile_L", "mouthSmile_R"],
    "blink": ["eyeBlink_L", "eyeBlink_R"], "browUp": ["browInnerUp_L", "browInnerUp_R", "browOuterUp_L", "browOuterUp_R"],
    "lookLeft": ["eyeLookOut_L", "eyeLookIn_R"], "lookRight": ["eyeLookIn_L", "eyeLookOut_R"],
    "lookUp": ["eyeLookUp_L", "eyeLookUp_R"], "lookDown": ["eyeLookDown_L", "eyeLookDown_R"]}


def slimmer_nose(P):
    """The generic ICT nose reads broad as a hologram: narrow it ~32% (its wings ~50%), shorten it a little and pull its tip back,
    fading out smoothly (nose tip ~(0, 0.1, 13.1) cm, front of the face only)."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    w = np.exp(-((x / 2.6) ** 2 + ((y - 0.8) / 2.6) ** 2)) * np.clip((z - 9.5) / 1.5, 0, 1)
    wings = np.exp(-((x / 2.2) ** 2 + ((y - 0.3) / 1.4) ** 2)) * np.clip((z - 9.0) / 1.5, 0, 1)   # the flared nostrils
    Q = P.copy()
    Q[:, 0] = x * (1 - 0.32 * w) * (1 - 0.28 * wings)
    Q[:, 1] = y + (0.8 - y) * 0.12 * w                     # a touch shorter, toward its middle
    Q[:, 2] = z - 0.3 * w * np.maximum(z - 9.5, 0)
    return Q


def read(path, faces=False):
    v, f, e, mat = [], {}, {}, None
    for line in open(path, encoding="utf-8"):
        if line.startswith("v "):
            v.append([float(x) for x in line.split()[1:4]])
        elif faces and line.startswith("usemtl"):
            mat = line.split()[1]
        elif faces and line.startswith("f "):
            ids = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
            f.setdefault(mat, []).extend([ids[0], ids[k], ids[k + 1]] for k in range(1, len(ids) - 1))
            e.setdefault(mat, []).extend(sorted((ids[k], ids[(k + 1) % len(ids)])) for k in range(len(ids)))
    return (np.array(v, np.float32), f, e) if faces else (np.array(v, np.float32), f)


base, faces, edges = read(SRC / "generic_neutral_mesh.obj", faces=True)
deltas = {name: sum(read(SRC / f"{s}.obj")[0] - base for s in shapes) for name, shapes in SHAPES.items()}
slim = slimmer_nose(base)
deltas = {name: slimmer_nose(base + d) - slim for name, d in deltas.items()}
base = slim
out = {}
for part, mats in PARTS.items():
    tris = np.array([t for m in mats for t in faces[m]], np.int64)
    used = np.unique(tris)
    remap = np.full(len(base), -1, np.int64)
    remap[used] = np.arange(len(used))
    P, T = base[used], remap[tris]
    fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    N = np.zeros_like(P)
    for k in range(3):
        np.add.at(N, T[:, k], fn)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
    out[f"{part}_pos"], out[f"{part}_nrm"], out[f"{part}_tri"] = P, N.astype(np.float32), T.astype(np.uint16)
    # the polygon edges (quads, not the triangulation's diagonals): the wireframe drawn over the skin
    E = np.unique(np.array([x for m in mats for x in edges[m]], np.int64), axis=0)
    out[f"{part}_edge"] = remap[E].astype(np.uint16)
    for name, d in deltas.items():
        dd = d[used]
        if np.abs(dd).max() > 1e-4:
            out[f"{part}@{name}"] = dd.astype(np.float16)
np.savez_compressed(OUT, **out)
print({k: v.shape for k, v in out.items() if k.endswith("_pos")}, "targets:", sorted({k.split("@")[1] for k in out if "@" in k}))
print("bounds", base.min(0), base.max(0), OUT.stat().st_size // 1024, "KB")
