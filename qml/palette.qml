import QtQuick
import QtQuick.Controls.Basic

// Command palette (Ctrl+K, ui.MainWindow._open_palette): type to filter every action, Enter runs it.
// Text that matches nothing is offered as a question to ATLAS. Commands and colours come from cmd (ui.PaletteBridge).
Item {
    id: root
    readonly property var t: cmd.theme
    property var shown: []

    function open() {
        field.text = ""
        refilter()
        field.forceActiveFocus()
        openAnim.restart()
    }
    function refilter() {
        const q = field.text.trim().toLowerCase().split(/\s+/).filter(w => w)
        const out = cmd.items.filter(c => q.every(w => (c.title + " " + c.group).toLowerCase().includes(w)))
        if (field.text.trim())
            out.push({ id: "ask", title: "Preguntar a ATLAS: " + field.text.trim(), group: "Chat", hint: "↵" })
        shown = out
        list.currentIndex = 0
    }
    function pick(i) {
        const c = shown[i]
        if (!c) return
        if (c.id === "ask") cmd.ask(field.text.trim())
        else cmd.run(c.id)
    }

    Rectangle {   // dim the window; a click outside closes
        anchors.fill: parent
        color: "#000000"
        opacity: 0.55 * card.opacity
        MouseArea { anchors.fill: parent; onClicked: cmd.close() }
    }

    Rectangle {
        id: card
        width: Math.min(580, root.width - 40)
        height: Math.min(field.height + 1 + list.contentHeight + 16 + foot.height + 8, root.height * 0.72)
        x: (root.width - width) / 2
        y: root.height * 0.12 + 10 * (1 - opacity)
        scale: 0.97 + 0.03 * opacity
        radius: 14
        color: t.panel
        border.color: t.pri
        border.width: 1
        MouseArea { anchors.fill: parent }   // clicks on the card stay on it

        TextField {
            id: field
            objectName: "field"
            x: 6; y: 4
            width: parent.width - 12; height: 50
            leftPadding: 40
            placeholderText: "Escribe un comando o pregúntale a ATLAS…"
            placeholderTextColor: t.textDim
            color: t.white
            font.family: t.font; font.pixelSize: 15
            background: Item {}
            onTextChanged: root.refilter()
            Keys.onDownPressed: list.incrementCurrentIndex()
            Keys.onUpPressed: list.decrementCurrentIndex()
            Keys.onReturnPressed: root.pick(list.currentIndex)
            Keys.onEnterPressed: root.pick(list.currentIndex)
            Keys.onEscapePressed: cmd.close()
            Text {
                x: 14; anchors.verticalCenter: parent.verticalCenter
                text: "⌕"; color: t.pri
                font.pixelSize: 20
            }
        }
        Rectangle { id: rule; y: field.y + field.height; width: parent.width; height: 1; color: t.border }

        ListView {
            id: list
            x: 8; y: rule.y + 8
            width: parent.width - 16
            height: card.height - y - foot.height - 8
            clip: true
            model: root.shown
            boundsBehavior: Flickable.StopAtBounds
            highlightMoveDuration: 90
            highlight: Rectangle { radius: 8; color: t.priGho; border.color: t.borderB }
            delegate: Item {
                required property int index
                required property var modelData
                width: list.width; height: 38
                Text {
                    x: 12; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 150
                    elide: Text.ElideRight
                    text: modelData.title
                    color: list.currentIndex === index ? t.white : t.text
                    font.family: t.font; font.pixelSize: 13
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.group.toUpperCase()
                        color: t.textDim
                        font.family: t.font; font.pixelSize: 9; font.letterSpacing: 1.5
                    }
                    Rectangle {
                        visible: modelData.hint !== ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: key.implicitWidth + 12; height: 20; radius: 4
                        color: "transparent"; border.color: t.borderB
                        Text { id: key; anchors.centerIn: parent; text: modelData.hint; color: t.pri
                               font.family: t.font; font.pixelSize: 10 }
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onEntered: list.currentIndex = index
                    onClicked: root.pick(index)
                }
            }
        }

        Text {
            id: foot
            anchors.bottom: parent.bottom; anchors.bottomMargin: 8
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.shown.length ? "↑ ↓  mover   ·   ↵  ejecutar   ·   Esc  cerrar" : "Nada coincide"
            color: t.textDim
            font.family: t.font; font.pixelSize: 10
        }
    }

    NumberAnimation { id: openAnim; target: card; property: "opacity"; from: 0; to: 1; duration: 170; easing.type: Easing.OutCubic }
}
