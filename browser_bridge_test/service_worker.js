// Test-only: exposes proxy mode, never credentials/cookies/history.
chrome.runtime.onMessageExternal.addListener((request, sender, reply) => {
  if (request?.type !== "proxy_probe") return;
  chrome.proxy.settings.get({incognito: false}, (value) => {
    if (chrome.runtime.lastError) return reply({ok:false, error:"proxy_read_failed"});
    const mode = value?.value?.mode || "unknown";
    reply({ok:true, mode, rules: mode === "fixed_servers" ? value.value.rules : undefined});
  });
  return true;
});
