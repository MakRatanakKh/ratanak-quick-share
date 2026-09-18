# RatanakQuickShare · Windows ↔ phone

Version 1.3 renames the app to RatanakQuickShare and retains vertical scrolling, mouse-wheel support and a resizable Windows interface. No new dependencies.

Transfer files and clipboard text between your Windows PC and an Android or iPhone browser over **trusted local Wi-Fi**, without a phone app, account, or cloud storage.

## Run on Windows

1. Install Python 3.11 or newer from https://www.python.org/downloads/windows/ .
2. Download/clone this repository, keeping the `assets` directory next to the Python files.
3. Double-click **`START_WINDOWS.bat`**. It creates a project-local **`.venv`** on first run, installs dependencies *only inside it*, and always launches using `.venv\Scripts\python.exe`. First setup requires internet access.
4. If prompted by Windows Firewall, allow Python on **Private networks only**.
5. Connect PC and phone to the same trusted Wi-Fi, scan the QR code in the RatanakQuickShare window and open the link.
6. Add Windows files using **Add files for phone…**, or upload files from the phone webpage. Clipboard syncing is disabled until you enable it in the Windows window.

The Windows GUI has a scrollbar on the right and supports mouse-wheel scrolling, including when the window is shorter than its contents. You can launch manually with `.venv\Scripts\python.exe quickshare.py` from the project directory. Do not install with global `pip`. The only use of system Python is to create `.venv`; all app packages remain private to the project. If you relocate the project, recreate `.venv` in the new location.

## Shared folders

A new installation stores explicitly shared files in `%USERPROFILE%\RatanakQuickShareFiles\Outbox` and phone uploads in `%USERPROFILE%\RatanakQuickShareFiles\Inbox`. When upgrading from QuickShare v1.x, an existing `%USERPROFILE%\QuickShareFiles` is reused automatically so your old shared files remain available. The app does not publish your entire PC. Empty the Outbox to stop offering a file for download.

## Optional Windows executable

Double-click `BUILD_EXE_WINDOWS.bat` on Windows. It creates/reuses the same `.venv`, installs PyInstaller only there, and produces `dist\RatanakQuickShare.exe`. The EXE bundles its Python runtime and assets; it must be built on Windows. There is no installer, auto-start, code-signing certificate or system tray feature yet. Generated `dist/`, `build/` and `.venv/` folders are excluded from Git.

## Phone browser limitations

The phone webpage can display PC clipboard text automatically while open, but cannot silently read or write the phone's clipboard on ordinary HTTP. Tap **Copy text to phone**; if the browser forbids copying, select/copy the highlighted text manually. Phone-to-PC clipboard updates require tapping **Send to PC clipboard**. Files are uploaded one at a time, up to 512 MiB per file, with enough free PC disk space required. The server listens on port 8765. Choose the correct LAN IP in the Windows dropdown; `127.0.0.1` works only on the PC.

## Security

The QR pairing link acts as a password. A random token is exchanged for an HttpOnly, SameSite session cookie, and **Reset pairing** invalidates older sessions. Clipboard syncing is disabled by default. File downloads are limited to Outbox, uploads go to Inbox, and authenticated modification requests require a custom header. **HTTP traffic is not encrypted**: use only a trusted private Wi-Fi network, never expose port 8765 to the Internet or use public/hotel Wi-Fi. Other network devices and firewall configuration still matter. Keep pairing links and sensitive clipboard text private. Close the Windows window to stop sharing.

## Project layout and tests

- `quickshare.py`: Tkinter Windows UI, QR pairing and clipboard sync.
- `server.py`: Python standard-library HTTP server, pairing, authentication, file transfers.
- `assets/`: mobile-friendly HTML, CSS and JavaScript.
- `START_WINDOWS.bat`, `BUILD_EXE_WINDOWS.bat`: isolated Windows launcher/build scripts.
- `tests/`: server tests and GUI scroll regression test.

After setting up `.venv`, run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. The GUI test requires a working display; server tests do not require a GUI. Dependencies install into `.venv\Lib\site-packages` and nowhere globally.
