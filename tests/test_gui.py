"""Optional Tkinter regression test; skips if a graphical display is unavailable."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class FakeServer:
    def serve_forever(self):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


class WindowScrollTests(unittest.TestCase):
    @unittest.skipIf(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                     "A graphical display is required to test Tkinter")
    def test_short_window_scrolls_to_files_and_accepts_mouse_wheel(self):
        try:
            import quickshare
        except ImportError as exc:
            self.skipTest(f"Optional GUI dependencies are unavailable: {exc}")

        # DISPLAY may be set to a stale value in a headless environment.
        try:
            probe = quickshare.tk.Tk()
        except quickshare.tk.TclError as exc:
            self.skipTest(f"No working graphical display: {exc}")
        else:
            probe.destroy()

        with patch.object(quickshare, "make_server", return_value=FakeServer()), \
             patch.object(quickshare, "local_ips", return_value=["127.0.0.1"]):
            app = quickshare.RatanakQuickShareWindow()
            try:
                app.root.geometry("520x420")
                app.root.update()
                canvas = app.scroll_canvas
                self.assertEqual(canvas.itemcget(app.scroll_window, "width"),
                                 str(canvas.winfo_width()))
                self.assertLess(canvas.yview()[1], 1.0, "Content should exceed the viewport")
                wheel = SimpleNamespace(x_root=canvas.winfo_rootx() + 80,
                                        y_root=canvas.winfo_rooty() + 80,
                                        delta=-120, num=None)
                self.assertEqual(app.scroll_with_wheel(wheel), "break")
                self.assertGreater(canvas.yview()[0], 0)
                canvas.yview_moveto(1)
                app.root.update()
                self.assertAlmostEqual(canvas.yview()[1], 1.0)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
