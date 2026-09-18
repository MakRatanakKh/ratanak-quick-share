# RatanakQuickShare v1.4 — Windows ↔ phone

Share files and clipboard text with your Android or iPhone browser over a **trusted, private LAN**. No phone app or cloud account is needed. This version adds short-lived, single-use QR codes, Windows approval, per-browser revocable sessions, interface binding, network filtering, timeouts and upload resource limits. **Traffic is still unencrypted HTTP: this is not safe for hostile or public Wi-Fi.**

## Run on Windows

1. Install Python 3.11 or later and download/extract the whole project, including `assets/`.
2. Double-click `START_WINDOWS.bat`. On first run it creates `.venv/` in this directory and installs QR/Pillow requirements **inside that virtual environment only**. No global packages are installed. First run requires internet access.
3. Allow access in Windows Firewall on **Private networks only**. Connect phone and PC to the same trusted Wi-Fi. Choose the PC's Wi-Fi IP address in the dropdown; changing it restarts the server, disconnects browsers, and invalidates pairing.
4. Scan the displayed QR code within 60 seconds. On your phone, compare the six-digit code with the new request in the Windows app, then click **Approve** on Windows. Never approve an unexpected request. Approvals expire after 90 seconds.
5. Your phone browser opens the sharing page. Upload phone files to Inbox, or explicitly select PC files to offer from Outbox. Clipboard sync remains off until you check its box on Windows.

The Windows window scrolls vertically, including mouse-wheel support. `Reset pairing` revokes all sessions and pending requests; you can also disconnect an individual browser under Device security. Each browser session expires after one hour; re-pair when necessary. A pairing QR can be used only once and is refreshed automatically after scanning or expiry.

## Data and limits

- A new installation uses `%USERPROFILE%\RatanakQuickShareFiles\Inbox` and `Outbox`. Existing `%USERPROFILE%\QuickShareFiles` is reused for upgrades. Only explicitly shared Outbox files are downloadable.
- Files upload individually: maximum **128 MiB each**, **1 GiB total Inbox quota**, up to **2 concurrent uploads**. There is a maximum of 12 active connections and a 12-second socket inactivity timeout. Large transfers on slow Wi-Fi may time out. Remove Inbox files to free quota. Do not upload untrusted files; treat them like any other download before opening them.
- The server binds only to the IP selected in the Windows dropdown on port 8765 and admits only its IPv4 /24 subnet (or loopback for localhost testing). Networks using a larger/different subnet can be excluded. This is not a substitute for Windows Firewall, nor does it prevent malicious devices already on that subnet.
- Phone browsers cannot silently access the phone clipboard on HTTP; phone-to-PC text needs an explicit Send action, and copying PC text may require manual selection. Clipboard syncing exposes copied text to any currently approved browser, so avoid passwords and private data.

## Security limitations

**HTTP traffic has no encryption or authenticated server identity.** A network attacker who can observe or interfere with traffic may steal the pairing link, pending approval ticket, session cookie, files, or clipboard content; they may also impersonate a browser. A Windows approval is not a substitute for HTTPS. Use only a trusted private Wi-Fi network, do not port-forward the server, and never treat this version as secure on public, guest or hotel networks. For hostile networks, add properly validated HTTPS or a trusted encrypted tunnel instead. Binding to an interface and filtering IPs do not establish device identity.

Passwords or credentials are not stored by the application. Session tokens, pairing links and tickets live in memory and are lost on close. Cookies use `HttpOnly` and `SameSite=Strict`, and the server checks Origin for mutating API calls; CSRF controls do not prevent on-path HTTP interception. Files stay on the PC except files downloaded to a phone, which may remain in its Downloads folder.

## EXE, development and tests

Double-click `BUILD_EXE_WINDOWS.bat` to build `dist\RatanakQuickShare.exe` on **Windows**, using only the same `.venv`. This repository is source code, not a prebuilt executable. To run manually: `.venv\Scripts\python.exe quickshare.py`. If relocating the source folder, recreate `.venv` in the new location.

Run tests: `.venv\Scripts\python.exe -m unittest discover -s tests -v`. The optional Tk GUI test needs a display. The source is `quickshare.py` (Tkinter), `server.py` (standard-library HTTP backend), `assets/` (browser interface and approval polling), `tests/` and local-only Windows batch scripts. `.venv/`, `dist/`, and shared data folders are excluded by `.gitignore`.