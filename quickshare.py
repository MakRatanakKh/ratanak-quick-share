"""RatanakQuickShare: Windows desktop controls and QR-code pairing."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import socket
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import qrcode
from PIL import ImageTk

from server import PORT, ShareState, make_server, publish_pc_file


def app_assets():
    bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_dir / "assets"


def local_ips():
    ips = []
    # A UDP 'connect' chooses an interface; no packet needs to be sent.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = result[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        ips.append("127.0.0.1")
    return ips


class RatanakQuickShareWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RatanakQuickShare • PC ↔ Phone")
        self.root.geometry("600x700")
        # A short window should still be usable: the content scrolls vertically.
        self.root.minsize(510, 380)
        self.root.configure(bg="#f6f8fc")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        legacy_dir = Path.home() / "QuickShareFiles"
        data_dir = legacy_dir if legacy_dir.exists() else Path.home() / "RatanakQuickShareFiles"
        self.state = ShareState(data_dir)
        self.events = queue.Queue()
        self.server = None
        self.running = False
        self.qr_photo = None
        self.ip = tk.StringVar(value=local_ips()[0])
        self.clip_enabled = tk.BooleanVar(value=False)
        self.message = tk.StringVar(value="Starting local server…")
        self.clip_status = tk.StringVar(value="Clipboard syncing is off")
        self.link_var = tk.StringVar()
        self.build_ui()
        self.ip.trace_add("write", lambda *_: self.change_interface())
        self.start_server()
        self.root.after(500, self.tick)

    def build_ui(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f6f8fc")
        style.configure("TLabel", background="#f6f8fc", foreground="#18243b", font=("Segoe UI", 10))
        style.configure("Heading.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#66758c")
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        style.configure("TCheckbutton", background="#f6f8fc", font=("Segoe UI", 10))
        # ttk.Frame does not scroll on its own. Put the entire interface inside
        # a Canvas window, with a real scrollbar and a width that tracks resize.
        scroll_host = ttk.Frame(self.root)
        scroll_host.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(scroll_host, bg="#f6f8fc", bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(scroll_host, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)

        main = ttk.Frame(self.scroll_canvas, padding=(26, 20))
        self.scroll_window = self.scroll_canvas.create_window((0, 0), window=main, anchor="nw")
        main.bind("<Configure>", self.update_scroll_region)
        self.scroll_canvas.bind("<Configure>", self.resize_scroll_content)
        self.root.bind_all("<MouseWheel>", self.scroll_with_wheel, add="+")
        # These also make development on Linux work with a traditional wheel.
        self.root.bind_all("<Button-4>", self.scroll_with_wheel, add="+")
        self.root.bind_all("<Button-5>", self.scroll_with_wheel, add="+")
        ttk.Label(main, text="RatanakQuickShare", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(main, text="Share files and text with your phone. No phone app required.", style="Sub.TLabel").pack(anchor="w", pady=(2, 12))
        ttk.Label(main, textvariable=self.message, foreground="#067348").pack(anchor="w", pady=(0, 8))

        row = ttk.Frame(main)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Computer's network address:").pack(side="left")
        self.ip_box = ttk.Combobox(row, textvariable=self.ip, values=local_ips(), width=18, state="readonly")
        self.ip_box.pack(side="right")

        self.qr_label = ttk.Label(main)
        self.qr_label.pack(pady=(4, 4))
        ttk.Label(main, text="Scan this QR code with your phone's camera.", style="Sub.TLabel").pack()
        entry = ttk.Entry(main, textvariable=self.link_var, state="readonly", font=("Consolas", 9))
        entry.pack(fill="x", pady=(8, 7))
        link_actions = ttk.Frame(main)
        link_actions.pack(fill="x")
        ttk.Button(link_actions, text="Copy pairing link", command=self.copy_link).pack(side="left", padx=(0, 8))
        ttk.Button(link_actions, text="Test in browser", command=self.open_browser).pack(side="left")
        ttk.Button(link_actions, text="Reset pairing", command=self.reset_pairing).pack(side="right")
        ttk.Label(main, text="QR expires after 60 seconds • Each connection needs Windows approval.",
                  style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

        ttk.Separator(main).pack(fill="x", pady=12)
        ttk.Label(main, text="Device security", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.approvals_frame = ttk.Frame(main)
        self.approvals_frame.pack(fill="x", pady=(5, 4))
        ttk.Label(main, text="Connected browsers (sessions expire after 1 hour):", style="Sub.TLabel").pack(anchor="w")
        self.devices_frame = ttk.Frame(main)
        self.devices_frame.pack(fill="x", pady=(4, 0))
        self._last_security_view = None

        ttk.Separator(main).pack(fill="x", pady=15)
        ttk.Label(main, text="Clipboard", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Checkbutton(main, text="Enable clipboard sync (shares copied text)", variable=self.clip_enabled,
                        command=self.toggle_clipboard).pack(anchor="w", pady=(5, 3))
        ttk.Label(main, textvariable=self.clip_status, style="Sub.TLabel", wraplength=510).pack(anchor="w")

        ttk.Separator(main).pack(fill="x", pady=15)
        ttk.Label(main, text="Files", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(main, text="Only files you explicitly add to Outbox are downloadable on the phone.",
                  style="Sub.TLabel", wraplength=510).pack(anchor="w", pady=(3, 9))
        file_actions = ttk.Frame(main)
        file_actions.pack(fill="x")
        ttk.Button(file_actions, text="Add files for phone…", command=self.add_files).pack(side="left", padx=(0, 8))
        ttk.Button(file_actions, text="Open Outbox", command=lambda: self.open_folder(self.state.outbox)).pack(side="left", padx=(0, 8))
        ttk.Button(file_actions, text="Open Inbox", command=lambda: self.open_folder(self.state.inbox)).pack(side="left")
        ttk.Label(main, text=f"Phone uploads are saved to {self.state.inbox}.", style="Sub.TLabel", wraplength=530).pack(anchor="w", pady=(9, 0))
        ttk.Label(main, text="Trusted Wi-Fi only • Traffic is not encrypted • Close this window to stop sharing.",
                  style="Sub.TLabel", wraplength=530).pack(anchor="w", pady=(13, 0))

    def update_scroll_region(self, _event=None):
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def resize_scroll_content(self, event):
        # Keep all sections as wide as the visible area, not their old size.
        self.scroll_canvas.itemconfigure(self.scroll_window, width=event.width)
        self.update_scroll_region()

    def scroll_with_wheel(self, event):
        # A global binding catches events over buttons/labels as well, but must
        # not steal wheel input from another window or a popup menu.
        hovered = self.root.winfo_containing(event.x_root, event.y_root)
        while hovered is not None:
            if hovered in (self.scroll_canvas, self.scrollbar):
                break
            hovered = hovered.master
        else:
            return

        bounds = self.scroll_canvas.bbox("all")
        if not bounds or bounds[3] - bounds[1] <= self.scroll_canvas.winfo_height():
            return
        if getattr(event, "num", None) == 4:
            steps = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return
            # Windows wheels typically deliver +/-120; handle small trackpad
            # deltas too, rather than ignoring them after rounding to zero.
            steps = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
        self.scroll_canvas.yview_scroll(steps, "units")
        return "break"

    def pairing_link(self):
        with self.state.lock:
            token = self.state.pair_token
        return f"http://{self.ip.get()}:{PORT}/pair/{token}"

    def update_qr(self):
        url = self.pairing_link()
        self.link_var.set(url)
        image = qrcode.make(url).convert("RGB").resize((222, 222))
        self.qr_photo = ImageTk.PhotoImage(image, master=self.root)
        self.qr_label.configure(image=self.qr_photo)

    def start_server(self):
        try:
            self.server = make_server(self.state, app_assets(), host=self.ip.get(), port=PORT)
        except OSError as error:
            self.message.set(f"Cannot start on port {PORT}: {error}")
            messagebox.showerror("RatanakQuickShare could not start", f"Port {PORT} may already be in use.\n\n{error}")
            self.update_qr()
            return
        self.running = True
        threading.Thread(target=self.server.serve_forever, name="RatanakQuickShareHTTP", daemon=True).start()
        self.message.set(f"● Sharing on {self.ip.get()}:{PORT} (selected interface only)")
        self.update_qr()

    def change_interface(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.running = False
            self.state.reset_pairing()
            self.start_server()
        else:
            self.update_qr()

    def refresh_security_view(self):
        pending = self.state.pending_requests()
        sessions = self.state.session_list()
        signature = (tuple(pending), tuple((token, label) for token, label, _ in sessions))
        if signature == self._last_security_view:
            return
        self._last_security_view = signature
        for frame in (self.approvals_frame, self.devices_frame):
            for widget in frame.winfo_children():
                widget.destroy()
        if not pending:
            ttk.Label(self.approvals_frame, text="No pending requests", style="Sub.TLabel").pack(anchor="w")
        for ticket, code, ip in pending:
            row = ttk.Frame(self.approvals_frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=f"Phone {ip} • code {code}").pack(side="left")
            ttk.Button(row, text="Reject", command=lambda k=ticket: self.decide_pairing(k, False)).pack(side="right")
            ttk.Button(row, text="Approve", command=lambda k=ticket: self.decide_pairing(k, True)).pack(side="right", padx=4)
        if not sessions:
            ttk.Label(self.devices_frame, text="No connected browsers", style="Sub.TLabel").pack(anchor="w")
        for token, label, remaining in sessions:
            row = ttk.Frame(self.devices_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label).pack(side="left")
            ttk.Button(row, text="Disconnect", command=lambda k=token: self.revoke_device(k)).pack(side="right")

    def decide_pairing(self, ticket, approve):
        if self.state.decide(ticket, approve):
            self.message.set("Connection approved." if approve else "Connection rejected.")
        self._last_security_view = None
        self.refresh_security_view()

    def revoke_device(self, token):
        self.state.revoke_session(token)
        self.message.set("Device disconnected.")
        self._last_security_view = None
        self.refresh_security_view()

    def copy_link(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.pairing_link())
        self.root.update_idletasks()
        self.message.set("Pairing link copied. Keep it private.")

    def open_browser(self):
        if self.running:
            webbrowser.open(self.pairing_link())

    def reset_pairing(self):
        self.state.reset_pairing()
        self.update_qr()
        self.message.set("Pairing reset. All browser sessions and pending requests revoked.")
        self._last_security_view = None

    def toggle_clipboard(self):
        self.state.set_clipboard_enabled(self.clip_enabled.get())
        self.clip_status.set("Clipboard syncing on: copied text is visible to paired browsers." if self.clip_enabled.get()
                             else "Clipboard syncing is off")

    def tick(self):
        if self.state.rotate_if_expired():
            self.update_qr()
        else:
            # A scan consumes the visible QR immediately.
            if self.state.pair_token not in self.link_var.get():
                self.update_qr()
        self.refresh_security_view()
        if self.clip_enabled.get():
            pending = self.state.take_pending_clipboard()
            if pending is not None:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(pending)
                    self.root.update_idletasks()
                except tk.TclError:
                    self.clip_status.set("Could not write to Windows clipboard; another app may be using it.")
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                text = None
            self.state.observe_clipboard(text)
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "added":
                self.message.set(f"Added {value} file(s). They're available on your phone.")
            elif kind == "error":
                self.message.set(f"File error: {value}")
        self.root.after(650, self.tick)

    def add_files(self):
        paths = filedialog.askopenfilenames(title="Choose files to share with your phone")
        if not paths:
            return
        self.message.set("Copying selected files into Outbox…")

        def worker():
            copied = 0
            for path in paths:
                try:
                    publish_pc_file(self.state, Path(path))
                    copied += 1
                except (OSError, ValueError) as error:
                    self.events.put(("error", str(error)))
            self.events.put(("added", copied))

        threading.Thread(target=worker, name="RatanakQuickShareCopy", daemon=True).start()

    def open_folder(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
        except OSError as error:
            messagebox.showerror("Cannot open folder", str(error))

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    RatanakQuickShareWindow().run()
