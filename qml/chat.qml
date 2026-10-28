import QtQuick
import QtQuick.Controls.Basic

// The conversation as bubbles (ui.ChatView): the user right, ATLAS left with its words typed in,
// system lines as small centred notes. Messages arrive through chat.message; colours in chat.theme.
Rectangle {
    id: root
    readonly property var t: chat.theme
    color: t.panel
    border.color: t.border
    radius: 6

    ListModel { id: msgs }

    Connections {
        target: chat
        function onMessage(kind, body, stamp) {
            msgs.append({ kind: kind, body: body, stamp: stamp, fresh: true })
            Qt.callLater(list.positionViewAtEnd)
        }
        function onCleared() { msgs.clear() }
    }

    Text {
        anchors.centerIn: parent
        visible: msgs.count === 0
        text: "Di “Atlas” o escribe abajo para empezar"
        color: t.textDim
        font.family: t.font; font.pixelSize: 12
    }

    ListView {
        id: list
        objectName: "list"
        anchors.fill: parent
        anchors.margins: 10
        spacing: 10
        clip: true
        model: msgs
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            contentItem: Rectangle { implicitWidth: 4; radius: 2; color: t.borderB }
        }

        delegate: Item {
            id: row
            required property int index
            required property string kind
            required property string body
            required property string stamp
            required property bool fresh
            readonly property bool chatty: kind === "you" || kind === "ai"
            readonly property real maxW: width * 0.84
            width: list.width - 12   // gutter for the scroll bar
            height: chatty ? bubble.height + when.height + 3 : note.height
            opacity: fresh ? 0 : 1   // rows rebuilt on scroll-back appear without replaying the entry
            transform: Translate { id: lift; y: row.fresh ? 12 : 0 }

            // entry: fade and rise; ATLAS's reply then types itself in
            property int shown: (kind === "ai" && fresh) ? 0 : body.length
            Component.onCompleted: if (fresh) { enter.start(); if (shown < body.length) typer.start() }
            ParallelAnimation {
                id: enter
                onFinished: if (row.kind !== "ai") msgs.setProperty(row.index, "fresh", false)
                NumberAnimation { target: row; property: "opacity"; to: 1; duration: 260; easing.type: Easing.OutCubic }
                NumberAnimation { target: lift; property: "y"; to: 0; duration: 320; easing.type: Easing.OutCubic }
            }
            Timer {
                id: typer
                interval: 16; repeat: true
                onTriggered: {
                    row.shown = Math.min(row.body.length, row.shown + 3)
                    if (row.shown >= row.body.length) { stop(); msgs.setProperty(row.index, "fresh", false) }
                    if (row.index === msgs.count - 1) list.positionViewAtEnd()
                }
            }

            Rectangle {
                id: bubble
                visible: row.chatty
                readonly property bool mine: row.kind === "you"
                anchors.right: mine ? parent.right : undefined
                anchors.left: mine ? undefined : parent.left
                width: Math.min(Math.max(metrics.advanceWidth, who.implicitWidth) + 24, row.maxW)
                height: msg.height + (mine ? 16 : who.height + 20)
                radius: 12
                bottomRightRadius: mine ? 3 : 12
                bottomLeftRadius: mine ? 12 : 3
                color: mine ? t.priGho : t.panel2
                border.color: mine ? t.pri : t.borderB
                border.width: 1

                TextMetrics { id: metrics; font: msg.font; text: row.body }
                Text {
                    id: who
                    visible: !bubble.mine
                    x: 12; y: 8
                    text: "ATLAS"
                    color: t.arc
                    font.family: t.display; font.pixelSize: 10; font.bold: true; font.letterSpacing: 2
                }
                TextEdit {
                    id: msg
                    x: 12; y: bubble.mine ? 8 : who.y + who.height + 4
                    width: bubble.width - 24
                    text: row.body.substring(0, row.shown)
                    wrapMode: TextEdit.Wrap
                    readOnly: true; selectByMouse: true
                    color: bubble.mine ? t.white : t.text
                    selectionColor: t.borderB
                    font.family: t.font; font.pixelSize: 13
                }
            }
            Text {
                id: when
                visible: row.chatty
                anchors.top: bubble.bottom; anchors.topMargin: 3
                anchors.right: bubble.mine ? bubble.right : undefined
                anchors.left: bubble.mine ? undefined : bubble.left
                text: row.stamp
                color: t.textDim
                font.family: t.font; font.pixelSize: 9
            }

            Rectangle {   // system, file and error lines
                id: note
                visible: !row.chatty
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(noteText.implicitWidth + 20, row.width)
                height: noteText.height + 8
                radius: height / 2
                color: row.kind === "sys" ? "transparent" : t.panel2
                border.color: row.kind === "err" ? t.red : row.kind === "file" ? t.green : "transparent"
                Text {
                    id: noteText
                    anchors.centerIn: parent
                    width: Math.min(implicitWidth, row.width - 20)
                    text: row.body
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    color: row.kind === "err" ? t.red : row.kind === "file" ? t.green : t.textDim
                    font.family: t.font; font.pixelSize: 11
                }
            }
        }
    }
}
