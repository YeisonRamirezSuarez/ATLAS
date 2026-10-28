import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Shapes

// MEMORIA (Settings › MEMORIA, behind the PIN): what ATLAS knows about the user as a 3D net they turn
// and zoom, a search that flies to a fact, a panel to correct or delete it, and a timeline that
// replays how it grew. Data and actions come through `mem` (ui.MemoryBridge): mem.graph is
// memory.knowledge.build_graph(); mem.save / mem.remove / mem.close go back to Python.
Rectangle {
    id: root
    readonly property var t: mem.theme
    // theme values arrive as "#rrggbb" strings; .r/.g/.b (for alpha variants) need real colors
    readonly property color arcC: t.arc || "#8ff0ff"
    readonly property color redC: t.red || "#ff3b5c"
    readonly property var g: mem.graph
    readonly property var nodes: g.nodes || []
    readonly property var links: g.links || []
    readonly property int factCount: nodes.filter(n => n.kind === "fact").length
    readonly property int convCount: nodes.filter(n => n.kind === "conv").length
    property real yaw: 0
    property real pitch: 0.35
    property real zoom: 1
    property real vyaw: 0
    property bool dragging: false
    property int sel: -1
    property bool editing: false
    property bool confirming: false
    property real tl: 1
    property bool playing: false
    property var proj: []
    property var dust: []            // unit points on the surface (Fibonacci), fixed
    property var dustP: []           // their projection this frame
    property var gridFront: []       // graticule polylines, front half and back half, this frame
    property var gridBack: []
    Component.onCompleted: {
        const n = 150, pts = []
        for (let i = 0; i < n; i++) {
            const y = 1 - 2 * (i + 0.5) / n, r = Math.sqrt(1 - y * y), a = i * 2.39996
            pts.push([r * Math.cos(a), y, r * Math.sin(a)])
        }
        dust = pts
    }
    property real idle: 0
    readonly property real span: g.start && g.end ? g.end - g.start : 0
    readonly property real cutoff: span > 0 ? g.start + span * tl : 1e18
    readonly property real panelW: sel >= 0 ? 340 : 0
    property real cx: (width - panelW) / 2
    Behavior on cx { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
    readonly property real cy: height / 2 + 6
    readonly property real rr: Math.min(width - 340, height - 190) * 0.42 * zoom

    color: t.bg
    focus: true
    Keys.onEscapePressed: mem.close()
    onGChanged: { sel = -1; editing = false; confirming = false }

    function catColor(c) {
        return ({ identity: t.arc, preferences: t.green, projects: t.acc2, relationships: t.acc,
                  wishes: t.amber, notes: t.white })[c] || t.textDim
    }
    function catName(c) {
        return ({ identity: "identidad", preferences: "gustos", projects: "proyectos", relationships: "personas",
                  wishes: "deseos", notes: "notas" })[c] || "conversación"
    }
    function shown(i) {
        const n = nodes[i]
        return n.kind === "you" || !n.t || n.t <= cutoff + 0.5
    }
    function touch() { idle = 0 }
    function rot(x, y, z) {   // [screen x, screen y, depth z] after yaw then pitch
        const x1 = x * Math.cos(yaw) + z * Math.sin(yaw), z1 = -x * Math.sin(yaw) + z * Math.cos(yaw)
        return [x1, y * Math.cos(pitch) - z1 * Math.sin(pitch), y * Math.sin(pitch) + z1 * Math.cos(pitch)]
    }
    function project() {
        const out = []
        for (const n of nodes) {
            const q = rot(n.x, n.y, n.z)
            out.push({ x: q[0], y: q[1], d: (q[2] + 1.05) / 2.1 })
        }
        proj = out
        dustP = dust.map(q => rot(q[0], q[1], q[2]))
        // graticule: 6 meridians and 5 parallels, split where they pass behind the globe
        const front = [], back = [], R = rr, X = cx, Y = cy
        const line = (f) => {
            let cur = null, side = null
            for (let k = 0; k <= 48; k++) {
                const p = f(k / 48 * 2 * Math.PI), q = rot(p[0], p[1], p[2]), s = q[2] >= 0
                const pt = Qt.point(X + q[0] * R, Y + q[1] * R)
                if (s !== side) { if (cur) (side ? front : back).push(cur); cur = cur ? [cur[cur.length - 1], pt] : [pt]; side = s }
                else cur.push(pt)
            }
            if (cur) (side ? front : back).push(cur)
        }
        for (let m = 0; m < 6; m++) {
            const a = m * Math.PI / 6
            line(t => [Math.cos(t) * Math.cos(a), Math.sin(t), Math.cos(t) * Math.sin(a)])
        }
        for (const lat of [-60, -30, 0, 30, 60]) {
            const y = Math.sin(lat * Math.PI / 180), r = Math.cos(lat * Math.PI / 180)
            line(t => [r * Math.cos(t), y, r * Math.sin(t)])
        }
        gridFront = front
        gridBack = back
    }
    function focusNode(i) {   // turn the sphere so node i faces the viewer, then select it
        const n = nodes[i]
        let ty = Math.atan2(-n.x, n.z)
        while (ty - yaw > Math.PI) ty -= 2 * Math.PI
        while (ty - yaw < -Math.PI) ty += 2 * Math.PI
        const z1 = Math.sqrt(n.x * n.x + n.z * n.z)
        turnYaw.to = ty; turnPitch.to = Math.atan2(n.y, z1) + 0.5   // front, and above the centre "TÚ"
        vyaw = 0
        turn.restart()
        sel = i; editing = false; confirming = false
    }
    ParallelAnimation {
        id: turn
        NumberAnimation { id: turnYaw; target: root; property: "yaw"; duration: 700; easing.type: Easing.InOutCubic }
        NumberAnimation { id: turnPitch; target: root; property: "pitch"; duration: 700; easing.type: Easing.InOutCubic }
    }

    FrameAnimation {
        running: root.visible
        onTriggered: {
            const dt = Math.min(frameTime, 0.05)
            root.idle += dt
            if (root.idle > 300) mem.close()   // five minutes without input: lock
            if (!root.dragging && !turn.running) {
                root.yaw += root.vyaw * dt
                root.vyaw *= Math.exp(-dt * 2.5)
                if (Math.abs(root.vyaw) < 0.05 && root.sel < 0) root.yaw += dt * 0.12   // slow drift at rest
            }
            if (root.playing) {
                root.tl = Math.min(1, root.tl + dt / 9)
                if (root.tl >= 1) root.playing = false
            }
            root.project()
        }
    }

    // ── backdrop: the globe: a lit volume, its turning graticule and surface dust ──
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {   // body with a bright rim, so it reads as a ball, not a disc
            strokeColor: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.55); strokeWidth: 1.5
            fillGradient: RadialGradient {
                centerX: root.cx; centerY: root.cy; centerRadius: root.rr
                focalX: root.cx - root.rr * 0.35; focalY: root.cy - root.rr * 0.4
                GradientStop { position: 0; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.16) }
                GradientStop { position: 0.75; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.05) }
                GradientStop { position: 0.94; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.14) }
                GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.32) }
            }
            PathAngleArc { centerX: root.cx; centerY: root.cy; radiusX: root.rr; radiusY: root.rr; sweepAngle: 360 }
        }
    }
    Shape {   // outer halo
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: root.cx; centerY: root.cy; centerRadius: root.rr * 1.35
                focalX: root.cx; focalY: root.cy
                GradientStop { position: 0.7; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0.10) }
                GradientStop { position: 1; color: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, 0) }
            }
            PathAngleArc { centerX: root.cx; centerY: root.cy; radiusX: root.rr * 1.35; radiusY: root.rr * 1.35; sweepAngle: 360 }
        }
    }
    Repeater {   // graticule: back half faint, front half brighter
        model: root.gridBack.length + root.gridFront.length
        Shape {
            id: gl
            required property int index
            readonly property bool front: index >= root.gridBack.length
            anchors.fill: parent
            ShapePath {
                strokeColor: Qt.rgba(root.arcC.r, root.arcC.g, root.arcC.b, gl.front ? 0.32 : 0.10)
                strokeWidth: 1; fillColor: "transparent"
                PathPolyline { path: gl.front ? root.gridFront[gl.index - root.gridBack.length] || []
                                              : root.gridBack[gl.index] || [] }
            }
        }
    }
    Repeater {   // dust on the surface
        model: root.dust.length
        Rectangle {
            required property int index
            readonly property var q: root.dustP[index] || [0, 0, 0]
            readonly property real dd: q[2] > 0 ? 2.6 : 1.6
            width: dd; height: dd; radius: dd / 2
            x: root.cx + q[0] * root.rr - dd / 2; y: root.cy + q[1] * root.rr - dd / 2
            color: root.t.arc
            opacity: q[2] > 0 ? 0.25 + 0.45 * q[2] : 0.12
        }
    }

    // ── turn, inertia and zoom ──
    MouseArea {
        anchors.fill: parent
        property real lx: 0
        property real ly: 0
        hoverEnabled: true
        onPositionChanged: (m) => {
            root.touch()
            if (!pressed) return
            const dx = m.x - lx, dy = m.y - ly
            root.yaw += dx * 0.008
            root.pitch = Math.max(-1.3, Math.min(1.3, root.pitch + dy * 0.008))
            root.vyaw = dx * 0.5
            lx = m.x; ly = m.y
        }
        onPressed: (m) => { lx = m.x; ly = m.y; root.dragging = true; turn.stop(); root.touch(); root.forceActiveFocus() }
        onReleased: root.dragging = false
        onClicked: if (Math.abs(root.vyaw) < 0.5) { root.sel = -1; root.editing = false; root.confirming = false }
        onWheel: (w) => { root.touch(); root.zoom = Math.max(0.6, Math.min(2.4, root.zoom * Math.pow(1.12, w.angleDelta.y / 120))) }
    }

    // ── links ──
    Repeater {
        model: root.links.length
        Rectangle {
            required property int index
            readonly property var l: root.links[index]
            readonly property var a: root.proj[l[0]] || { x: 0, y: 0, d: 0 }
            readonly property var b: root.proj[l[1]] || { x: 0, y: 0, d: 0 }
            readonly property real dx: (b.x - a.x) * root.rr
            readonly property real dy: (b.y - a.y) * root.rr
            readonly property bool lit: root.sel === l[0] || root.sel === l[1]
            visible: l[2] !== "you" && root.shown(l[0]) && root.shown(l[1])
            antialiasing: true
            x: root.cx + a.x * root.rr; y: root.cy + a.y * root.rr - height / 2
            width: Math.sqrt(dx * dx + dy * dy); height: lit ? 2 : 1.2
            transformOrigin: Item.Left
            rotation: Math.atan2(dy, dx) * 180 / Math.PI
            color: l[2] === "related" ? root.t.textDim : root.t.arc
            opacity: (lit ? 0.95 : 0.5) * (0.35 + 0.65 * (a.d + b.d) / 2)
        }
    }

    // ── nodes ──
    Repeater {
        model: root.nodes.length
        Item {
            id: nd
            required property int index
            readonly property var n: root.nodes[index]
            readonly property var q: root.proj[index] || { x: 0, y: 0, d: 0.5 }
            readonly property bool on: root.shown(index)
            readonly property bool picked: root.sel === index
            readonly property real base: n.kind === "you" ? 30 : n.kind === "conv" ? 7 : (n.archived ? 10 : 15)
            readonly property real dd: base * (0.55 + 0.6 * q.d) * Math.sqrt(root.zoom)
            x: root.cx + q.x * root.rr; y: root.cy + q.y * root.rr
            z: q.d * 100
            opacity: on ? (n.archived ? 0.5 : 1) * (n.kind === "you" ? 1 : 0.35 + 0.65 * q.d) : 0
            scale: on ? 1 : 0.2
            Behavior on opacity { NumberAnimation { duration: 450 } }
            Behavior on scale { NumberAnimation { duration: 450; easing.type: Easing.OutBack } }

            Rectangle {   // selection ring
                visible: nd.picked
                width: nd.dd + 14; height: width; radius: width / 2
                x: -width / 2; y: -height / 2
                color: "transparent"; border.color: root.t.white; border.width: 2
                SequentialAnimation on opacity { running: nd.picked; loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 700 } NumberAnimation { to: 1; duration: 700 } }
            }
            Rectangle {   // halo
                width: nd.dd * (nd.n.kind === "you" ? 3.2 : 2.2); height: width; radius: width / 2
                x: -width / 2; y: -height / 2
                color: nd.n.kind === "you" ? root.t.arc : root.catColor(nd.n.cat)
                opacity: nd.n.kind === "you" ? 0.18 : 0.14
            }
            Rectangle {
                width: nd.dd; height: width; radius: width / 2
                x: -width / 2; y: -height / 2
                color: nd.n.kind === "you" ? root.t.white : root.catColor(nd.n.cat)
                border.color: nd.n.archived ? root.catColor(nd.n.cat) : "transparent"; border.width: 1
            }
            Text {
                // labels only where they can be read: the selected or hovered node, and the nearest ones
                visible: nd.n.kind !== "conv" && (nd.picked || hov.containsMouse || nd.n.kind === "you" || nd.q.d > 0.8)
                x: nd.n.kind === "you" ? -width / 2 : nd.dd / 2 + 6
                y: nd.n.kind === "you" ? nd.dd / 2 + 6 : -height / 2
                text: nd.n.label
                color: nd.picked ? root.t.white : root.t.text
                font.family: nd.n.kind === "you" ? root.t.display : root.t.font
                font.pixelSize: nd.n.kind === "you" ? 13 : 12
                font.bold: nd.n.kind === "you"
                opacity: nd.picked ? 1 : 0.85
            }
            MouseArea {
                id: hov
                enabled: nd.on && nd.n.kind !== "you"
                width: Math.max(18, nd.dd + 8); height: width
                x: -width / 2; y: -height / 2
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { root.touch(); root.focusNode(nd.index) }
            }
        }
    }

    // ── top bar: title, search, counts, close ──
    Text {
        x: 24; y: 22
        text: "ATLAS · MEMORIA"
        color: root.t.pri
        font.family: root.t.display; font.pixelSize: 20; font.bold: true; font.letterSpacing: 4
    }
    TextField {
        id: search
        width: Math.min(360, root.width * 0.32); height: 36
        x: (root.width - width) / 2; y: 16
        leftPadding: 34
        placeholderText: "Buscar un recuerdo…"
        placeholderTextColor: root.t.textDim
        color: root.t.white
        font.family: root.t.font; font.pixelSize: 13
        background: Rectangle { radius: 18; color: root.t.panel; border.color: search.activeFocus ? root.t.pri : root.t.borderB }
        Text { x: 13; anchors.verticalCenter: parent.verticalCenter; text: "⌕"; color: root.t.pri; font.pixelSize: 16 }
        onTextChanged: root.touch()
        Keys.onEscapePressed: { if (text) text = ""; else mem.close() }
        Keys.onReturnPressed: if (hits.count) root.focusNode(hits.model[0])
        Keys.onDownPressed: hits.forceActiveFocus()
    }
    ListView {
        id: hits
        visible: search.text.trim().length > 0
        x: search.x; y: search.y + search.height + 4; z: 500
        width: search.width; height: Math.min(contentHeight, 6 * 34)
        clip: true
        model: {
            const q = search.text.trim().toLowerCase()
            if (!q) return []
            const out = []
            for (let i = 0; i < root.nodes.length; i++) {
                const n = root.nodes[i]
                if (n.kind === "fact" && (n.label + " " + n.value + " " + root.catName(n.cat)).toLowerCase().includes(q)) out.push(i)
            }
            return out
        }
        delegate: Rectangle {
            required property int modelData
            readonly property var n: root.nodes[modelData]
            width: hits.width; height: 34
            color: hover.hovered ? root.t.priGho : root.t.panel
            border.color: root.t.border
            Rectangle { x: 12; anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; radius: 4; color: root.catColor(parent.n.cat) }
            Text {
                x: 28; width: parent.width - 36; anchors.verticalCenter: parent.verticalCenter
                text: parent.n.label + "  ·  " + parent.n.value
                elide: Text.ElideRight; color: root.t.text; font.family: root.t.font; font.pixelSize: 12
            }
            HoverHandler { id: hover }
            TapHandler { onTapped: { root.focusNode(parent.modelData); search.text = "" } }
        }
    }
    Text {
        anchors.right: closeBtn.left; anchors.rightMargin: 18; y: 26
        text: root.factCount + " datos · " + root.convCount + " conversaciones"
        color: root.t.textDim; font.family: root.t.font; font.pixelSize: 12
    }
    Rectangle {
        id: closeBtn
        anchors.right: parent.right; anchors.rightMargin: 20; y: 16
        width: 132; height: 36; radius: 6
        color: closeHover.hovered ? root.t.priGho : "transparent"; border.color: root.t.borderB
        Text { anchors.centerIn: parent; text: "🔒  Cerrar"; color: root.t.pri; font.family: root.t.font; font.pixelSize: 13 }
        HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: mem.close() }
    }

    // ── legend ──
    Column {
        x: 24; y: 78; spacing: 7
        Repeater {
            model: ["identity", "projects", "preferences", "relationships", "wishes", "notes", ""]
            Row {
                required property string modelData
                spacing: 8
                Rectangle { width: modelData ? 9 : 6; height: width; radius: width / 2; anchors.verticalCenter: parent.verticalCenter
                            color: modelData ? root.catColor(modelData) : root.t.textDim }
                Text {
                    text: root.catName(modelData) + (modelData ? "  " + root.nodes.filter(n => n.cat === modelData && n.kind === "fact").length : "")
                    color: root.t.textDim; font.family: root.t.font; font.pixelSize: 11
                }
            }
        }
        Text { topPadding: 8; text: "◌ atenuado: archivado"; color: root.t.textDim; font.family: root.t.font; font.pixelSize: 10 }
    }

    // ── side panel: the selected fact or conversation ──
    Rectangle {
        id: panel
        readonly property var n: root.sel >= 0 ? root.nodes[root.sel] : null
        readonly property bool isFact: !!n && n.kind === "fact"
        width: 320; height: Math.min(root.height - 190, body.implicitHeight + 36)
        x: root.width - (root.sel >= 0 ? width + 20 : -10); y: 72
        Behavior on x { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
        radius: 12
        color: root.t.panel; border.color: root.t.borderB
        visible: x < root.width
        MouseArea { anchors.fill: parent; onPressed: root.touch() }   // clicks stay on the panel

        Column {
            id: body
            x: 18; y: 18; width: parent.width - 36
            spacing: 10
            Text {
                text: panel.n ? root.catName(panel.n.cat).toUpperCase() : ""
                color: panel.n ? root.catColor(panel.n.cat) : root.t.text
                font.family: root.t.font; font.pixelSize: 10; font.letterSpacing: 2; font.bold: true
            }
            Text {
                visible: panel.isFact
                text: panel.n ? panel.n.key.replace(/_/g, " ") : ""
                color: root.t.textDim; font.family: root.t.font; font.pixelSize: 12
            }
            Text {
                visible: !root.editing
                width: parent.width; wrapMode: Text.Wrap
                text: panel.n ? (panel.isFact ? panel.n.value : panel.n.label) : ""
                color: root.t.white; font.family: root.t.font; font.pixelSize: 17; font.bold: true
            }
            TextArea {
                id: edit
                visible: root.editing
                width: parent.width
                wrapMode: TextEdit.Wrap
                color: root.t.white; font.family: root.t.font; font.pixelSize: 13
                background: Rectangle { radius: 6; color: root.t.bg; border.color: root.t.pri }
            }
            Text {
                width: parent.width; wrapMode: Text.Wrap
                text: panel.n ? (panel.isFact ? "Aprendido: " : "Conversación: ") + panel.n.when : ""
                color: root.t.textDim; font.family: root.t.font; font.pixelSize: 11
            }
            Text {
                visible: panel.isFact && panel.n.archived
                width: parent.width; wrapMode: Text.Wrap
                text: "Archivado: ya no está en su memoria activa, pero ATLAS aún puede encontrarlo al buscar."
                color: root.t.textDim; font.family: root.t.font; font.pixelSize: 11
            }
            Text {
                visible: !!panel.n && panel.n.excerpt !== ""
                width: parent.width; wrapMode: Text.Wrap; maximumLineCount: 6; elide: Text.ElideRight
                text: panel.n ? (panel.isFact ? "Probablemente de: " : "") + panel.n.excerpt : ""
                color: root.t.textDim; font.family: root.t.font; font.pixelSize: 11; font.italic: true
            }
            Flow {   // related facts, clickable
                width: parent.width; spacing: 6
                visible: panel.isFact
                Repeater {
                    model: {
                        if (!panel.isFact) return []
                        const out = []
                        for (const l of root.links)
                            if (l[2] === "related" && (l[0] === root.sel || l[1] === root.sel)) out.push(l[0] === root.sel ? l[1] : l[0])
                        return out
                    }
                    Rectangle {
                        required property int modelData
                        width: rel.implicitWidth + 16; height: 24; radius: 12
                        color: "transparent"; border.color: root.catColor(root.nodes[modelData].cat)
                        Text { id: rel; anchors.centerIn: parent; text: root.nodes[parent.modelData].label
                               color: root.t.text; font.family: root.t.font; font.pixelSize: 11 }
                        TapHandler { onTapped: root.focusNode(parent.modelData) }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }
            Item { width: 1; height: 4 }
            Row {   // Corregir / Borrar, then their confirm steps
                visible: panel.isFact
                spacing: 10
                Repeater {
                    model: root.confirming ? [["Borrar definitivamente", "del"], ["Cancelar", "back"]]
                         : root.editing ? [["Guardar", "save"], ["Cancelar", "back"]]
                         : [["✎  Corregir", "edit"], ["🗑  Borrar", "ask"]]
                    Rectangle {
                        required property var modelData
                        readonly property bool danger: modelData[1] === "del" || modelData[1] === "ask"
                        width: (body.width - 10) / 2; height: 34; radius: 6
                        color: danger ? Qt.rgba(root.redC.r, root.redC.g, root.redC.b, 0.08) : root.t.priGho
                        border.color: danger ? root.t.red : root.t.pri
                        Text { anchors.centerIn: parent; text: parent.modelData[0]
                               color: parent.danger ? root.t.red : root.t.pri; font.family: root.t.font; font.pixelSize: 12; font.bold: true }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                root.touch()
                                const a = parent.modelData[1]
                                if (a === "edit") { edit.text = panel.n.value; root.editing = true; edit.forceActiveFocus() }
                                else if (a === "ask") root.confirming = true
                                else if (a === "back") { root.editing = false; root.confirming = false }
                                else if (a === "save") { mem.save(panel.n.id, edit.text); root.editing = false }
                                else if (a === "del") { mem.remove(panel.n.id); root.confirming = false }
                            }
                        }
                    }
                }
            }
            Text {
                visible: root.confirming
                width: parent.width; wrapMode: Text.Wrap
                text: "Se quita de la memoria de ATLAS, de Engram y de tus otros equipos con ATLAS ID. Las conversaciones donde salió se conservan."
                color: root.t.textDim; font.family: root.t.font; font.pixelSize: 11
            }
        }
    }

    // ── timeline ──
    Item {
        x: 24; width: root.width - 48; height: 52
        anchors.bottom: parent.bottom; anchors.bottomMargin: 16
        visible: root.span > 0
        Rectangle {
            id: play
            width: 38; height: 38; radius: 19
            anchors.verticalCenter: parent.verticalCenter
            color: root.t.priGho; border.color: root.t.pri
            Text { anchors.centerIn: parent; text: root.playing ? "❚❚" : "▶"; color: root.t.pri; font.pixelSize: 14 }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: { root.touch(); if (!root.playing && root.tl >= 1) root.tl = 0; root.playing = !root.playing } }
        }
        Slider {
            id: slider
            x: play.width + 16; width: parent.width - x - 230
            anchors.verticalCenter: parent.verticalCenter
            from: 0; to: 1; value: root.tl
            onMoved: { root.touch(); root.playing = false; root.tl = value }
            background: Rectangle {
                x: slider.leftPadding; y: slider.topPadding + slider.availableHeight / 2 - 2
                width: slider.availableWidth; height: 4; radius: 2; color: root.t.border
                Rectangle { width: slider.visualPosition * parent.width; height: 4; radius: 2; color: root.t.pri }
            }
            handle: Rectangle {
                x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
                y: slider.topPadding + slider.availableHeight / 2 - height / 2
                width: 16; height: 16; radius: 8; color: root.t.white; border.color: root.t.pri
            }
        }
        Text {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 210; horizontalAlignment: Text.AlignRight
            text: {
                const d = new Date(root.cutoff * 1000)
                const n = root.nodes.filter((x, i) => x.kind === "fact" && root.shown(i)).length
                return (root.tl >= 1 ? "hoy" : d.toLocaleDateString(Qt.locale(), "d MMM yyyy")) + "  ·  " + n + " datos"
            }
            color: root.t.text; font.family: root.t.font; font.pixelSize: 12
        }
    }
    Text {
        visible: root.span <= 0 && root.factCount > 0
        anchors.bottom: parent.bottom; anchors.bottomMargin: 26; anchors.horizontalCenter: parent.horizontalCenter
        text: "La línea de tiempo aparece cuando haya recuerdos de días distintos."
        color: root.t.textDim; font.family: root.t.font; font.pixelSize: 12
    }

    // ── loading and empty states ──
    Text {
        visible: !mem.busy && root.factCount === 0
        x: root.cx - width / 2; y: root.cy + root.rr * 0.5
        text: "ATLAS aún no sabe nada de ti. Habla con él y vuelve aquí."
        color: root.t.textDim; font.family: root.t.font; font.pixelSize: 14
    }
    Rectangle {
        visible: mem.busy
        anchors.centerIn: parent
        width: 220; height: 54; radius: 10
        color: root.t.panel; border.color: root.t.borderB
        Text { anchors.centerIn: parent; text: "Cargando memoria…"; color: root.t.pri; font.family: root.t.font; font.pixelSize: 14 }
    }
    Text {
        visible: mem.status !== ""
        anchors.bottom: parent.bottom; anchors.bottomMargin: 76; anchors.horizontalCenter: parent.horizontalCenter
        text: mem.status
        color: root.t.green; font.family: root.t.font; font.pixelSize: 12
    }
}
