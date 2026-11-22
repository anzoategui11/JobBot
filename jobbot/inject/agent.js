// JobBot page agent. Injected into every frame of every page in the JobBot Brave profile.
// Written as one expression (IIFE) so it works both as an init script and via frame.evaluate().
// No innerHTML anywhere: some sites enforce Trusted Types.
(() => {
  if (window.__jobbotLoaded) return;
  window.__jobbotLoaded = true;

  const isTop = window.top === window;

  function send(msg) {
    if (typeof window.__jobbot === "function") {
      window.__jobbot(msg);
      return true;
    }
    return false;
  }

  // ---- Hotkey: Ctrl+Alt+G (matched by physical key, so AltGr+G also works) ----
  window.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey && e.altKey && e.code === "KeyG") || e.repeat) return;
    e.preventDefault();
    e.stopPropagation();
    const ok = send({ type: "hotkey", url: location.href, top: isTop });
    if (!ok && isTop) panel.show("JobBot isn't connected to this tab yet. Reload the page (F5) and try again.");
  }, true);

  if (!isTop) return;

  // ---- Side panel (top frame only), isolated in a shadow root ----
  const CSS = `
    :host { all: initial; }
    .box { position: fixed; top: 16px; right: 16px; width: 340px; max-height: 80vh; overflow: auto;
           z-index: 2147483647; background: #fff; color: #1a1a1a; border: 1px solid #d0d0d0;
           border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.18);
           font: 14px/1.4 system-ui, "Segoe UI", sans-serif; }
    .head { display: flex; align-items: center; justify-content: space-between;
            padding: 10px 12px; border-bottom: 1px solid #eee; font-weight: 600; }
    .close { cursor: pointer; border: none; background: none; font-size: 18px; line-height: 1; color: #666; }
    .body { padding: 12px; white-space: pre-wrap; }
    @media (prefers-color-scheme: dark) {
      .box { background: #1f1f1f; color: #eee; border-color: #444; }
      .head { border-color: #333; }
      .close { color: #aaa; }
    }`;

  let host = null, body = null;

  function build() {
    host = document.createElement("div");
    host.setAttribute("data-jobbot-ui", "");
    const root = host.attachShadow({ mode: "open" });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      root.adoptedStyleSheets = [sheet];
    } catch {
      const style = document.createElement("style");
      style.textContent = CSS;
      root.appendChild(style);
    }
    const box = document.createElement("div");
    box.className = "box";
    const head = document.createElement("div");
    head.className = "head";
    const title = document.createElement("span");
    title.textContent = "JobBot";
    const close = document.createElement("button");
    close.className = "close";
    close.title = "Close";
    close.textContent = "×";
    close.addEventListener("click", () => panel.hide());
    head.append(title, close);
    body = document.createElement("div");
    body.className = "body";
    box.append(head, body);
    root.appendChild(box);
  }

  const panel = {
    show(text) {
      if (!host) build();
      body.textContent = text;
      if (!host.isConnected) (document.body || document.documentElement).appendChild(host);
      host.style.display = "";
    },
    hide() {
      if (host) host.style.display = "none";
    },
  };

  window.__jobbotPanel = panel;
})();
