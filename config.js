/**
 * Runtime config for the static UI.
 * - Local without R2: leave dataBaseUrl empty (loads ./data/... or examples/).
 * - Hosted: R2 public ui/ prefix (rate-limited r2.dev OK for now).
 * Never put API secrets here.
 */
window.GOLDENBIRD_CONFIG = window.GOLDENBIRD_CONFIG || {
  dataBaseUrl: "https://pub-a4c7e94b27154cee93aade744bfd1f36.r2.dev/ui",
  geminiApiBase: "http://127.0.0.1:8787",
};
