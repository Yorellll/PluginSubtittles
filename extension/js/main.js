(function () {
  var cs = new CSInterface();
  var state = {
    source: null,
    lastSrtPath: null,
    activeJobId: null,
    pollTimer: null,
    selectedBackend: localStorage.getItem("grosPouce.backend") || "nemo",
  };

  var el = {
    serviceUrl: document.getElementById("serviceUrl"),
    serviceStatus: document.getElementById("serviceStatus"),
    checkService: document.getElementById("checkService"),
    workspacePath: document.getElementById("workspacePath"),
    pickWorkspace: document.getElementById("pickWorkspace"),
    startService: document.getElementById("startService"),
    stopService: document.getElementById("stopService"),
    backendSelect: document.getElementById("backendSelect"),
    applyBackend: document.getElementById("applyBackend"),
    useSelectedClip: document.getElementById("useSelectedClip"),
    pickMedia: document.getElementById("pickMedia"),
    sourceInfo: document.getElementById("sourceInfo"),
    maxLineChars: document.getElementById("maxLineChars"),
    maxLines: document.getElementById("maxLines"),
    maxDuration: document.getElementById("maxDuration"),
    maxCps: document.getElementById("maxCps"),
    autoImport: document.getElementById("autoImport"),
    generate: document.getElementById("generate"),
    importLast: document.getElementById("importLast"),
    progress: document.getElementById("progress"),
    log: document.getElementById("log"),
  };

  function log(message) {
    var time = new Date().toLocaleTimeString();
    el.log.textContent = "[" + time + "] " + message + "\n" + el.log.textContent;
  }

  function setStoredValue(key, value) {
    localStorage.setItem(key, value || "");
  }

  function setProgress(message, className) {
    el.progress.textContent = message;
    el.progress.className = "progress " + (className || "muted");
  }

  function setServiceStatus(message, className) {
    el.serviceStatus.textContent = message;
    el.serviceStatus.className = "status " + (className || "muted");
  }

  function jsxString(value) {
    return JSON.stringify(String(value));
  }

  function evalHost(functionName, args) {
    args = args || [];
    var script = "GrosPouce." + functionName + "(" + args.join(",") + ")";
    return new Promise(function (resolve) {
      cs.evalScript(script, function (raw) {
        try {
          resolve(JSON.parse(raw));
        } catch (error) {
          resolve({ ok: false, error: "Réponse Premiere invalide: " + raw });
        }
      });
    });
  }

  function requestJson(method, url, body) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      xhr.setRequestHeader("Content-Type", "application/json");
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        var payload = null;
        try {
          payload = xhr.responseText ? JSON.parse(xhr.responseText) : {};
        } catch (error) {
          reject(new Error("Réponse JSON invalide: " + xhr.responseText));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(payload.detail || payload.error || "HTTP " + xhr.status));
          return;
        }
        resolve(payload);
      };
      xhr.onerror = function () {
        reject(new Error("Impossible de contacter le service local."));
      };
      xhr.send(body ? JSON.stringify(body) : null);
    });
  }

  function serviceBaseUrl() {
    return el.serviceUrl.value.replace(/\/+$/, "");
  }

  function getWorkspacePath() {
    return (el.workspacePath.value || "").trim();
  }

  function getBackendCommand() {
    var workspace = getWorkspacePath();
    if (!workspace) return null;
    return {
      program: "powershell.exe",
      args: [
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        workspace + "\\scripts\\start_server.ps1",
      ],
    };
  }

  function renderSource() {
    if (!state.source) {
      el.sourceInfo.textContent = "Aucune source sélectionnée.";
      el.sourceInfo.className = "source-box muted";
      return;
    }
    var lines = [];
    if (state.source.mode === "clips") {
      lines.push("Clips sélectionnés: " + state.source.clips.length);
      lines.push("Séquence: " + state.source.aggregate_label);
      state.source.clips.forEach(function (clip, index) {
        lines.push(
          (index + 1) +
            ". " +
            clip.name +
            " | timeline " +
            clip.sequence_start_seconds.toFixed(3) +
            "s | source " +
            (typeof clip.trim_start_seconds === "number" ? clip.trim_start_seconds.toFixed(3) : "0.000") +
            " -> " +
            (typeof clip.trim_end_seconds === "number" ? clip.trim_end_seconds.toFixed(3) : "?") +
            "s"
        );
      });
    } else {
      lines = ["Fichier local", state.source.media_path];
    }
    el.sourceInfo.textContent = lines.join("\n");
    el.sourceInfo.className = "source-box";
  }

  function currentSubtitleSettings() {
    return {
      max_line_chars: Number(el.maxLineChars.value || 42),
      max_lines: Number(el.maxLines.value || 2),
      max_duration: Number(el.maxDuration.value || 5.5),
      max_cps: Number(el.maxCps.value || 18),
      min_duration: 0.75,
      pause_break: 0.45,
    };
  }

  async function checkService() {
    setServiceStatus("Test...", "muted");
    try {
      var health = await requestJson("GET", serviceBaseUrl() + "/health");
      if (!health.ffmpeg_ok) {
        setServiceStatus("ffmpeg absent", "warning");
        log(health.ffmpeg_error || "ffmpeg introuvable");
        return;
      }
      setServiceStatus("OK", "ok");
      state.selectedBackend = health.selected_backend || state.selectedBackend;
      el.backendSelect.value = state.selectedBackend;
      log("Service local disponible.");
    } catch (error) {
      setServiceStatus("Hors ligne", "error");
      log(error.message);
    }
  }

  async function pickWorkspace() {
    var result = await evalHost("pickFolder", []);
    if (!result.ok) {
      log(result.error);
      return;
    }
    el.workspacePath.value = result.folderPath;
    setStoredValue("grosPouce.workspacePath", result.folderPath);
    log("Dossier du plugin défini.");
  }

  function launchLocalProcess(command) {
    if (!window.cep || !window.cep.process || !window.cep.process.createProcess) {
      throw new Error("CEP process API indisponible dans ce panneau.");
    }
    return window.cep.process.createProcess.apply(window.cep.process, [command.program].concat(command.args));
  }

  async function startService() {
    var command = getBackendCommand();
    if (!command) {
      log("Définis d'abord le dossier du plugin.");
      return;
    }
    try {
      launchLocalProcess(command);
      log("Commande de démarrage envoyée au backend.");
      window.setTimeout(checkService, 2500);
    } catch (error) {
      log(error.message || String(error));
    }
  }

  async function stopService() {
    try {
      await requestJson("POST", serviceBaseUrl() + "/shutdown", {});
      setServiceStatus("Arrêt...", "warning");
      log("Demande d'arrêt du backend envoyée.");
      window.setTimeout(checkService, 1200);
    } catch (error) {
      log(error.message);
    }
  }

  async function applyBackendSelection() {
    var backend = el.backendSelect.value;
    try {
      var result = await requestJson("POST", serviceBaseUrl() + "/backend/select", {
        backend: backend,
        preload: true,
      });
      state.selectedBackend = result.selected_backend || backend;
      setStoredValue("grosPouce.backend", state.selectedBackend);
      el.backendSelect.value = state.selectedBackend;
      log("Backend actif: " + state.selectedBackend);
    } catch (error) {
      log(error.message);
    }
  }

  async function useSelectedClip() {
    var result = await evalHost("getSelectedClipInfo", []);
    if (!result.ok) {
      log(result.error);
      return;
    }
    state.source = {
      mode: "clips",
      aggregate_key: result.aggregateKey,
      aggregate_label: result.aggregateLabel,
      project_path: result.projectPath,
      clips: (result.clips || []).map(function (clip) {
        return {
          clip_key: clip.clipKey,
          media_path: clip.mediaPath,
          name: clip.name,
          sequence_start_seconds: Number(clip.sequenceStartSeconds || 0),
          trim_start_seconds:
            typeof clip.sourceInSeconds === "number" ? Number(clip.sourceInSeconds) : null,
          trim_end_seconds:
            typeof clip.sourceOutSeconds === "number" ? Number(clip.sourceOutSeconds) : null,
        };
      }),
      import_offset_seconds: 0,
    };
    renderSource();
    log("Source définie depuis la sélection timeline (" + state.source.clips.length + " clips).");
  }

  async function pickMedia() {
    var result = await evalHost("pickMediaFile", []);
    if (!result.ok) {
      log(result.error);
      return;
    }
    state.source = {
      mode: "file",
      media_path: result.mediaPath,
      import_offset_seconds: 0,
    };
    renderSource();
    log("Source définie depuis un fichier local.");
  }

  function setBusy(isBusy) {
    el.generate.disabled = isBusy;
    el.useSelectedClip.disabled = isBusy;
    el.pickMedia.disabled = isBusy;
  }

  async function generate() {
    if (!state.source) {
      log("Sélectionne d'abord une source.");
      return;
    }
    setBusy(true);
    setProgress("Création du job...", "muted");
    state.lastSrtPath = null;
    el.importLast.disabled = true;

    try {
      var created;
      if (state.source.mode === "clips") {
        var outputDir = state.source.project_path
          ? state.source.project_path.replace(/[\\/][^\\/]+$/, "")
          : state.source.clips[0].media_path.replace(/[\\/][^\\/]+$/, "");
        created = await requestJson("POST", serviceBaseUrl() + "/batch-jobs", {
          aggregate_key: state.source.aggregate_key,
          aggregate_label: state.source.aggregate_label,
          output_dir: outputDir,
          backend: state.selectedBackend,
          subtitle_settings: currentSubtitleSettings(),
          source_items: state.source.clips.map(function (clip) {
            return {
              clip_key: clip.clip_key,
              source_label: clip.name,
              media_path: clip.media_path,
              timeline_offset_seconds: clip.sequence_start_seconds,
              trim_start_seconds: clip.trim_start_seconds,
              trim_end_seconds: clip.trim_end_seconds,
            };
          }),
        });
      } else {
        created = await requestJson("POST", serviceBaseUrl() + "/jobs", {
          media_path: state.source.media_path,
          backend: state.selectedBackend,
          subtitle_settings: currentSubtitleSettings(),
        });
      }
      state.activeJobId = created.job_id;
      log("Job lancé: " + state.activeJobId);
      pollJob();
    } catch (error) {
      setBusy(false);
      setProgress("Erreur au lancement.", "error");
      log(error.message);
    }
  }

  async function pollJob() {
    if (!state.activeJobId) return;
    try {
      var job = await requestJson("GET", serviceBaseUrl() + "/jobs/" + state.activeJobId);
      setProgress(job.step || job.status, job.status === "error" ? "error" : "muted");

      if (job.status === "done") {
        setBusy(false);
        state.lastSrtPath = job.result.srt_path;
        el.importLast.disabled = false;
        setProgress("SRT généré: " + state.lastSrtPath, "ok");
        var generatedMessage =
          "Sous-titres générés (" +
          job.result.cue_count +
          " cues";
        if (job.result.clip_count) {
          generatedMessage += ", " + job.result.clip_count + " clips agrégés";
        }
        generatedMessage += ").";
        log(generatedMessage);
        if (el.autoImport.checked) {
          importLast();
        }
        return;
      }

      if (job.status === "error") {
        setBusy(false);
        setProgress("Erreur: " + job.error, "error");
        log(job.error);
        return;
      }

      state.pollTimer = window.setTimeout(pollJob, 1500);
    } catch (error) {
      setBusy(false);
      setProgress("Erreur de suivi.", "error");
      log(error.message);
    }
  }

  async function importLast() {
    if (!state.lastSrtPath) {
      log("Aucun SRT à importer.");
      return;
    }
    var startAt = state.source ? Number(state.source.import_offset_seconds || 0) : 0;
    var result = await evalHost("importSrtToActiveSequence", [jsxString(state.lastSrtPath), String(startAt)]);
    if (!result.ok) {
      log(result.error);
      return;
    }
    log("SRT importé dans la séquence active.");
  }

  el.checkService.addEventListener("click", checkService);
  el.pickWorkspace.addEventListener("click", pickWorkspace);
  el.startService.addEventListener("click", startService);
  el.stopService.addEventListener("click", stopService);
  el.applyBackend.addEventListener("click", applyBackendSelection);
  el.useSelectedClip.addEventListener("click", useSelectedClip);
  el.pickMedia.addEventListener("click", pickMedia);
  el.generate.addEventListener("click", generate);
  el.importLast.addEventListener("click", importLast);

  el.workspacePath.value = localStorage.getItem("grosPouce.workspacePath") || "";
  el.backendSelect.value = state.selectedBackend;
  renderSource();
  checkService();
})();
