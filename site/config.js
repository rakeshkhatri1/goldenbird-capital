/**
 * Runtime config for the static UI.
 * - Local: leave dataBaseUrl empty (loads ./data/... or examples/).
 * - Hosted + R2 public ui/: set dataBaseUrl to that HTTPS prefix.
 * Never put API secrets here.
 */
window.GOLDENBIRD_CONFIG = window.GOLDENBIRD_CONFIG || {
  dataBaseUrl: "",
  geminiApiBase: "http://127.0.0.1:8787",
};
