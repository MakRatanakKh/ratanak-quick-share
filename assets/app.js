"use strict";

const byId = id => document.getElementById(id);
const connection = byId("connection");
const pcText = byId("pcText");
const phoneText = byId("phoneText");
const copyButton = byId("copyButton");
const sendButton = byId("sendButton");
const clipboardBadge = byId("clipboardBadge");
const clipboardNote = byId("clipboardNote");
const sendNote = byId("sendNote");
const uploadButton = byId("uploadButton");
const fileInput = byId("fileInput");
const uploadNote = byId("uploadNote");
const fileList = byId("fileList");
let busy = false;
let clipboardEnabled = false;
let lastFiles = "";

function status(element, message, kind = "") {
  element.textContent = message;
  element.className = `note ${kind}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: { "X-QuickShare-Request": "1", ...(options.headers || {}) }
  });
  if (response.status === 401) {
    connection.textContent = "Pair again";
    connection.className = "pill disconnected";
    throw new Error("Pairing expired. Scan the QR code on Windows again.");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function fileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let number = bytes / 1024;
  let unit = 0;
  while (number >= 1024 && unit < units.length - 1) { number /= 1024; unit += 1; }
  return `${number.toFixed(number < 10 ? 1 : 0)} ${units[unit]}`;
}

function renderFiles(files) {
  const fingerprint = JSON.stringify(files);
  if (fingerprint === lastFiles) return;
  lastFiles = fingerprint;
  byId("fileCount").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
  fileList.replaceChildren();
  if (!files.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No shared files yet. Add files in the Windows app.";
    fileList.append(empty);
    return;
  }
  for (const file of files) {
    const row = document.createElement("div"); row.className = "file";
    const detail = document.createElement("div"); detail.className = "file-details";
    const icon = document.createElement("span"); icon.className = "file-icon"; icon.textContent = "↓";
    const text = document.createElement("div"); text.style.minWidth = "0";
    const name = document.createElement("div"); name.className = "file-name"; name.textContent = file.name;
    const metadata = document.createElement("div"); metadata.className = "file-meta"; metadata.textContent = fileSize(file.size);
    text.append(name, metadata); detail.append(icon, text);
    const download = document.createElement("a");
    download.href = file.url; download.textContent = "Download";
    download.setAttribute("download", file.name);
    row.append(detail, download); fileList.append(row);
  }
}

async function refresh() {
  try {
    const data = await api("/api/state");
    connection.textContent = "Connected";
    connection.className = "pill connected";
    clipboardEnabled = data.clipboard_enabled;
    clipboardBadge.textContent = clipboardEnabled ? "Sync on" : "Sync off";
    pcText.value = data.clipboard || "";
    pcText.placeholder = clipboardEnabled ? "No text in the PC clipboard" : "Enable clipboard syncing on Windows";
    copyButton.disabled = !clipboardEnabled || !data.clipboard;
    sendButton.disabled = !clipboardEnabled || busy;
    status(clipboardNote, data.clipboard_note || (clipboardEnabled ? "Clipboard ready." : "Clipboard syncing is off."));
    renderFiles(data.files);
  } catch (error) {
    connection.textContent = "Disconnected";
    connection.className = "pill disconnected";
    sendButton.disabled = true;
    copyButton.disabled = true;
    clipboardEnabled = false;
    status(clipboardNote, error.message, "error");
  }
}

copyButton.addEventListener("click", async () => {
  const value = pcText.value;
  if (!value) return;
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("Browser clipboard API unavailable");
    await navigator.clipboard.writeText(value);
    status(clipboardNote, "Copied to phone clipboard!", "success");
  } catch (_) {
    pcText.focus(); pcText.select();
    try {
      if (document.execCommand("copy")) {
        status(clipboardNote, "Copied to phone clipboard!", "success");
        return;
      }
    } catch (_) { /* Some browsers intentionally prohibit copying on HTTP. */ }
    status(clipboardNote, "Text selected. Use your phone’s Copy command to finish.");
  }
});

sendButton.addEventListener("click", async () => {
  if (busy || !clipboardEnabled) return;
  busy = true; sendButton.disabled = true;
  try {
    await api("/api/clipboard", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: phoneText.value })
    });
    status(sendNote, "Sent! Windows clipboard will update shortly.", "success");
    phoneText.value = "";
  } catch (error) {
    status(sendNote, error.message, "error");
  } finally {
    busy = false; sendButton.disabled = !clipboardEnabled;
  }
});

fileInput.addEventListener("change", () => {
  const files = [...fileInput.files];
  byId("fileChoice").textContent = files.length ? `${files.length} selected · ${files.map(f => f.name).join(", ")}` : "Tap to browse your phone";
  uploadButton.disabled = files.length === 0 || busy;
});

uploadButton.addEventListener("click", async () => {
  if (busy || !fileInput.files.length) return;
  const files = [...fileInput.files];
  busy = true; uploadButton.disabled = true;
  let done = 0;
  try {
    for (const file of files) {
      if (file.size > 512 * 1024 * 1024) throw new Error(`${file.name} exceeds the 512 MB upload limit.`);
      status(uploadNote, `Uploading ${done + 1} of ${files.length}: ${file.name} (${fileSize(file.size)})…`);
      const data = await api(`/api/upload?name=${encodeURIComponent(file.name)}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file
      });
      done += 1;
      status(uploadNote, `Uploaded ${done} of ${files.length}: ${data.name}`, "success");
    }
    fileInput.value = "";
    byId("fileChoice").textContent = "Tap to browse your phone";
    status(uploadNote, `${done} file${done === 1 ? "" : "s"} saved to Windows Inbox.`, "success");
  } catch (error) {
    status(uploadNote, `${done} uploaded. ${error.message}`, "error");
  } finally {
    busy = false; uploadButton.disabled = fileInput.files.length === 0;
  }
});

refresh();
setInterval(refresh, 1700);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
