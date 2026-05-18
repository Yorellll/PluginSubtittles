var GrosPouce = GrosPouce || {};

(function () {
    function json(payload) {
        try {
            return JSON.stringify(payload);
        } catch (error) {
            return '{"ok":false,"error":"JSON stringify failed"}';
        }
    }

    function fail(message) {
        return json({ ok: false, error: String(message) });
    }

    function fileNameFromPath(path) {
        return File(path).name;
    }

    function sanitizeKey(value) {
        return String(value || "")
            .replace(/[^A-Za-z0-9._-]+/g, "_")
            .replace(/^_+|_+$/g, "");
    }

    function safeSeconds(timeObject) {
        try {
            if (timeObject && typeof timeObject.seconds !== "undefined") {
                return Number(timeObject.seconds);
            }
        } catch (error) {}
        return null;
    }

    function getMediaPath(projectItem) {
        if (!projectItem) {
            return null;
        }
        try {
            if (projectItem.getMediaPath) {
                return projectItem.getMediaPath();
            }
        } catch (error) {}
        return null;
    }

    function findItemByPathOrName(item, path, name) {
        if (!item) {
            return null;
        }

        var mediaPath = getMediaPath(item);
        if (mediaPath && mediaPath === path) {
            return item;
        }

        try {
            if (item.name === name) {
                return item;
            }
        } catch (error) {}

        try {
            if (item.children && item.children.numItems) {
                for (var i = 0; i < item.children.numItems; i++) {
                    var found = findItemByPathOrName(item.children[i], path, name);
                    if (found) {
                        return found;
                    }
                }
            }
        } catch (error) {}

        return null;
    }

    GrosPouce.pickMediaFile = function () {
        var file = File.openDialog(
            "Choisir une vidéo ou un fichier audio",
            "Media:*.mp4;*.mov;*.mxf;*.wav;*.mp3;*.m4a;*.aac;*.flac,All:*.*",
            false
        );
        if (!file) {
            return fail("Aucun fichier sélectionné.");
        }
        return json({
            ok: true,
            mediaPath: file.fsName
        });
    };

    GrosPouce.pickFolder = function () {
        var folder = Folder.selectDialog("Choisir le dossier du plugin");
        if (!folder) {
            return fail("Aucun dossier sélectionné.");
        }
        return json({
            ok: true,
            folderPath: folder.fsName
        });
    };

    GrosPouce.getSelectedClipInfo = function () {
        try {
            var seq = app.project.activeSequence;
            if (!seq) {
                return fail("Aucune séquence active.");
            }

            var selection = seq.getSelection();
            if (!selection || selection.length === 0) {
                return fail("Sélectionne un clip audio/vidéo dans la timeline.");
            }

            var clips = [];
            var seenClipKeys = {};
            for (var i = 0; i < selection.length; i++) {
                var item = selection[i];
                if (!item || !item.projectItem) {
                    continue;
                }

                var mediaPath = getMediaPath(item.projectItem);
                if (!mediaPath) {
                    continue;
                }

                var sourceIn = safeSeconds(item.inPoint);
                var sourceOut = safeSeconds(item.outPoint);
                var sequenceStart = safeSeconds(item.start);
                var sequenceEnd = safeSeconds(item.end);

                var label = item.name || item.projectItem.name || fileNameFromPath(mediaPath);
                var clipKey = [
                    sanitizeKey(mediaPath),
                    sequenceStart || 0,
                    sourceIn || 0,
                    sourceOut || 0
                ].join("__");

                if (seenClipKeys[clipKey]) {
                    continue;
                }
                seenClipKeys[clipKey] = true;

                clips.push({
                    clipKey: clipKey,
                    mediaPath: mediaPath,
                    name: label,
                    sequenceStartSeconds: sequenceStart || 0,
                    sequenceEndSeconds: sequenceEnd,
                    sourceInSeconds: sourceIn,
                    sourceOutSeconds: sourceOut
                });
            }

            if (!clips.length) {
                return fail("Impossible de trouver un chemin média dans la sélection.");
            }

            clips.sort(function (a, b) {
                return a.sequenceStartSeconds - b.sequenceStartSeconds;
            });

            var projectPath = null;
            try {
                projectPath = app.project.path || null;
            } catch (error) {}

            return json({
                ok: true,
                sequenceName: seq.name || "Sequence",
                projectPath: projectPath,
                aggregateKey: sanitizeKey((projectPath || "unsaved_project") + "__" + (seq.name || "sequence")),
                aggregateLabel: sanitizeKey(seq.name || "sequence"),
                clips: clips
            });
        } catch (error) {
            return fail(error);
        }
    };

    GrosPouce.importSrtToActiveSequence = function (srtPath, startAtSeconds) {
        try {
            var seq = app.project.activeSequence;
            if (!seq) {
                return fail("Aucune séquence active pour importer les sous-titres.");
            }

            var srtFile = File(srtPath);
            if (!srtFile.exists) {
                return fail("SRT introuvable: " + srtPath);
            }

            var targetBin = app.project.rootItem;
            try {
                if (app.project.getInsertionBin) {
                    targetBin = app.project.getInsertionBin();
                }
            } catch (error) {}

            var imported = app.project.importFiles([srtFile.fsName], 1, targetBin, 0);
            if (!imported) {
                return fail("Premiere n'a pas pu importer le fichier SRT.");
            }

            var captionItem = findItemByPathOrName(app.project.rootItem, srtFile.fsName, srtFile.name);
            if (!captionItem) {
                return fail("SRT importé, mais ProjectItem introuvable dans le projet.");
            }

            var captionFormat = Sequence.CAPTION_FORMAT_SUBTITLE;
            var offset = Number(startAtSeconds || 0);
            var ok = seq.createCaptionTrack(captionItem, offset, captionFormat);
            if (!ok) {
                return fail("createCaptionTrack a échoué.");
            }

            return json({
                ok: true,
                srtPath: srtFile.fsName,
                startAtSeconds: offset
            });
        } catch (error) {
            return fail(error);
        }
    };
})();
