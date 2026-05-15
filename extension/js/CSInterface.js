(function () {
  function CSInterface() {}

  CSInterface.prototype.evalScript = function (script, callback) {
    if (!window.__adobe_cep__) {
      if (callback) callback('{"ok":false,"error":"CEP indisponible"}');
      return;
    }
    window.__adobe_cep__.evalScript(script, callback || function () {});
  };

  window.CSInterface = CSInterface;
})();
