"use strict";
const statusElement = document.getElementById("status");
const poll = async () => {
  try {
    const response = await fetch("/pair/status", { cache: "no-store" });
    const result = await response.json();
    if (result.status === "approved") { location.replace("/"); return; }
    if (result.status !== "waiting") { statusElement.textContent = result.status + ". Scan a new QR code."; clearInterval(timer); }
  } catch (_) { statusElement.textContent = "Connection lost. Reconnect to your PC."; }
};
const timer = setInterval(poll, 1200);
