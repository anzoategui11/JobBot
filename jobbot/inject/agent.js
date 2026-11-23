// JobBot page agent. Injected into every frame of every page in the JobBot Brave profile.
// Written as one expression (IIFE) so it works both as an init script and via frame.evaluate().
// No innerHTML anywhere: some sites enforce Trusted Types.
(() => {
  const VERSION = 6;                       // bump when this file changes: tabs with an older copy get this one
  if ((window.__jobbotLoaded || 0) >= VERSION) return;
  window.__jobbotLoaded = VERSION;
  const me = {};
  window.__jobbotOwner = me;

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
    if (!(e.ctrlKey && e.altKey && e.code === "KeyG") || e.repeat || window.__jobbotOwner !== me) return;
    e.preventDefault();
    e.stopPropagation();
    const ok = send({ type: "hotkey", url: location.href, top: isTop });
    if (!ok && isTop) panel.show("JobBot isn't connected to this tab yet. Reload the page (F5) and try again.");
  }, true);

  // ---- Field extractor (every frame). Read-only apart from tagging elements with data-jobbot-id. ----
  const TAG = isTop ? "f" : String.fromCharCode(97 + Math.floor(Math.random() * 26)) +
                            String.fromCharCode(97 + Math.floor(Math.random() * 26));
  let counter = 0;
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image", "range", "color"]);
  const TEXTISH = { email: "email", tel: "tel", number: "number", date: "date", "datetime-local": "date",
                    month: "date", week: "date", time: "text", url: "text", search: "text", text: "text" };
  const NO_TEXT = new Set(["SELECT", "OPTION", "TEXTAREA", "SCRIPT", "STYLE", "NOSCRIPT", "SVG"]);

  const squash = (s, n = 200) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);

  function textOf(node) {
    // Visible-ish text of a node, skipping nested controls' option lists and our own UI.
    if (!node) return "";
    let out = "";
    const walk = (n) => {
      if (n.nodeType === 3) { out += n.nodeValue + " "; return; }
      if (n.nodeType !== 1 || NO_TEXT.has(n.tagName.toUpperCase()) || n.hasAttribute("data-jobbot-ui")) return;
      if (n.getAttribute("aria-hidden") === "true") return;
      for (const c of n.childNodes) walk(c);
    };
    walk(node);
    return squash(out);
  }

  function* walkAll(root) {
    for (const el of root.querySelectorAll("*")) {
      if (el.hasAttribute("data-jobbot-ui")) continue;
      yield el;
      if (el.shadowRoot) yield* walkAll(el.shadowRoot);
    }
  }

  function visible(el) {
    if (el.checkVisibility) return el.checkVisibility({ visibilityProperty: true });
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  }

  function byIds(el, attr) {
    const ids = (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    const root = el.getRootNode();
    return squash(ids.map((id) => textOf(root.getElementById ? root.getElementById(id) : null)).join(" "));
  }

  function precedingText(el) {
    let node = el;
    for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
        const t = textOf(sib);
        if (t) return t;
        if (sib.querySelector && sib.querySelector("input,select,textarea")) break;
      }
    }
    return "";
  }

  function labelFor(el) {
    const viaLabels = el.labels && el.labels.length ? squash([...el.labels].map(textOf).join(" ")) : "";
    return viaLabels || byIds(el, "aria-labelledby") || squash(el.getAttribute("aria-label")) ||
           squash(el.getAttribute("placeholder")) || squash(el.getAttribute("aria-placeholder")) ||
           squash(el.getAttribute("title")) || wrapperText(el) ||
           precedingText(el) || squash(el.getAttribute("name"));
  }

  const CONTROLS = 'input:not([type="hidden"]),select,textarea,[role="combobox"],[contenteditable="true"],[role="radio"]';

  function wrapperText(el) {
    // Custom dropdowns often show their name as placeholder text next to the control, inside a wrapper
    // that holds only this one control ("Location ▾"). Use that wrapper's text.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 3; depth++, node = node.parentElement) {
      if (node.querySelectorAll(CONTROLS).length > 1) break;
      const t = textOf(node);
      if (t && t.length <= 120) return t;
    }
    return el.tagName !== "INPUT" && el.getAttribute("role") === "combobox" ? textOf(el).slice(0, 120) : "";
  }

  const DIALOGS = '[role="dialog"],[role="alertdialog"],dialog[open],[aria-modal="true"]';

  function activeDialog() {
    // An open modal (LinkedIn Easy Apply, cookie-free apply popups) is the form; ignore the page behind it.
    // Prefer one covering the middle of the screen: chat pop-ups are dialogs too, but sit in a corner.
    let last = null, centred = null;
    const cx = innerWidth / 2, cy = innerHeight / 2;
    for (const el of walkAll(document)) {
      if (!(el.matches(DIALOGS) && visible(el) && el.querySelector(CONTROLS))) continue;
      last = el;                                                   // later in the DOM = on top
      const r = el.getBoundingClientRect();
      if (r.left <= cx && cx <= r.right && r.top <= cy && cy <= r.bottom) centred = el;
    }
    return centred || last;
  }

  function groupLabel(el) {
    // Question text for a radio/checkbox group: radiogroup labels, fieldset legend, then preceding text.
    const group = el.closest('[role="radiogroup"],[role="group"],fieldset');
    if (group) {
      const t = byIds(group, "aria-labelledby") || squash(group.getAttribute("aria-label")) ||
                (group.tagName === "FIELDSET" ? textOf(group.querySelector("legend")) : "");
      if (t) return t;
      return precedingText(group);
    }
    return "";
  }

  const registry = new Map();          // id -> Set of elements (radio groups have several)

  function tag(el, id) {
    if (!el.getAttribute("data-jobbot-id")) el.setAttribute("data-jobbot-id", id || `${TAG}${++counter}`);
    const got = el.getAttribute("data-jobbot-id");
    if (!registry.has(got)) registry.set(got, new Set());
    registry.get(got).add(el);
    return got;
  }

  function cleanLabel(s) {
    const required = /\*\s*$|\(required\)\s*$/i.test(s);
    return { text: squash(s.replace(/\s*(\*|\(required\))\s*$/i, ""), 200), required };
  }

  function extract(root) {
    const fields = [];
    const radioGroups = new Map();      // scope+name -> field
    const scopeIds = new Map();
    for (const el of walkAll(root)) {
      const tagName = el.tagName;
      let kind = null;
      if (tagName === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (SKIP_TYPES.has(type)) continue;
        if (type === "radio" || type === "checkbox" || type === "file" || type === "password") kind = type;
        else kind = TEXTISH[type] || "text";
        if (kind === "text" && (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list"))
          kind = "combobox";
      } else if (tagName === "SELECT") kind = "select";
      else if (tagName === "TEXTAREA") kind = "textarea";
      else if (el.getAttribute("role") === "combobox" && !el.querySelector("input,select")) kind = "combobox";
      else if (el.getAttribute("role") === "radio") kind = "aria-radio";
      else if (el.isContentEditable && !(el.parentElement && el.parentElement.isContentEditable)) kind = "textarea";
      if (!kind) continue;
      if (el.disabled || el.getAttribute("aria-disabled") === "true") continue;
      // File inputs are usually hidden behind a styled button; radios/checkboxes behind custom visuals.
      const shown = visible(el) || ((kind === "radio" || kind === "checkbox") && el.labels &&
                                    [...el.labels].some(visible));
      if (kind !== "file" && !shown) continue;
      if (el.readOnly && kind !== "combobox" && kind !== "date") continue;

      if (kind === "radio" || kind === "aria-radio") {
        const isAria = kind === "aria-radio";
        const optLabel = isAria ? (squash(el.getAttribute("aria-label")) || textOf(el)) : labelFor(el);
        const scope = isAria ? el.closest('[role="radiogroup"]') : (el.form || el.getRootNode());
        if (!scopeIds.has(scope)) scopeIds.set(scope, scopeIds.size + 1);
        const groupKey = isAria ? `aria|${scopeIds.get(scope)}`
                                : (el.name ? `${scopeIds.get(scope)}|${el.name}` : el);
        let f = radioGroups.get(groupKey);
        if (!f) {
          const lab = cleanLabel(groupLabel(el) || (isAria ? "" : precedingText(el.closest("label") || el)));
          f = { id: tag(el), kind: "radio", label: lab.text, options: [], required: lab.required ||
                el.required || el.closest('[aria-required="true"]') !== null, current_value: "", autocomplete: "", accept: "" };
          radioGroups.set(groupKey, f);
          fields.push(f);
        } else tag(el, f.id);
        el.setAttribute("data-jobbot-opt", optLabel);
        f.options.push(optLabel);
        if (isAria ? el.getAttribute("aria-checked") === "true" : el.checked) f.current_value = optLabel;
        continue;
      }

      const lab = cleanLabel(kind === "checkbox" ? [groupLabel(el), labelFor(el)].filter(Boolean).join(" — ")
                                                 : labelFor(el));
      const f = { id: tag(el), kind, label: lab.text, options: [], autocomplete: squash(el.getAttribute("autocomplete"), 40),
                  accept: squash(el.getAttribute("accept"), 100),
                  required: lab.required || !!el.required || el.getAttribute("aria-required") === "true",
                  current_value: "" };
      if (kind === "select") {
        f.options = [...el.options].filter((o, i) => !(i === 0 && !o.value)).map((o) => squash(o.text, 120))
                                   .filter(Boolean).slice(0, 200);
        const sel = el.selectedOptions[0];
        f.current_value = sel && sel.value ? squash(sel.text, 120) : "";
      } else if (kind === "checkbox") f.current_value = el.checked ? "true" : "false";
      else if (kind === "file") f.current_value = el.files && el.files.length ? el.files[0].name : "";
      else if (kind === "password") f.current_value = el.value ? "(filled)" : "";   // never read a password out
      else if (kind === "combobox" && tagName !== "INPUT") f.current_value = textOf(el);
      else if (el.isContentEditable) f.current_value = squash(el.innerText, 300);
      else f.current_value = squash(el.value, 300);
      if (kind === "combobox") {
        const ctl = (el.getAttribute("aria-controls") || el.getAttribute("aria-owns") || "").split(/\s+/)[0];
        const root = el.getRootNode();
        const list = ctl && root.getElementById ? root.getElementById(ctl) : null;
        if (list) f.options = [...list.querySelectorAll('[role="option"]')].map((o) => textOf(o)).filter(Boolean).slice(0, 100);
      }
      fields.push(f);
    }
    return fields;
  }

  const JOB_SELECTORS = [
    "#job-details", ".jobs-description__content", ".jobs-description", '[data-automation-id="jobPostingDescription"]',
    "#content .job-post", ".posting-page", '[class*="job-description" i]', '[class*="jobDescription" i]',
    '[id*="job-description" i]', '[class*="description" i]', "article", "main", '[role="main"]',
  ];

  function jobContext(maxChars) {
    let text = "";
    for (const sel of JOB_SELECTORS) {
      for (const el of document.querySelectorAll(sel)) {
        const t = (el.innerText || "").trim();
        if (t.length > 200 && visible(el)) { text = t; break; }
      }
      if (text) break;
    }
    if (!text && document.body) text = (document.body.innerText || "").trim();
    const meta = (p) => squash((document.querySelector(`meta[property="${p}"],meta[name="${p}"]`) || {}).content);
    return {
      title: squash(document.title, 300), h1: textOf(document.querySelector("h1")),
      site: meta("og:site_name"), og_title: meta("og:title"),
      text: text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").slice(0, maxChars),
    };
  }

  window.__jobbotExtract = (maxChars = 6000) => {
    try {
      const dialog = activeDialog();
      return { ok: true, url: location.href, top: isTop, modal: !!dialog,
               modal_title: dialog ? (byIds(dialog, "aria-labelledby") || squash(dialog.getAttribute("aria-label")) ||
                                      textOf(dialog.querySelector("h1,h2,h3"))) : "",
               fields: extract(dialog || document), job: jobContext(maxChars) };
    } catch (e) {
      return { ok: false, url: location.href, top: isTop, error: String(e && e.message || e), fields: [], job: null };
    }
  };

  // ---- Filler (every frame). Only touches elements tagged by the extractor; never submits or navigates.
  // Values are set the way autofill does (native setter + input/change events), so the page doesn't scroll.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const same = (a, b) => squash(a, 500).toLowerCase() === squash(b, 500).toLowerCase();

  function elementsFor(id) {
    const set = registry.get(id);
    const live = set ? [...set].filter((e) => e.isConnected) : [];
    return live.length ? live : [...walkAll(document)].filter((e) => e.getAttribute("data-jobbot-id") === id);
  }

  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);                                      // React/Vue track the native setter
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function pressOption(opt) {
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"])
      opt.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  }

  async function pickOption(el, value) {
    // Wait for the dropdown list, then choose the option that matches (exact first, then prefix/contains).
    for (let i = 0; i < 15; i++) {
      await sleep(100);
      const ctl = (el.getAttribute("aria-controls") || el.getAttribute("aria-owns") || "").split(/\s+/)[0];
      const root = el.getRootNode();
      const list = ctl && root.getElementById ? root.getElementById(ctl) : null;
      const opts = [...(list || document).querySelectorAll('[role="option"]')].filter(visible);
      const want = value.toLowerCase();
      const hit = opts.find((o) => same(textOf(o), value)) ||
                  opts.find((o) => textOf(o).toLowerCase().startsWith(want)) ||
                  opts.find((o) => textOf(o).toLowerCase().includes(want));
      if (hit) { pressOption(hit); return true; }
    }
    return false;
  }

  function currentText(el) {
    return el.isContentEditable && !("value" in el) ? el.innerText : (el.value || "");
  }

  window.__jobbotValues = (ids) => {
    const out = {};
    for (const id of ids) { const el = elementsFor(id)[0]; out[id] = el ? currentText(el) : null; }
    return out;
  };

  async function fillOne({ id, kind, value, ifEmpty }) {
    const els = elementsFor(id);
    if (!els.length) return "gone";
    const el = els[0];
    if (ifEmpty && currentText(el).trim()) return "kept";        // you typed something meanwhile
    if (kind === "radio") {
      const opt = els.find((e) => same(e.getAttribute("data-jobbot-opt") || "", value));
      if (!opt) return "no such option";
      const checked = opt.getAttribute("role") === "radio" ? opt.getAttribute("aria-checked") === "true" : opt.checked;
      if (!checked) opt.click();
      return "ok";
    }
    if (kind === "checkbox") {
      if (el.checked !== (value === "true")) el.click();
      return "ok";
    }
    if (kind === "select") {
      const opt = [...el.options].find((o) => same(o.text, value)) || [...el.options].find((o) => same(o.value, value));
      if (!opt) return "no such option";
      setNative(el, opt.value);
      return "ok";
    }
    if (kind === "combobox") {
      if (el.tagName === "INPUT") {
        el.focus({ preventScroll: true });
        setNative(el, value);
        return (await pickOption(el, value)) ? "ok" : "typed";      // typed but nothing picked from the list
      }
      pressOption(el);                                                // open a div-based dropdown
      return (await pickOption(el, value)) ? "ok" : "manual";
    }
    if (el.isContentEditable && !("value" in el)) {
      el.focus({ preventScroll: true });
      document.execCommand("selectAll", false);
      if (!document.execCommand("insertText", false, value)) el.textContent = value;
      return "ok";
    }
    setNative(el, value);
    if (kind === "date" && el.value === "" && value) return "bad date";
    return "ok";
  }

  window.__jobbotFill = async (items) => {
    const out = {};
    const active = document.activeElement;
    for (const item of items) {
      try { out[item.id] = await fillOne(item); } catch (e) { out[item.id] = "error: " + String(e && e.message || e); }
    }
    try { if (active && active.focus) active.focus({ preventScroll: true }); } catch {}
    return out;
  };

  window.__jobbotPageHas = (needle) => {
    const n = needle.toLowerCase();
    if ((document.body && document.body.innerText || "").toLowerCase().includes(n)) return true;
    for (const el of walkAll(document)) if (el.shadowRoot && (el.shadowRoot.textContent || "").toLowerCase().includes(n)) return true;
    return false;
  };

  if (!isTop) return;

  // ---- Side panel (top frame only), isolated in a shadow root ----
  const CSS = `
    :host { all: initial; }
    .box { position: fixed; top: 16px; right: 16px; width: 360px; max-height: 85vh; overflow: auto;
           z-index: 2147483647; background: #fff; color: #1a1a1a; border: 1px solid #d0d0d0;
           border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.18);
           font: 14px/1.4 system-ui, "Segoe UI", sans-serif; }
    .head { display: flex; align-items: center; justify-content: space-between;
            padding: 10px 12px; border-bottom: 1px solid #eee; font-weight: 600; }
    .close { cursor: pointer; border: none; background: none; font-size: 18px; line-height: 1; color: #666; }
    .body { padding: 12px; }
    .status { white-space: pre-wrap; margin: 0 0 10px; }
    h4 { margin: 12px 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #666; }
    .q { margin: 0 0 12px; }
    .q label.t { display: block; font-weight: 500; margin-bottom: 4px; }
    .q .hint { font-size: 12px; color: #777; margin-top: 2px; }
    .q input[type=text], .q select, .q textarea { box-sizing: border-box; width: 100%; padding: 6px 8px;
           font: inherit; border: 1px solid #bbb; border-radius: 6px; background: #fff; color: inherit; }
    .q textarea { min-height: 70px; resize: vertical; }
    .save { font-size: 12px; color: #555; display: flex; gap: 4px; align-items: center; margin-top: 4px; }
    button.go { font: inherit; font-weight: 600; padding: 7px 16px; border-radius: 16px; border: none;
                background: #0a66c2; color: #fff; cursor: pointer; }
    button.go:disabled { opacity: .5; cursor: default; }
    button.small { font: inherit; font-size: 12px; padding: 3px 10px; border-radius: 12px; cursor: pointer;
                   border: 1px solid #0a66c2; background: none; color: #0a66c2; margin-top: 4px; }
    ul { margin: 0; padding-left: 18px; }
    li { margin: 2px 0; }
    .notes { font-size: 12px; color: #777; }
    .conflict { margin: 0 0 10px; }
    .rolebar { display: flex; gap: 8px; align-items: center; font-weight: 500; margin: 0 0 10px; }
    .rolebar select { flex: 1; padding: 4px 6px; font: inherit; border: 1px solid #bbb; border-radius: 6px;
                      background: #fff; color: inherit; }
    .badge { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 8px;
             background: #fff3cd; color: #7a5b00; }
    @media (prefers-color-scheme: dark) {
      .box { background: #1f1f1f; color: #eee; border-color: #444; }
      .head { border-color: #333; }
      .close, h4, .notes, .q .hint, .save { color: #aaa; }
      .q input[type=text], .q select, .q textarea { background: #2a2a2a; border-color: #555; }
      button.small { color: #6cb4ff; border-color: #6cb4ff; }
      .rolebar select { background: #2a2a2a; border-color: #555; }
      .badge { background: #4a3d10; color: #ffd866; }
    }`;

  let host = null, body = null;

  function el(tagName, props = {}, ...kids) {
    const e = document.createElement(tagName);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    }
    e.append(...kids.filter(Boolean));
    return e;
  }

  function build() {
    host = document.createElement("div");
    host.setAttribute("data-jobbot-ui", "");
    // Keep the page's own handlers (modal focus traps, shortcuts) away from what you type in the panel.
    for (const t of ["keydown", "keyup", "keypress", "focusin", "focusout", "mousedown", "pointerdown", "click", "input"])
      host.addEventListener(t, (e) => { if (!(t === "keydown" && e.ctrlKey && e.altKey)) e.stopPropagation(); });
    const root = host.attachShadow({ mode: "open" });
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      root.adoptedStyleSheets = [sheet];
    } catch {
      root.appendChild(el("style", { text: CSS }));
    }
    body = el("div", { class: "body" });
    const box = el("div", { class: "box" },
      el("div", { class: "head" }, el("span", { text: "JobBot" }),
         el("button", { class: "close", title: "Close", text: "×", onclick: () => panel.hide() })),
      body);
    root.appendChild(box);
  }

  function mount() {
    if (!host) build();
    if (!host.isConnected) (document.body || document.documentElement).appendChild(host);
    host.style.display = "";
  }

  function question(q) {
    const id = `jb-${q.qid}`;
    let input;
    if (q.options && q.options.length) {
      input = el("select", { id }, el("option", { value: "", text: "—" }));
      for (const o of q.options) {
        const opt = el("option", { value: o, text: o });
        if (o === q.value) opt.selected = true;
        input.append(opt);
      }
    } else if (q.kind === "textarea") {
      input = el("textarea", { id });
      input.value = q.value || "";
    } else {
      input = el("input", { id, type: "text" });
      input.value = q.value || "";
    }
    input.dataset.qid = q.qid;
    const save = el("input", { type: "checkbox" });
    save.checked = q.save !== false;
    save.dataset.saveFor = q.qid;
    return el("div", { class: "q" },
      el("label", { class: "t", for: id, text: q.label }), input,
      q.hint ? el("div", { class: "hint", text: q.hint }) : null,
      el("label", { class: "save" }, save, document.createTextNode("Save to profile")));
  }

  const panel = {
    show(text) {
      mount();
      body.replaceChildren(el("p", { class: "status", text }));
    },
    render(spec) {
      mount();
      const parts = [];
      if (spec.roles && spec.roles.length) {
        const sel = el("select", { class: "role", title: "Role you're applying as (shapes AI drafts and the CV)",
          onchange: () => send({ type: "role", role: sel.value }) });
        for (const r of spec.roles) {
          const o = el("option", { value: r, text: r });
          if (r === spec.role) o.selected = true;
          sel.append(o);
        }
        parts.push(el("label", { class: "rolebar" }, document.createTextNode("Role "), sel));
      }
      parts.push(el("p", { class: "status", text: spec.status || "" }));
      if (spec.drafts && spec.drafts.length) {
        parts.push(el("h4", { text: "AI drafts – review before submitting" }),
                   el("ul", {}, ...spec.drafts.map((d) => el("li", {},
                     el("span", { class: "badge", text: "AI draft" }), document.createTextNode(" " + d)))));
      }
      if (spec.conflicts && spec.conflicts.length) {
        parts.push(el("h4", { text: "Profile conflicts" }));
        for (const c of spec.conflicts) {
          const btn = el("button", { class: "small", text: "Update profile",
            onclick: () => { btn.disabled = true; send({ type: "replace", cid: c.cid }); } });
          parts.push(el("div", { class: "conflict" }, el("div", { text: c.text }), btn));
        }
      }
      if (spec.questions && spec.questions.length) {
        parts.push(el("h4", { text: "Needs your answer" }));
        const form = el("div");
        for (const q of spec.questions) form.append(question(q));
        const go = el("button", { class: "go", text: spec.button || "Fill" });
        go.addEventListener("click", () => {
          const answers = [...form.querySelectorAll("[data-qid]")].map((i) => ({
            qid: i.dataset.qid, value: i.value,
            save: form.querySelector(`[data-save-for="${i.dataset.qid}"]`).checked,
          })).filter((a) => a.value.trim());
          go.disabled = true;
          go.textContent = "Filling…";
          send({ type: "answers", answers });
        });
        parts.push(form, go);
      }
      if (spec.manual && spec.manual.length) {
        parts.push(el("h4", { text: "Do these yourself" }), el("ul", {}, ...spec.manual.map((m) => el("li", { text: m }))));
      }
      if (spec.notes && spec.notes.length) {
        parts.push(el("h4", { text: "Details" }),
                   el("ul", { class: "notes" }, ...spec.notes.map((m) => el("li", { text: m }))));
      }
      body.replaceChildren(...parts);
    },
    hide() {
      if (host) host.style.display = "none";
    },
  };

  window.__jobbotPanel = panel;
})();
