import QtQuick

// ATLAS's neural net (hud.qml's core). Idle: a 3D net turning, breathing, signals running its links.
// Thinking: it spins faster and activation waves sweep through it. Speaking: it swells and dissolves
// while the holographic head (Head3D.qml) forms out of it; morph tracks that hand-over.
// One JS pass a frame (step) moves plain Rectangles (no per-item bindings, no Canvas), so the scene
// graph batches it all on the GPU.
Item {
    id: net
    property real sr: 100              // net radius, px
    property real speakW: 0            // 0..1 eased states from hud.qml
    property real thinkW: 0
    property real listenW: 0
    property real level: 0             // voice loudness now, 0..1
    property real power: 0.3
    property color arcC: "#8ff0ff"
    property color coreC: "#ffffff"

    property real t: 0
    property real spin: 0
    property real morph: 0             // speakW with its own easing: the head holds through short pauses
    property var sph: []               // unit sphere points
    property var links: []             // [i, j] nearest neighbours
    readonly property int n: 64

    Component.onCompleted: {
        const S = [], L = [], seen = {}
        for (let i = 0; i < n; i++) {
            const y = 1 - 2 * (i + 0.5) / n, r = Math.sqrt(1 - y * y), a = i * 2.39996
            S.push([r * Math.cos(a), y, r * Math.sin(a)])
        }
        for (let i = 0; i < n; i++) {
            const near = S.map((q, j) => [j, (q[0] - S[i][0]) ** 2 + (q[1] - S[i][1]) ** 2 + (q[2] - S[i][2]) ** 2])
                          .filter(e => e[0] !== i).sort((a, b) => a[1] - b[1]).slice(0, 3)
            for (const [j] of near) {
                const key = Math.min(i, j) + "-" + Math.max(i, j)
                if (!seen[key]) { seen[key] = true; L.push([i, j]) }
            }
        }
        sph = S
        links = L
    }

    function step(dt) {
        t += dt
        spin += dt * (0.2 + 0.18 * listenW + 0.6 * speakW + 1.5 * thinkW)
        // the head comes fast and leaves slowly, so pauses between sentences don't flicker it
        morph += (speakW - morph) * (1 - Math.exp(-dt * (speakW > morph ? 2.6 : 1.2)))
        if (morph < 0.003 && speakW === 0) morph = 0
        const ay = spin, ax = 0.42 + 0.12 * Math.sin(t * 0.35)
        const cy_ = Math.cos(ay), sy_ = Math.sin(ay), cx_ = Math.cos(ax), sx_ = Math.sin(ax)
        const wave = (t * 0.7) % 2.6 - 1.3                     // thinking: an activation front crossing the net
        const swell = (1 + 0.035 * listenW * Math.sin(t * 3.2)) * (0.92 + 0.5 * morph) * (1 + 0.06 * level)
        const fade = 1 - morph
        const W = width / 2, H = height / 2
        const out = new Array(n)
        for (let i = 0; i < n; i++) {
            const q = sph[i]
            const x1 = q[0] * cy_ + q[2] * sy_, z1 = -q[0] * sy_ + q[2] * cy_
            const y2 = q[1] * cx_ - z1 * sx_, z2 = q[1] * sx_ + z1 * cx_
            const act = thinkW * Math.exp(-Math.pow(q[1] * 0.6 + x1 * 0.8 - wave, 2) * 9)
            const p = out[i] = { x: x1 * swell * sr, y: y2 * swell * sr, d: (z2 + 1) / 2, a: act }
            const it = nodes.itemAt(i)
            if (!it) continue
            it.x = W + p.x - it.width / 2; it.y = H + p.y - it.height / 2
            it.scale = (i % 5 === 0 ? 1.6 : 1) * (0.6 + 0.7 * p.d) * (1 + 1.4 * act)
            it.opacity = fade * Math.min(1, (0.25 + 0.75 * p.d) * (0.7 + 0.3 * power) + act)
        }
        for (let k = 0; k < links.length; k++) {
            const l = links[k], it = edges.itemAt(k)
            if (!it) continue
            const a = out[l[0]], b = out[l[1]], dx = b.x - a.x, dy = b.y - a.y
            it.visible = fade > 0.02
            it.x = W + a.x; it.y = H + a.y - 0.65
            it.width = Math.sqrt(dx * dx + dy * dy)
            it.rotation = Math.atan2(dy, dx) * 180 / Math.PI
            it.opacity = fade * ((0.15 + 0.65 * (a.d + b.d) / 2) * (0.65 + 0.35 * power) + 0.6 * (a.a + b.a))
        }
        const T = t * (0.5 + 1.8 * thinkW + 1.1 * speakW + 0.4 * listenW)
        for (let k = 0; k < 14; k++) {   // signals: light running along the links
            const it = pulses.itemAt(k)
            if (!it) continue
            const tt = T * 0.9 + k * 0.37, f = tt - Math.floor(tt)
            const l = links[(Math.floor(tt) * 7 + k * 11) % links.length]
            const a = out[l[0]], b = out[l[1]]
            it.visible = fade > 0.05 && k < 4 + 10 * Math.max(thinkW, listenW * 0.4)
            it.x = W + a.x + (b.x - a.x) * f - it.width / 2
            it.y = H + a.y + (b.y - a.y) * f - it.height / 2
            it.opacity = fade * Math.sin(Math.PI * f) * (0.4 + 0.6 * (a.d + b.d) / 2)
        }
    }

    Repeater {
        id: edges
        model: net.links.length
        Rectangle { visible: false; antialiasing: true; height: 1.3; transformOrigin: Item.Left; color: net.arcC }
    }
    Repeater {
        id: nodes
        model: net.n
        Rectangle {
            required property int index
            readonly property real dd: net.sr * (index % 5 === 0 ? 0.075 : 0.05)
            width: dd; height: dd; radius: dd / 2
            color: index % 5 === 0 ? net.coreC : net.arcC
            border.color: net.arcC; border.width: index % 5 === 0 ? 1 : 0
        }
    }
    Repeater {
        id: pulses
        model: 14
        Rectangle {
            readonly property real dd: net.sr * 0.07
            visible: false
            width: dd; height: dd; radius: dd / 2
            color: net.coreC
        }
    }
}
