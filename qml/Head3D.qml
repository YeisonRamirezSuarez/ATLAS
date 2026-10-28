import QtQuick
import QtQuick3D

// ATLAS's face while it talks: ICT-FaceKit's head (head3d.py) as a wireframe hologram (shaders/head.*): a
// translucent skin with its polygon mesh lit over it, eyes included. It forms
// out of the neural sphere (morph). Lips follow the sounds (ui.mouth_shapes: a opens the jaw, o/u round
// the lips, e/i/s spread them, quiet closes), each on a damped spring so they flow; stressed syllables nod the
// head and lift the brows, new phrases often start with a blink; the eyes glance, the head drifts. Expressions are morph
// targets (order: head3d.TARGETS) blended on the GPU. Loaded by hud.qml through a Loader, so without
// Qt Quick 3D the HUD keeps the neural net alone.
//
// No blending on purpose: as an opaque material only the nearest surface survives the depth test, and
// its premultiplied colour and alpha land as-is in View3D's own texture, which Qt Quick then blends
// over the HUD. Blended in 3D, the layers behind the skin showed through as bright patches.
View3D {
    id: v
    property real morph: 0
    property real level: 0             // voice loudness now, 0..1
    property var shape: [0, 0, 0]      // ui.mouth_shapes targets now: jaw (open), round (o, u), wide (e, i, s)
    property real time: 0
    property color col: "#8ff0ff"
    property color hot: "#ffffff"

    // expression state: the jaw and the head ride damped springs (they have mass: a little
    // overshoot, no snapping); stressed syllables (onsets) kick a nod, lift the brows, may blink
    property real jaw: 0
    property real jawV: 0
    property real round: 0
    property real roundV: 0
    property real wide: 0
    property real wideV: 0
    property real emph: 0              // slow loudness envelope
    property real brow: 0
    property real lastOnset: -10
    property real quietSince: 0
    property real blink: 0
    property real blinkT0: -10
    property real nextBlink: 2
    property real gx: 0                // gaze, -1..1
    property real gy: 0
    property real gxT: 0
    property real gyT: 0
    property real nextGlance: 1
    property real nod: 0               // degrees, down positive
    property real nodV: 0
    property real turn: 0              // degrees, an extra yaw kicked by onsets
    property real turnV: 0

    visible: morph > 0.005
    FrameAnimation {
        running: v.visible
        onTriggered: {
            const dt = Math.min(frameTime, 0.05), t = v.time
            const ease = (cur, tgt, up, down) => cur + (tgt - cur) * (1 - Math.exp(-dt * (tgt > cur ? up : down)))
            const spring = (x, vel, tgt, w, z) => { vel += (w * w * (tgt - x) - 2 * z * w * vel) * dt; return [x + vel * dt, vel] }
            const L = Math.min(1, v.level * 1.15)
            ;[v.jaw, v.jawV] = spring(v.jaw, v.jawV, v.shape[0], 24, 0.8)
            ;[v.round, v.roundV] = spring(v.round, v.roundV, v.shape[1], 17, 0.9)
            ;[v.wide, v.wideV] = spring(v.wide, v.wideV, v.shape[2], 17, 0.9)
            v.jaw = Math.max(0, v.jaw); v.round = Math.max(0, v.round); v.wide = Math.max(0, v.wide)
            v.emph = ease(v.emph, L, 3, 1.5)
            if (L < 0.05) { if (!v.quietSince) v.quietSince = t } 
            if (L - v.emph > 0.18 && t - v.lastOnset > 0.3) {   // a stressed syllable
                v.nodV += 22 + 30 * (L - v.emph)
                v.turnV += (Math.random() * 2 - 1) * 25
                v.brow = Math.min(1, v.brow + 0.5 * (L - v.emph) + 0.15)
                if (v.quietSince && t - v.quietSince > 0.45 && Math.random() < 0.6) v.blinkT0 = t   // a new phrase: often a blink
                v.lastOnset = t
                v.quietSince = 0
            }
            v.brow = ease(v.brow, 0, 0, 1.6)
            ;[v.nod, v.nodV] = spring(v.nod, v.nodV, 0, 7, 0.55)
            ;[v.turn, v.turnV] = spring(v.turn, v.turnV, 0, 3.5, 0.7)
            if (t > v.nextBlink) { v.nextBlink = t + 2 + Math.random() * 4; v.blinkT0 = t }
            const b = (t - v.blinkT0) / 0.22
            v.blink = b >= 0 && b <= 1 ? Math.pow(Math.sin(b * Math.PI), 0.7) : 0
            if (t > v.nextGlance) {   // a glance somewhere, mostly back to the viewer
                v.nextGlance = t + 0.7 + Math.random() * 2.3
                const away = Math.random() < 0.35
                v.gxT = away ? Math.random() * 1.2 - 0.6 : (Math.random() - 0.5) * 0.15
                v.gyT = away ? Math.random() * 0.6 - 0.3 : 0
            }
            v.gx = ease(v.gx, v.gxT, 22, 22)   // eyes jump, they don't drift
            v.gy = ease(v.gy, v.gyT, 22, 22)
        }
    }
    readonly property var weights: [
        jaw * 0.7,                               // jawOpen
        round * 0.6,                             // funnel
        round * 0.45,                            // pucker
        wide * 0.55,                             // stretch
        jaw * 0.04 + wide * 0.06,                // upperUp: barely, it pulled the lip up into the nostrils
        jaw * 0.4 + wide * 0.2,                  // lowerDown
        0.1 + 0.15 * wide,                       // smile: a hint, always
        blink,                                   // blink
        Math.min(1, brow + 0.25 * emph),         // browUp
        Math.max(0, -gx), Math.max(0, gx),       // lookLeft, lookRight
        Math.max(0, gy), Math.max(0, -gy)]       // lookUp, lookDown

    component Part: Model {
        id: part
        property real fill: 0.1
        property real rim: 1.2
        property real glow: 0
        property real line: 0
        property real lift: 0
        morphTargets: [
            MorphTarget { weight: v.weights[0] }, MorphTarget { weight: v.weights[1] }, MorphTarget { weight: v.weights[2] },
            MorphTarget { weight: v.weights[3] }, MorphTarget { weight: v.weights[4] }, MorphTarget { weight: v.weights[5] },
            MorphTarget { weight: v.weights[6] }, MorphTarget { weight: v.weights[7] }, MorphTarget { weight: v.weights[8] },
            MorphTarget { weight: v.weights[9] }, MorphTarget { weight: v.weights[10] }, MorphTarget { weight: v.weights[11] },
            MorphTarget { weight: v.weights[12] }]
        materials: CustomMaterial {
            shadingMode: CustomMaterial.Unshaded
            cullMode: Material.BackFaceCulling
            vertexShader: "shaders/head.vert"
            fragmentShader: "shaders/head.frag"
            property real time: v.time
            property real morph: v.morph
            property real fill: part.fill
            property real rim: part.rim
            property real glow: part.glow
            property real line: part.line
            property real lift: part.lift
            property real fade: 1.0
            property color col: v.col
            property color hot: v.hot
        }
    }

    environment: SceneEnvironment {
        backgroundMode: SceneEnvironment.Transparent
        antialiasingMode: SceneEnvironment.MSAA
        antialiasingQuality: SceneEnvironment.High
    }
    PerspectiveCamera { position: Qt.vector3d(0, -1.5, 66); fieldOfView: 30; clipNear: 30; clipFar: 120 }   // tight: depth precision for the wireframe
    Node {   // slow drift (incommensurate sines: never repeats) plus the springs; eyes lead, the head follows
        eulerRotation.y: 5 * Math.sin(v.time * 0.31) + 2.5 * Math.sin(v.time * 0.53 + 1) + v.turn + 4 * v.gx
        eulerRotation.x: -3 + 1.5 * Math.sin(v.time * 0.41 + 2) + v.nod - 2 * v.jaw + 2 * v.gy * -1
        eulerRotation.z: 1.5 * Math.sin(v.time * 0.23) + 0.08 * v.turn
        Part { geometry: faceSkin; fill: 0.14; rim: 1.1 }                       // translucent blue skin
        Part { geometry: faceSkinWire; fill: 0; rim: 0; line: 0.4; lift: 0.04 }  // its polygon mesh, lit
        // teeth, gums, tongue: their mesh only (a filled surface hid its own lines at this depth)
        Part { geometry: faceMouthWire; fill: 0; rim: 0; line: 0.3; lift: 0.04 }
        // eyes: the same skin and mesh as the rest of the head, no light of their own
        // like real eyes: the white a little lighter than the skin, the iris darker; no rim (round
        // eyeballs read as a glow with it)
        Part { geometry: faceSclera; fill: 0.2; rim: 0 }
        Part { geometry: faceIris; fill: 0.05; rim: 0 }
        Part { geometry: faceIrisWire; fill: 0; rim: 0; line: 0.3; lift: 0.04 }
    }
}
