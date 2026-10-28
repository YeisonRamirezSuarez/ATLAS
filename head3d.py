"""ATLAS's talking head for qml/Head3D.qml: ICT-FaceKit's face model (USC ICT, MIT, see
qml/face-LICENSE.txt), baked by tools/make_face.py into qml/face.npz. One Qt Quick 3D geometry per
part (skin, mouth = teeth/gums/tongue, sclera, iris), plus the skin's polygon edges as lines for the
wireframe, with the expressions as morph targets, so QML only sets weights and the GPU blends.
"""
from pathlib import Path

import numpy as np
from PyQt6.QtGui import QVector3D
from PyQt6.QtQuick3D import QQuick3DGeometry

NPZ = Path(__file__).resolve().parent / "qml" / "face.npz"
PARTS = ("skin", "mouth", "sclera", "iris")
# morph target order: Head3D.qml's morphTargets list follows it
TARGETS = ("jawOpen", "funnel", "pucker", "stretch", "upperUp", "lowerDown", "smile",
           "blink", "browUp", "lookLeft", "lookRight", "lookUp", "lookDown")

_data = None


def data():
    global _data
    if _data is None:
        with np.load(NPZ) as z:
            _data = {k: z[k] for k in z.files}
    return _data


class FaceGeometry(QQuick3DGeometry):
    def __init__(self, part: str, wire: bool = False, parent=None):
        super().__init__(parent)
        z = data()
        P, N, T = z[f"{part}_pos"], z[f"{part}_nrm"], z[f"{part}_edge" if wire else f"{part}_tri"]
        A = QQuick3DGeometry.Attribute
        F32 = A.ComponentType.F32Type
        self.setStride(6 * 4)
        self.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Lines if wire else QQuick3DGeometry.PrimitiveType.Triangles)
        self.addAttribute(A.Semantic.PositionSemantic, 0, F32)
        self.addAttribute(A.Semantic.NormalSemantic, 12, F32)
        self.addAttribute(A.Semantic.IndexSemantic, 0, A.ComponentType.U16Type)
        self.setVertexData(np.column_stack([P, N]).astype(np.float32).tobytes())
        self.setIndexData(T.astype(np.uint16).tobytes())
        # Qt blends toward each target's absolute positions (not deltas); every part gets every
        # target, unchanged where the expression doesn't touch it, so one weight list fits all.
        size = len(P) * 12
        for i, name in enumerate(TARGETS):
            self.addTargetAttribute(i, A.Semantic.PositionSemantic, i * size, 12)
        self.setTargetData(np.concatenate([P + z[f"{part}@{n}"].astype(np.float32) if f"{part}@{n}" in z else P
                                           for n in TARGETS]).astype(np.float32).tobytes())
        self.setBounds(QVector3D(*map(float, P.min(0))), QVector3D(*map(float, P.max(0))))


def geometries() -> dict:
    """Context properties for hud.qml: faceSkin, faceMouth, faceSclera, faceIris, and their wireframes
    faceSkinWire, faceMouthWire, faceScleraWire, faceIrisWire."""
    geo = {f"face{p.capitalize()}": FaceGeometry(p) for p in PARTS}
    geo.update({f"face{p.capitalize()}Wire": FaceGeometry(p, wire=True) for p in PARTS})
    return geo
