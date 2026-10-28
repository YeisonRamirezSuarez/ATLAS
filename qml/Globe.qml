import QtQuick

// The "net" tool look (web search, browser, flights, weather): a holographic Earth turning in the
// centre (shaders/globe.frag on the GPU) with data routes leaving home for cities round the world as
// dotted great-circle arcs, a ping where each lands. step(dt) moves plain Rectangles once a frame.
Item {
    id: g
    property color col: "#8ff0ff"
    property color hot: "#ffffff"
    property real t: 0
    property real rot: 1.3                 // front longitude, radians (starts over the Americas)
    readonly property real tilt: 0.36
    readonly property real rad: width / 2 * 0.84   // the shader's R
    readonly property var cities: [         // [code, lat, lon]
        ["BOG", 4.7, -74.1], ["NYC", 40.7, -74.0], ["LON", 51.5, -0.1], ["MAD", 40.4, -3.7], ["SAO", -23.5, -46.6],
        ["MEX", 19.4, -99.1], ["SFO", 37.8, -122.4], ["TYO", 35.7, 139.7], ["SYD", -33.9, 151.2], ["LOS", 6.5, 3.4],
        ["DXB", 25.2, 55.3], ["SIN", 1.3, 103.8], ["PAR", 48.9, 2.4], ["MIA", 25.8, -80.2]]
    readonly property int routes: 4
    readonly property int dots: 22
    readonly property real life: 2.6       // seconds per route

    function vec(lat, lon) {               // world unit vector, longitude measured from the front
        const a = lat * Math.PI / 180, o = lon * Math.PI / 180 - rot
        return [Math.cos(a) * Math.sin(o), Math.sin(a), Math.cos(a) * Math.cos(o)]
    }
    function screen(w, h) {                // world vector (scaled by altitude h) -> {x, y, front}
        const ct = Math.cos(tilt), st = Math.sin(tilt)
        const ny = w[1] * ct - w[2] * st, nz = w[1] * st + w[2] * ct
        const x = w[0] * h, y = ny * h
        return { x: width / 2 + x * rad, y: height / 2 - y * rad, front: nz > 0 || x * x + y * y > 1 }
    }
    function slerp(a, b, f) {
        const d = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])), om = Math.acos(d)
        if (om < 1e-4) return a
        const s = Math.sin(om), ka = Math.sin((1 - f) * om) / s, kb = Math.sin(f * om) / s
        return [a[0] * ka + b[0] * kb, a[1] * ka + b[1] * kb, a[2] * ka + b[2] * kb]
    }

    function step(dt) {
        t += dt
        rot += dt * 0.22
        for (let i = 0; i < cities.length; i++) {
            const c = cities[i], p = screen(vec(c[1], c[2]), 1), it = marks.itemAt(i)
            if (!it) continue
            it.visible = p.front
            it.x = p.x - it.width / 2; it.y = p.y - it.height / 2
        }
        for (let r = 0; r < routes; r++) {
            const tt = t / life + r / routes, k = Math.floor(tt), f = tt - k
            const from = cities[(k * 3 + r * 5) % 2 ? 0 : (k * 7 + r) % cities.length]   // mostly from home
            let to = cities[(k * 5 + r * 3 + 1) % cities.length]
            if (to === from) to = cities[(cities.indexOf(from) + 4) % cities.length]
            const a = vec(from[1], from[2]), b = vec(to[1], to[2])
            const head = Math.min(1, f / 0.7)               // the route draws in 70% of its life, then fades
            const fade = f < 0.7 ? 1 : 1 - (f - 0.7) / 0.3
            for (let d = 0; d < dots; d++) {
                const it = trail.itemAt(r * dots + d)
                if (!it) continue
                const u = d / (dots - 1) * head
                const w = slerp(a, b, u), p = screen(w, 1 + 0.22 * Math.sin(Math.PI * u))
                it.visible = p.front
                it.x = p.x - it.width / 2; it.y = p.y - it.height / 2
                it.opacity = fade * (0.25 + 0.75 * Math.pow(u / Math.max(head, 1e-3), 3))
            }
            const end = screen(b, 1), ping = pings.itemAt(r), lab = labels.itemAt(r)
            if (ping) {
                const pf = Math.max(0, (f - 0.7) / 0.3)
                ping.visible = end.front && f > 0.7
                ping.width = ping.height = 6 + 34 * pf
                ping.x = end.x - ping.width / 2; ping.y = end.y - ping.height / 2
                ping.opacity = 1 - pf
            }
            if (lab) {
                const hp = screen(slerp(a, b, head), 1 + 0.22 * Math.sin(Math.PI * head))
                lab.visible = hp.front && fade > 0.05
                lab.text = from[0] + " ▸ " + to[0]
                lab.x = hp.x + 8; lab.y = hp.y - lab.height - 4
                lab.opacity = fade
            }
        }
    }

    Image { id: land; source: "land.png"; visible: false; smooth: true }
    ShaderEffect {
        anchors.fill: parent
        property real rot: g.rot
        property real tilt: g.tilt
        property real time: g.t
        property color col: g.col
        property color hot: g.hot
        property variant land: land
        fragmentShader: "shaders/globe.frag.qsb"
    }
    Repeater {
        id: marks
        model: g.cities.length
        Rectangle { width: 5; height: 5; radius: 2.5; color: g.hot; opacity: 0.85; visible: false }
    }
    Repeater {
        id: trail
        model: g.routes * g.dots
        Rectangle { width: 3.2; height: 3.2; radius: 1.6; color: g.hot; visible: false }
    }
    Repeater {
        id: pings
        model: g.routes
        Rectangle { color: "transparent"; border.color: g.hot; border.width: 1.5; radius: width / 2; visible: false }
    }
    Repeater {
        id: labels
        model: g.routes
        Text { color: g.col; font.family: "Consolas"; font.pixelSize: 10; font.bold: true; visible: false }
    }
}
