(function () {
  var cs = new CSInterface();
  var state = {
    source: null,
    lastSrtPath: null,
    activeJobId: null,
    pollTimer: null,
  };

  var el = {
    serviceUrl: document.getElementById("serviceUrl"),
    serviceStatus: document.getElementById("serviceStatus"),
    checkService: document.getElementById("checkService"),
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

  function renderSource() {
    if (!state.source) {
      el.sourceInfo.textContent = "Aucune source sélectionnée.";
      el.sourceInfo.className = "source-box muted";
      return;
    }
    var lines = [
      state.source.mode === "clip" ? "Clip sélectionné" : "Fichier local",
      state.source.media_path,
    ];
    if (state.source.mode === "clip") {
      lines.push("Début timeline: " + state.source.sequence_start_seconds.toFixed(3) + "s");
      if (typeof state.source.trim_start_seconds === "number") {
        lines.push("In source: " + state.source.trim_start_seconds.toFixed(3) + "s");
      }
      if (typeof state.source.trim_end_seconds === "number") {
        lines.push("Out source: " + state.source.trim_end_seconds.toFixed(3) + "s");
      }
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
      log("Service local disponible.");
    } catch (error) {
      setServiceStatus("Hors ligne", "error");
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
      mode: "clip",
      media_path: result.mediaPath,
      sequence_start_seconds: Number(result.sequenceStartSeconds || 0),
      trim_start_seconds: result.sourceInSeconds,
      trim_end_seconds: result.sourceOutSeconds,
    };
    renderSource();
    log("Source définie depuis le clip sélectionné.");
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
      sequence_start_seconds: 0,
      trim_start_seconds: null,
      trim_end_seconds: null,
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

    var payload = {
      media_path: state.source.media_path,
      backend: "auto",
      trim_start_seconds: state.source.trim_start_seconds,
      trim_end_seconds: state.source.trim_end_seconds,
      subtitle_settings: currentSubtitleSettings(),
    };

    try {
      var created = await requestJson("POST", serviceBaseUrl() + "/jobs", payload);
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
        log("Sous-titres générés (" + job.result.cue_count + " cues, " + job.result.backend + ").");
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
    var startAt = state.source ? Number(state.source.sequence_start_seconds || 0) : 0;
    var result = await evalHost("importSrtToActiveSequence", [jsxString(state.lastSrtPath), String(startAt)]);
    if (!result.ok) {
      log(result.error);
      return;
    }
    log("SRT importé dans la séquence active.");
  }

  el.checkService.addEventListener("click", checkService);
  el.useSelectedClip.addEventListener("click", useSelectedClip);
  el.pickMedia.addEventListener("click", pickMedia);
  el.generate.addEventListener("click", generate);
  el.importLast.addEventListener("click", importLast);

  renderSource();
  checkService();
})();
