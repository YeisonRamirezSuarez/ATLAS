import QtQuick
import QtQuick.Shapes
import QtQuick.Particles

// ATLAS core drawn by Qt Quick over the painted HUD (ui.HudCanvas): ambient glow, radar sweep, orbits,
// a turning 3D neural net at the centre, sparks, status line and voice wave. Every value comes from
// hud.s, which ui.HudBridge sends only when something changes (state, colours, status). All motion,
// including the HUD rings, the radar and the voice wave, runs here on one clock (FrameAnimation): no
// Python work per frame, so a busy voice thread can't stall it and the painted layer stays still.
Item {
    id: root
    readonly property var s: hud.s
    readonly property real fw: Math.min(width, height * 0.86)
    readonly property real cx: width / 2
    readonly property real cy: height / 2 - height * 0.05
    readonly property real boot: 1 - Math.pow(1 - s.boot, 3)      // same ease-out as the painted rings
    readonly property real k: 0.7 + 0.3 * boot
    readonly property real rad: fw * 0.24 * k
    property real spin: 0
    property int decode: 0          // status characters still scrambled after a state change
    property real phase: 0
    property string mode: s.state
    readonly property var modeEs: ({ LISTENING: "ESCUCHANDO", THINKING: "PENSANDO", PROCESSING: "PROCESANDO", SPEAKING: "HABLANDO",
                                     STANDBY: "EN ESPERA", INITIALISING: "INICIANDO", MUTED: "SILENCIADO", ERROR: "ERROR" })
    readonly property bool thinking: s.state === "THINKING" || s.state === "PROCESSING"
    readonly property bool listening: s.state === "LISTENING"
    // Eased every frame (FrameAnimation below): Python's speech values jump ~8 times a second and
    // states switch at once; these glide, so speed, glow and effects blend between states.
    property real glowS: 0.3
    property real scaleS: 1
    property real glowT: 0.3        // targets re-picked a few times a second, eased into glowS / scaleS
    property real scaleT: 1
    property real nextPick: 0
    property var waveA: []          // 40 bars of the voice wave
    property real ringA: 0          // HUD rings and radar angles, degrees
    property real ringB: 120
    property real scanA: 0
    property real nowS: Date.now() / 1000
    property real taskW: 0           // tool overlay fade, eased
    readonly property real netW: taskMode === "net" ? taskW : 0
    property string taskMode: ""     // last tool mode, kept while it fades out
    readonly property real taskEl: nowS - (s.taskT0 || nowS)
    readonly property var jobs: s.jobs || []
    property real speakW: 0
    property real thinkW: 0
    property real listenW: 0
    readonly property real power: Math.max(glowS, 0.3 + 0.22 * listenW + thinkW * (0.22 + 0.1 * Math.sin(phase * 2.4)))
    readonly property real breath: 1 + 0.025 * (1 - speakW) * Math.sin(phase * 1.7)
    property color coreC: s.core
    property color ringC: s.ring
    property color priC: s.pri
    Behavior on ringC { ColorAnimation { duration: 450 } }
    Behavior on priC { ColorAnimation { duration: 450 } }
    // Speech loudness: ui sends each chunk's levels (0..1 per 20 ms) as it starts playing; queued here
    // and read back on the clock, so the face's mouth, the glow and the wave follow the words heard.
    property var vq: []             // ui.mouth_shapes: [level, jaw, round, wide] per 20 ms, flat
    property real vStart: 0
    property real vLev: 0
    property var vShape: [0, 0, 0]  // jaw, round, wide of the 20 ms being heard (the head eases them)
    Connections {
        target: hud
        function onVoice(shapes, delay) {
            const start = Date.now() / 1000 + delay
            if (start > root.vStart + root.vq.length / 4 * 0.02 + 0.05) { root.vq = shapes; root.vStart = start }
            else root.vq = root.vq.concat(shapes)
        }
    }
    property color arcC: s.arc
    Behavior on arcC { ColorAnimation { duration: 450 } }
    opacity: boot
    Behavior on opacity { NumberAnimation { duration: 150 } }   // boot arrives ~10 times a second

    onModeChanged: { pop.restart(); decode = 14; decoder.restart() }
    Timer { id: decoder; interval: 32; repeat: true; onTriggered: if (--root.decode <= 0) stop() }
    function scramble(t) {   // sci-fi "decode": the tail of the text flickers, then settles left to right
        if (decode <= 0) return t
        const g = "▚▞░▒◆◇01ΛΣΞ#", keep = Math.floor(t.length * (1 - decode / 14))
        let o = t.substring(0, keep)
        for (let i = keep; i < t.length; i++) o += t[i] === " " ? " " : g[Math.floor(Math.random() * g.length)]
        return o
    }

    FrameAnimation {
        running: true
        onTriggered: {
            const dt = Math.min(frameTime, 0.05)
            const slow = 1 - Math.exp(-dt * 3.5), fast = 1 - Math.exp(-dt * 7)
            root.phase += dt
            const vi = Math.floor((Date.now() / 1000 - root.vStart) / 0.02)
            const have = vi >= 0 && vi < root.vq.length / 4
            let lv = have ? root.vq[vi * 4] : 0
            const sa = vi + 2   // shapes 40 ms early: lips move ahead of the sound, and the head's springs lag about that
            root.vShape = sa >= 0 && sa < root.vq.length / 4 ? [root.vq[sa * 4 + 1], root.vq[sa * 4 + 2], root.vq[sa * 4 + 3]] : [0, 0, 0]
            if (s.speaking && vi > root.vq.length / 4 + 25) {   // speaking with no levels (no audio hook): a soft stand-in
                lv = 0.2 + 0.35 * Math.abs(Math.sin(root.phase * 9.3) * Math.sin(root.phase * 3.1))
                root.vShape = [lv, 0.3 * Math.max(0, Math.sin(root.phase * 2.1)), 0]
            }
            if (vi > 400) { root.vq = root.vq.slice(vi * 4); root.vStart += vi * 0.02 }
            root.vLev += (lv - root.vLev) * (1 - Math.exp(-dt * (lv > root.vLev ? 32 : 12)))
            if (root.phase >= root.nextPick) {   // same ranges the painted HUD used
                const r = (a, b) => a + Math.random() * (b - a)
                if (s.muted) { root.glowT = r(15, 28) / 190; root.scaleT = 1 }
                else if (s.speaking) { root.glowT = r(145, 190) / 190; root.scaleT = r(1.03, 1.07) }
                else { root.glowT = r(48, 68) / 190; root.scaleT = r(1, 1.01) }
                root.nextPick = root.phase + (s.speaking ? 0.12 : 0.5)
            }
            if (s.speaking && !s.muted) { root.glowT = 0.62 + 0.38 * root.vLev; root.scaleT = 1.015 + 0.045 * root.vLev }
            root.glowS += (root.glowT - root.glowS) * fast
            root.scaleS += (root.scaleT - root.scaleS) * fast
            const w = root.waveA.length ? root.waveA.slice() : new Array(40).fill(0), we = 1 - Math.exp(-dt * 25)
            for (let i = 0; i < 40; i++) {
                const tg = s.muted ? 0.05 : s.speaking ? Math.min(1, 0.08 + 1.15 * root.vLev * (1 - Math.abs(i - 19.5) / 26)
                                                                  * (0.55 + 0.45 * Math.abs(Math.sin(root.phase * 7.3 + i * 0.83))))
                                                       : 0.12 + 0.08 * Math.sin(root.phase * 4.8 + i * 0.5)
                w[i] += (tg - w[i]) * we
            }
            root.waveA = w
            root.nowS = Date.now() / 1000
            if (s.taskMode) root.taskMode = s.taskMode
            root.taskW += ((s.taskOn ? 1 : 0) - root.taskW) * (1 - Math.exp(-dt * 7))
            root.ringA = (root.ringA + dt * 60 * (0.4 + 0.7 * root.speakW)) % 360
            root.ringB = (root.ringB - dt * 60 * (0.25 + 0.45 * root.speakW)) % 360
            root.scanA = (root.scanA - dt * 60 * (1.2 + 1.8 * root.speakW) + 360) % 360
            root.speakW += ((s.speaking ? 1 : 0) - root.speakW) * slow
            root.thinkW += ((root.thinking ? 1 : 0) - root.thinkW) * slow
            root.listenW += ((root.listening ? 1 : 0) - root.listenW) * slow
            root.spin = (root.spin + dt * (12 + 10 * root.listenW + 55 * root.speakW + 85 * root.thinkW)) % 360
            neural.step(dt)
            if (root.netW > 0.01) globe.step(dt)
        }
    }

    Image {   // static background from Python (ui.HudCanvas._static_layer): grid, compass, title
        anchors.fill: parent
        source: s.bgUrl || ""
        cache: false
        asynchronous: false
    }

    Shape {   // ambient glow
        id: ambient
        readonly property real gr: root.fw * 0.42
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: root.cx; centerY: root.cy; centerRadius: ambient.gr
                focalX: root.cx; focalY: root.cy
                GradientStop { position: 0; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, (60 + 120 * root.glowS) / 255) }
                GradientStop { position: 0.4; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, (15 + 45 * root.glowS) / 255) }
                GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
            }
            PathAngleArc { centerX: root.cx; centerY: root.cy; radiusX: ambient.gr; radiusY: ambient.gr; sweepAngle: 360 }
        }
    }
    Shape {   // radar sweep, in step with the bearing marker below
        id: radar
        readonly property real rs: root.fw * 0.40 * root.k
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: ConicalGradient {
                centerX: root.cx; centerY: root.cy; angle: root.scanA
                GradientStop { position: 0; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, (70 + 60 * root.glowS) / 255) }
                GradientStop { position: 0.1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
                GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
            }
            PathAngleArc { centerX: root.cx; centerY: root.cy; radiusX: radar.rs; radiusY: radar.rs; sweepAngle: 360 }
        }
    }

    // ── HUD rings (were painted by HudCanvas every frame): segmented armour, dashed and arc rings ──
    component Ring: Item {
        id: ring
        property real r: 100
        property real w: 2
        property color c: "white"
        property real seg: 360      // lit degrees, then gap degrees, round the ring
        property real gap: 0
        property bool round: false
        x: root.cx - r; y: root.cy - r; width: 2 * r; height: 2 * r
        Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "transparent"; strokeColor: ring.c; strokeWidth: ring.w
                capStyle: ring.round ? ShapePath.RoundCap : ShapePath.FlatCap
                strokeStyle: ring.gap > 0 ? ShapePath.DashLine : ShapePath.SolidLine
                // dash lengths are in stroke widths: degrees of arc -> pixels -> widths
                dashPattern: [ring.r * ring.seg * Math.PI / 180 / ring.w, ring.r * ring.gap * Math.PI / 180 / ring.w]
                PathAngleArc { centerX: ring.r; centerY: ring.r; radiusX: ring.r; radiusY: ring.r; sweepAngle: 360 }
            }
        }
    }
    Ring { r: root.fw * 0.415 * root.k; w: root.fw * 0.02; seg: 44; gap: 16; rotation: -root.ringA
           c: Qt.rgba(root.ringC.r, root.ringC.g, root.ringC.b, (140 + 100 * root.glowS) / 255) }
    Ring { r: root.fw * 0.375 * root.k; w: 1.5; seg: 8; gap: 4; rotation: -root.ringB
           c: Qt.rgba(root.priC.r, root.priC.g, root.priC.b, 170 / 255) }
    Ring { r: root.fw * 0.355 * root.k; w: 3; seg: 60; gap: 60; round: true; rotation: -root.ringB - 30
           c: Qt.rgba(root.priC.r, root.priC.g, root.priC.b, 230 / 255) }
    Ring { r: root.fw * 0.33 * root.k; w: 1; c: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 90 / 255) }
    Item {   // bearing marker riding the compass
        id: bearing
        x: root.cx; y: root.cy
        rotation: -root.scanA
        readonly property real ro: root.fw * 0.47 * root.k
        Shape {
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"; fillColor: root.priC
                startX: bearing.ro - root.fw * 0.035; startY: 0
                PathLine { x: bearing.ro * Math.cos(0.03); y: bearing.ro * Math.sin(0.03) }
                PathLine { x: bearing.ro * Math.cos(0.03); y: -bearing.ro * Math.sin(0.03) }
            }
        }
    }
    Column {   // output readout, top right
        anchors.right: parent.right; anchors.rightMargin: 14; y: 14
        spacing: 3
        readonly property int out: s.muted ? 12 : Math.round(root.boot * (40 + 60 * root.glowS))
        Text {
            anchors.right: parent.right
            text: "POTENCIA  " + parent.out + "%"
            color: root.arcC; font.family: s.font; font.pixelSize: 11; font.bold: true
            renderType: Text.CurveRendering
        }
        Rectangle {
            anchors.right: parent.right
            width: 120; height: 4; color: s.barBg
            Rectangle { width: parent.width * parent.parent.out / 100; height: 4; color: root.arcC }
        }
        Text {
            anchors.right: parent.right
            text: s.boot < 1 ? "INICIANDO ATLAS…" : "SISTEMAS EN LÍNEA"
            color: s.textDim; font.family: s.font; font.pixelSize: 9
            renderType: Text.CurveRendering
        }
    }

    // ── orbits: two tilted rings, a satellite with a comet tail on each; speed follows the state ──
    Repeater {
        model: [{ tilt: -26, speed: 1.6, rx: 0.40 }, { tilt: 34, speed: -2.3, rx: 0.355 }]
        Item {
            id: orbit
            required property var modelData
            readonly property real rx: root.fw * modelData.rx * root.k
            readonly property real ry: rx * 0.27
            readonly property real a: root.spin * modelData.speed * Math.PI / 180
            x: root.cx; y: root.cy
            rotation: modelData.tilt
            Shape {
                x: -orbit.rx; y: -orbit.ry; width: orbit.rx * 2; height: orbit.ry * 2
                opacity: 0.45 + 0.4 * root.power
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.arcC; strokeWidth: 1; fillColor: "transparent"
                    strokeStyle: ShapePath.DashLine; dashPattern: [5, 5]
                    PathAngleArc { centerX: orbit.rx; centerY: orbit.ry; radiusX: orbit.rx; radiusY: orbit.ry; sweepAngle: 360 }
                }
            }
            Rectangle {   // the satellite's halo
                readonly property real d: 26
                width: d; height: d; radius: d / 2
                x: orbit.rx * Math.cos(orbit.a) - d / 2; y: orbit.ry * Math.sin(orbit.a) - d / 2
                color: root.arcC; opacity: 0.22
            }
            Repeater {
                model: 5   // the satellite, then its fading tail
                Rectangle {
                    required property int index
                    readonly property real b: orbit.a - Math.sign(orbit.modelData.speed) * index * 0.07
                    readonly property real d: index === 0 ? 10 : 7 - index
                    width: d; height: d; radius: d / 2
                    x: orbit.rx * Math.cos(b) - d / 2; y: orbit.ry * Math.sin(b) - d / 2
                    color: index === 0 ? s.core : root.arcC
                    border.color: index === 0 ? root.arcC : "transparent"
                    opacity: index === 0 ? 1 : 0.6 - index * 0.12
                }
            }
        }
    }

    // ── data ring: the state written round the core, turning slowly ──
    Item {
        id: dataRing
        readonly property string txt: "ATLAS  ·  ENLACE NEURONAL  ·  " + (root.modeEs[root.mode] || root.mode) + "  ·  v" + (s.version || "") + "  ·  "
        readonly property real rr: root.fw * 0.262 * root.k
        readonly property real step: 7.4 / rr * 180 / Math.PI      // one character's width, in degrees
        x: root.cx; y: root.cy
        rotation: -root.spin * 0.3
        opacity: 0.55 + 0.3 * root.power
        Repeater {
            model: dataRing.txt.length
            Text {
                required property int index
                text: dataRing.txt[index]
                color: root.arcC
                font.family: s.display; font.pixelSize: 8; font.bold: true
                renderType: Text.CurveRendering   // subpixel AA fringes in colour on the transparent layer
                x: -width / 2; y: -dataRing.rr - height / 2
                transform: Rotation { origin.x: width / 2; origin.y: dataRing.rr + height / 2; angle: index * dataRing.step }
            }
        }
    }

    // ── listening: radar pings leave the core ──
    Repeater {
        model: 3
        Rectangle {
            required property int index
            readonly property real t: (root.phase * 0.55 + index / 3) % 1
            readonly property real d: root.rad * 2 * (1 + 1.1 * t)
            visible: root.listenW > 0.01
            width: d; height: d; radius: d / 2
            x: root.cx - d / 2; y: root.cy - d / 2
            color: "transparent"
            border.color: root.arcC; border.width: 2
            opacity: 0.7 * (1 - t) * root.listenW
        }
    }

    // ── core: dark housing, dotted rim, and a 3D neural net turning inside, signals running its links ──
    Item {
        id: core
        width: root.rad * 2; height: width
        x: root.cx - width / 2; y: root.cy - height / 2
        scale: root.scaleS * root.breath
        readonly property real r: width / 2
        readonly property real sr: r * 0.7          // the net's radius

        Shape {   // housing
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.6); strokeWidth: 1.5
                fillGradient: RadialGradient {
                    centerX: core.r; centerY: core.r * 0.75; centerRadius: core.r * 1.2
                    focalX: core.r; focalY: core.r * 0.75
                    GradientStop { position: 0; color: s.steel }
                    GradientStop { position: 1; color: s.steelD }
                }
                PathAngleArc { centerX: core.r; centerY: core.r; radiusX: core.r - 1; radiusY: core.r - 1; sweepAngle: 360 }
            }
        }
        Shape {   // inner glow, swelling with power; dimmed behind the talking head
            anchors.fill: parent
            opacity: 1 - neural.morph
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: core.r; centerY: core.r; centerRadius: core.r * 0.95
                    focalX: core.r; focalY: core.r
                    GradientStop { position: 0; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.25 + 0.4 * root.power) }
                    GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
                }
                PathAngleArc { centerX: core.r; centerY: core.r; radiusX: core.r * 0.95; radiusY: core.r * 0.95; sweepAngle: 360 }
            }
        }
        Shape {   // dotted rim turning the other way
            anchors.fill: parent
            rotation: -root.spin * 0.6
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: "transparent"; strokeColor: root.arcC; strokeWidth: 2
                strokeStyle: ShapePath.DashLine; dashPattern: [1, 4]
                PathAngleArc { centerX: core.r; centerY: core.r; radiusX: core.r * 0.93; radiusY: core.r * 0.93; sweepAngle: 360 }
            }
        }

        Shape {   // the heart: a small bright orb behind the net, breathing (scaled, so its path never changes)
            id: heart
            readonly property real rr: core.r * 0.3
            anchors.fill: parent
            scale: (0.24 + 0.08 * root.power + 0.02 * Math.sin(root.phase * 2)) / 0.3
            opacity: 1 - neural.morph
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: "transparent"
                fillGradient: RadialGradient {
                    centerX: core.r; centerY: core.r; centerRadius: heart.rr
                    focalX: core.r; focalY: core.r
                    GradientStop { position: 0; color: root.coreC }
                    GradientStop { position: 0.4; color: Qt.rgba(root.coreC.r, root.coreC.g, root.coreC.b, 0.75) }
                    GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
                }
                PathAngleArc { centerX: core.r; centerY: core.r; radiusX: heart.rr; radiusY: heart.rr; sweepAngle: 360 }
            }
        }
        NeuralCore {
            id: neural
            anchors.fill: parent
            sr: core.sr
            speakW: root.speakW; thinkW: root.thinkW; listenW: root.listenW
            level: root.vLev; power: root.power
            arcC: root.arcC; coreC: root.coreC
            opacity: 1 - 0.85 * root.netW   // the globe takes the centre while browsing
        }
        Loader {   // the holographic head forming out of the net while ATLAS talks (needs Qt Quick 3D)
            id: head3d
            width: core.width * 1.02; height: width
            anchors.centerIn: parent
            anchors.verticalCenterOffset: core.r * 0.02
            source: "Head3D.qml"
            onLoaded: {
                item.morph = Qt.binding(() => neural.morph)
                item.level = Qt.binding(() => root.vLev)
                item.shape = Qt.binding(() => root.vShape)
                item.time = Qt.binding(() => root.phase)
                item.col = Qt.binding(() => root.priC)   // blue, like the rest of the HUD
                item.hot = Qt.binding(() => root.coreC)
            }
        }
    }

    // ── tool overlays (were painted in Python every frame while a tool ran): one look per kind of tool ──
    Item {
        id: fx
        anchors.fill: parent
        readonly property real ff: root.fw * root.k
        readonly property string mode: root.taskMode
        property color tc: s.taskColor || "#8ff0ff"
        visible: root.taskW > 0.01
        opacity: root.taskW
        function at(deg, r) { const a = deg * Math.PI / 180; return Qt.point(root.cx + r * Math.cos(a), root.cy - r * Math.sin(a)) }

        // media: radial spectrum and notes drifting out
        Repeater {
            model: fx.mode === "media" ? 48 : 0
            Rectangle {
                required property int index
                readonly property real v: 0.3 + 0.7 * Math.abs(Math.sin(root.phase * (2.3 + (index % 5) * 0.7) + index * 1.3))
                readonly property real a: index * 7.5 + root.ringA * 0.3
                readonly property point o: fx.at(a, fx.ff * 0.255)
                width: fx.ff * (0.01 + 0.065 * v); height: Math.max(2, fx.ff * 0.012)
                x: o.x; y: o.y - height / 2
                transformOrigin: Item.Left; rotation: -a
                color: v < 0.65 ? root.arcC : root.priC
                opacity: 0.45 + 0.55 * v
            }
        }
        Repeater {
            model: fx.mode === "media" ? 6 : 0
            Text {
                required property int index
                readonly property real ph: (root.phase * 0.36 + index / 6) % 1
                readonly property point o: fx.at(index * 60 + 25 * Math.sin(root.phase * 1.2 + index), fx.ff * (0.34 + 0.16 * ph))
                x: o.x; y: o.y - height
                text: index % 2 ? "♪" : "♫"
                color: root.priC; opacity: 1 - ph
                font.pixelSize: Math.max(12, fx.ff * 0.04)
            }
        }

        // code: source typed round a turning ring, braces pulsing
        Item {
            visible: fx.mode === "code"
            x: root.cx; y: root.cy
            rotation: -root.ringB * 1.5
            readonly property string shown: { const t = s.codeRing || "", n = Math.floor(root.taskEl * 28) % Math.max(1, t.length) + 1; return t.substring(0, n).slice(-78) }
            Repeater {
                model: fx.mode === "code" ? 78 : 0
                Text {
                    required property int index
                    visible: index < parent.shown.length
                    text: parent.shown[index] || ""
                    color: fx.tc; opacity: 0.35 + 0.65 * index / 78
                    font.family: "Consolas"; font.bold: true; font.pixelSize: Math.max(9, fx.ff * 0.02)
                    x: -width / 2; y: -fx.ff * 0.295 - height / 2
                    transform: Rotation { origin.x: width / 2; origin.y: fx.ff * 0.295 + height / 2; angle: index * 360 / 78 }
                }
            }
        }
        Repeater {
            model: fx.mode === "code" ? 2 : 0
            Text {
                required property int index
                readonly property real pulse: 0.5 + 0.5 * Math.sin(root.phase * 7)
                text: index ? "}" : "{"
                color: fx.tc; opacity: 0.45 + 0.45 * pulse
                font.family: "Consolas"; font.bold: true; font.pixelSize: Math.max(16, fx.ff * 0.06)
                x: root.cx + (index ? 1 : -1) * fx.ff * (0.37 + 0.01 * pulse) - width / 2; y: root.cy - height / 2
            }
        }

        // net: a holographic Earth in the centre, routes flying from home round the world (Globe.qml)
        Globe {
            id: globe
            visible: fx.mode === "net"
            width: root.rad * 2.45; height: width
            x: root.cx - width / 2; y: root.cy - height / 2
            scale: 0.85 + 0.15 * root.taskW
            col: root.arcC; hot: root.coreC
        }

        // scan: a sweeping line and brackets closing to a lock
        Item {
            visible: fx.mode === "scan"
            anchors.fill: parent
            readonly property real h: fx.ff * 0.46
            readonly property real ly: root.cy - h + 2 * h * ((root.phase * 0.6) % 1)
            readonly property real lock: Math.min(1, ((root.phase * 60) % 150) / 90)
            readonly property real d: fx.ff * (0.5 - 0.1 * (1 - Math.pow(1 - lock, 3)))
            Rectangle {
                x: root.cx - parent.h; width: 2 * parent.h; height: fx.ff * 0.08; y: parent.ly - height
                gradient: Gradient {
                    GradientStop { position: 0; color: "transparent" }
                    GradientStop { position: 1; color: Qt.rgba(fx.tc.r, fx.tc.g, fx.tc.b, 0.28) }
                }
            }
            Rectangle { x: root.cx - parent.h; width: 2 * parent.h; height: 1.5; y: parent.ly; color: fx.tc }
            Repeater {
                model: 8   // four corners, two strokes each
                Rectangle {
                    required property int index
                    readonly property int sx: (index >> 1) % 2 ? 1 : -1
                    readonly property int sy: index >> 2 ? 1 : -1
                    readonly property bool horiz: index % 2 === 0
                    readonly property real len: fx.ff * 0.06
                    width: horiz ? len : 2.2; height: horiz ? 2.2 : len
                    x: root.cx + sx * parent.d - (horiz ? (sx > 0 ? len : 0) : 1.1)
                    y: root.cy + sy * parent.d - (horiz ? 1.1 : (sy > 0 ? len : 0))
                    color: fx.tc
                }
            }
            Text {
                visible: parent.lock >= 1 && Math.floor(root.phase * 1.6) % 2 === 0
                text: "OBJETIVO FIJADO"; color: fx.tc
                font.family: s.font; font.bold: true; font.pixelSize: 11
                x: root.cx + parent.d - width; y: root.cy - parent.d - height - 4
            }
        }

        // comms: wavefronts left and right, uplink bars
        Repeater {
            model: fx.mode === "comms" ? 8 : 0
            Shape {
                id: wave
                required property int index
                readonly property real ph: (root.phase * 0.9 + (index % 4) / 4) % 1
                readonly property real r: fx.ff * (0.36 + 0.16 * ph)
                anchors.fill: parent
                opacity: 0.9 * (1 - ph)
                ShapePath {
                    strokeColor: fx.tc; strokeWidth: 2.5; fillColor: "transparent"; capStyle: ShapePath.FlatCap
                    PathAngleArc { centerX: root.cx; centerY: root.cy; radiusX: wave.r; radiusY: wave.r
                                   startAngle: (wave.index < 4 ? 0 : 180) - 22; sweepAngle: 44 }
                }
            }
        }
        Repeater {
            model: fx.mode === "comms" ? 2 : 0
            Column {
                required property int index
                readonly property int side: index ? -1 : 1
                x: root.cx + side * fx.ff * 0.56 - width / 2; y: root.cy - fx.ff * 0.12
                spacing: 4
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: parent.side > 0 ? "TX" : "RX"
                       color: fx.tc; opacity: Math.floor(root.phase * 1.6) % 2 ? 0.5 : 1
                       font.family: s.font; font.bold: true; font.pixelSize: 11 }
                Row {
                    spacing: 2
                    Repeater {
                        model: 5
                        Rectangle {
                            required property int index
                            anchors.bottom: parent.bottom
                            width: 4; height: fx.ff * 0.012 * (index + 1)
                            color: fx.tc
                            opacity: index <= (Math.floor(root.phase * 10) + (parent.parent.side > 0 ? 2 : 0)) % 6 ? 0.9 : 0.2
                        }
                    }
                }
            }
        }

        // system: a segmented progress ring filling, gear teeth turning
        Repeater {
            model: fx.mode === "system" ? 36 : 0
            Rectangle {
                required property int index
                readonly property real a: 90 - index * 10 - 3.5
                readonly property point o: fx.at(a, fx.ff * 0.29)
                readonly property int done: Math.floor((root.taskEl * 0.5 % 1) * 36)
                width: fx.ff * 0.29 * 7 * Math.PI / 180; height: fx.ff * 0.022
                x: o.x - width / 2; y: o.y - height / 2
                rotation: 90 - a
                color: fx.tc; opacity: index <= done ? 0.95 : 0.18
            }
        }
        Repeater {
            model: fx.mode === "system" ? 24 : 0
            Rectangle {
                required property int index
                readonly property point o: fx.at(index * 15 + root.ringA * 2, fx.ff * 0.264)
                width: fx.ff * 0.012; height: width
                x: o.x - width / 2; y: o.y - height / 2
                rotation: -(index * 15 + root.ringA * 2)
                color: root.ringC; opacity: 0.7
            }
        }

        // memory: a ring of neurons, synapses firing along its links
        Item {
            id: neurons
            visible: fx.mode === "memory"
            anchors.fill: parent
            readonly property var pts: {
                const out = []
                for (let i = 0; i < 14; i++) out.push(fx.at(i * 360 / 14 + root.ringA * 0.4, fx.ff * (i % 2 ? 0.27 : 0.315)))
                return out
            }
            readonly property var edges: { const e = []; for (let i = 0; i < 14; i++) { e.push([i, (i + 3) % 14]); e.push([i, (i + 5) % 14]) } return e }
            readonly property int beat: Math.floor(root.phase * 6)
            Repeater {
                model: neurons.visible ? neurons.edges.length : 0
                Rectangle {
                    required property int index
                    readonly property point a: neurons.pts[neurons.edges[index][0]]
                    readonly property point b: neurons.pts[neurons.edges[index][1]]
                    readonly property bool firing: [0, 7, 14, 21].some(k => (neurons.beat + k) % neurons.edges.length === index)
                    antialiasing: true
                    x: a.x; y: a.y - height / 2
                    width: Math.hypot(b.x - a.x, b.y - a.y); height: firing ? 1.8 : 1
                    transformOrigin: Item.Left; rotation: Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI
                    color: fx.tc; opacity: firing ? 0.9 : 0.18
                    Rectangle {   // the signal running along a firing link
                        visible: parent.firing
                        width: 6; height: 6; radius: 3; color: root.coreC
                        x: parent.width * ((root.phase * 6) % 1) - 3; y: parent.height / 2 - 3
                    }
                }
            }
            Repeater {
                model: neurons.visible ? 14 : 0
                Rectangle {
                    required property int index
                    width: 9; height: 9; radius: 4.5
                    x: neurons.pts[index].x - 4.5; y: neurons.pts[index].y - 4.5
                    color: s.panel; border.color: fx.tc; border.width: 1.2
                }
            }
        }

        // the task tag, top centre
        Rectangle {
            width: 250; height: 42
            x: root.cx - width / 2; y: 14
            radius: 4
            color: Qt.rgba(0, 0, 0, 0.55); border.color: fx.tc
            Text { x: 12; y: 5; text: "◢ " + (s.taskTitle || ""); color: fx.tc
                   font.family: s.font; font.bold: true; font.pixelSize: 12; renderType: Text.CurveRendering }
            Text { x: 12; y: 23; text: (s.taskTool || "").toUpperCase() + "   T+" + root.taskEl.toFixed(1) + "s"
                   color: s.text; opacity: 0.8; font.family: "Consolas"; font.pixelSize: 10; renderType: Text.CurveRendering }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 14; y: 10
                spacing: 2
                Repeater {
                    model: 10
                    Rectangle { required property int index
                                width: 5; height: 3; color: fx.tc
                                opacity: (index - Math.floor(root.phase * 15) % 10 + 10) % 10 < 3 ? 0.9 : 0.2 }
                }
            }
        }
    }

    // ── background jobs: a drone per job orbiting the core, and a numbered list top left ──
    Repeater {
        model: root.jobs.length
        Item {
            required property int index
            readonly property real rr: root.fw * root.k * 0.46
            readonly property real deg: (root.phase * 30 + index * 360 / root.jobs.length) % 360
            x: root.cx + rr * Math.cos(deg * Math.PI / 180); y: root.cy - rr * Math.sin(deg * Math.PI / 180)
            Rectangle { width: 10; height: 10; x: -5; y: -5; rotation: 45; color: s.jobColor || "#a8ecff"; border.color: "white"; border.width: 1 }
            Text { x: 8; y: -16; text: (parent.index + 1 < 10 ? "0" : "") + (parent.index + 1)
                   color: s.jobColor || "#a8ecff"; font.family: "Consolas"; font.bold: true; font.pixelSize: 10 }
        }
    }
    // the jobs themselves are listed in the OPERACIONES panel (ui.MainWindow._show_ops)

    // ── sparks: a gentle drift while speaking ──
    ParticleSystem { id: sparks }
    ImageParticle {
        system: sparks
        source: "qrc:///particleresources/glowdot.png"
        color: root.arcC; colorVariation: 0.12
        entryEffect: ImageParticle.Scale
    }
    Emitter {
        id: sparkEmitter
        system: sparks
        width: root.rad * 1.1; height: width
        x: root.cx - width / 2; y: root.cy - height / 2
        shape: EllipseShape { fill: false }
        enabled: root.speakW > 0.5
        emitRate: 22; lifeSpan: 1800; lifeSpanVariation: 500
        size: 10; sizeVariation: 5; endSize: 1
        velocity: AngleDirection { angleVariation: 360; magnitude: 32; magnitudeVariation: 14 }
    }

    // ── status and mirrored voice wave ──
    Item {
        id: statusRow
        width: root.width; height: 22
        y: Math.min(root.height - 58, root.cy + root.fw * 0.5)
        Text {
            id: label
            anchors.centerIn: parent
            text: root.scramble(s.status)
            color: s.statusColor
            font.family: s.display; font.pixelSize: 14; font.bold: true; font.letterSpacing: 3
            renderType: Text.CurveRendering   // subpixel AA fringes in colour on the transparent layer
            Behavior on color { ColorAnimation { duration: 300 } }
        }
        Repeater {
            model: [-1, 1]
            Rectangle {
                required property int modelData
                width: 40; height: 1.5; y: 11
                x: root.cx + modelData * 130 - width / 2
                color: s.acc; opacity: 0.7
            }
        }
    }
    NumberAnimation { id: pop; target: label; property: "scale"; from: 1.08; to: 1; duration: 450; easing.type: Easing.OutCubic }

    Row {
        spacing: 2
        height: 34
        x: (root.width - width) / 2
        y: statusRow.y + 40 - height / 2
        Repeater {
            model: 40
            Rectangle {
                required property int index
                anchors.verticalCenter: parent.verticalCenter
                width: 4; radius: 2
                height: Math.max(2, (root.waveA[index] || 0) * 32)
                color: root.arcC
                opacity: 0.45 + 0.55 * (root.waveA[index] || 0)
            }
        }
    }

    // ── toasts: a background job starting or ending, top right, gone after a few seconds ──
    ListModel { id: toasts }
    Connections {
        target: hud
        function onToast(kind, text) {
            toasts.append({ kind: kind, msg: text })
            if (toasts.count > 3) toasts.remove(0)
        }
    }
    ListView {
        id: toastList
        anchors.right: parent.right; anchors.rightMargin: 14
        y: 62
        width: Math.min(300, root.width * 0.45); height: contentHeight
        spacing: 6
        interactive: false
        model: toasts
        add: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 220 }
            NumberAnimation { property: "x"; from: 40; to: 0; duration: 280; easing.type: Easing.OutCubic }
        }
        remove: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 220 } }
        displaced: Transition { NumberAnimation { property: "y"; duration: 200; easing.type: Easing.OutCubic } }
        delegate: Rectangle {
            id: toast
            required property int index
            required property string kind
            required property string msg
            width: toastList.width; height: body.height + 18
            radius: 8
            color: s.panel
            border.color: kind === "done" ? s.green : s.arc
            Text {
                x: 11; y: 8
                text: toast.kind === "done" ? "✓" : "▸"
                color: toast.border.color
                font.pixelSize: 13; font.bold: true
            }
            Text {
                id: body
                x: 30; y: 9
                width: parent.width - 40
                text: toast.msg
                wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                color: s.text
                font.family: s.font; font.pixelSize: 12
                renderType: Text.CurveRendering   // subpixel AA fringes in colour on the transparent layer
            }
            Timer { interval: 4500; running: true; onTriggered: toasts.remove(toast.index) }
        }
    }
}
