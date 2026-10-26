import QtQuick 2.15
import net.asivery.CommandExecutor 1.0
import xofm.libs.epaper 1.0 as Epaper

Item {
    id: root
    property var captureTarget
    property bool opened: false
    property bool busy: false
    property bool capturing: false
    property double captureStarted: 0
    property string requestID: ""
    property string responseText: ""
    property string statusText: "Напишите вопрос на странице и нажмите LLM"
    readonly property string workdir: "/home/root/remarkabl"

    CommandExecutor { id: commands }
    FontLoader { id: handwriting; source: "file:///home/root/remarkabl/Caveat.ttf" }
    Component.onCompleted: console.log("[remarkabl] page panel ready")

    function readJSON(path) {
        try {
            var request = new XMLHttpRequest();
            request.open("GET", "file://" + path, false);
            request.send();
            return JSON.parse(request.responseText);
        } catch (error) { return null; }
    }

    function capture() {
        if (busy || !captureTarget) return;
        busy = true;
        opened = false;
        capturing = true;
        captureStarted = Date.now();
        requestID = "";
        responseText = "";
        statusText = "Читаю вопрос и текст страницы…";
        commands.executeCommand("mkdir", ["-p", workdir + "/pdf-inbox"]);
        commands.executeCommand("rm", ["-f", workdir + "/pdf-inbox/current.json"]);
        captureDelay.start();
    }

    // Read the already rendered framebuffer in a separate process.
    // grabToImage requests an unsupported graphics layer on the rM2 engine.
    Timer {
        id: captureDelay
        interval: 150
        repeat: false
        onTriggered: {
            var launch = "set -a; . " + root.workdir + "/device.env; set +a; " +
                "/home/root/xovi/exthome/appload/remarkabl/remarkabl-diary --capture-screen " +
                root.workdir + "/pdf-inbox/current.json >" + root.workdir + "/pdf-inbox/capture.log 2>&1 &";
            commands.executeCommand("/bin/sh", ["-c", launch]);
        }
    }

    Timer {
        interval: 300
        running: root.busy
        repeat: true
        onTriggered: {
            if (!root.requestID) {
                var pointer = root.readJSON(root.workdir + "/pdf-inbox/current.json");
                if ((pointer && pointer.error) || Date.now() - root.captureStarted > 15000) {
                    root.busy = false;
                    root.capturing = false;
                    root.opened = true;
                    root.statusText = "Можно повторить после закрытия панели";
                    root.responseText = pointer && pointer.error ? pointer.error : "Не удалось прочитать страницу";
                    return;
                }
                if (pointer && /^[a-zA-Z0-9_-]+$/.test(pointer.id)) {
                    root.requestID = pointer.id;
                    root.capturing = false;
                    root.opened = true;
                }
            }
            if (!root.requestID) return;
            var value = root.readJSON(root.workdir + "/outbox/" + root.requestID + ".result.json");
            if (!value) return;
            if (value.answer) {
                root.responseText = value.answer;
                root.statusText = "Ответ появляется по мере генерации…";
            }
            if (value.status === "complete" || value.status === "error") {
                root.busy = false;
                root.responseText = value.answer || value.error || "Не удалось получить ответ";
                root.statusText = value.status === "complete" ? "Ответ сохранён в истории дневника" : "Вопрос сохранён. Можно повторить через дневник.";
            }
        }
    }

    Rectangle {
        id: trigger
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 32
        width: 145; height: 78; radius: 12
        color: "white"; border.color: "black"; border.width: 2
        visible: !root.capturing && !root.opened && !(root.captureTarget && root.captureTarget.appLoadView && root.captureTarget.appLoadView.visible)
        Text { anchors.centerIn: parent; text: root.busy ? "…" : "LLM"; font.pixelSize: 34 }
        MouseArea { anchors.fill: parent; onClicked: root.busy ? root.opened = true : root.capture() }
    }

    Rectangle {
        id: panel
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.margins: 28
        width: Math.min(620, parent.width * 0.47)
        color: "white"; border.color: "black"; border.width: 2
        visible: root.opened && !root.capturing
        // Mono refresh covers the answer panel, while native handwriting stays native.
        Epaper.ScreenModeItem { anchors.fill: parent; mode: Epaper.ScreenModeItem.Mono; objectName: "remarkabl-pdf-answer" }
        MouseArea { anchors.fill: parent }
        Text { x: 22; y: 20; width: parent.width - 100; text: "Живой дневник"; font.pixelSize: 32 }
        Rectangle {
            anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 15
            width: 65; height: 65; color: "white"; border.color: "black"
            Text { anchors.centerIn: parent; text: "×"; font.pixelSize: 40 }
            MouseArea { anchors.fill: parent; onClicked: root.opened = false }
        }
        Flickable {
            x: 22; y: 105; width: parent.width - 44; height: parent.height - 240
            contentHeight: answer.height; clip: true
            Text {
                id: answer
                width: parent.width
                text: root.responseText || root.statusText
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                font.family: handwriting.status === FontLoader.Ready ? handwriting.name : "Sans"
                font.pixelSize: 38
            }
        }
        Text {
            x: 22; anchors.bottom: parent.bottom; anchors.bottomMargin: 25
            width: parent.width - 44; text: root.statusText
            textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 23
        }
    }
}
