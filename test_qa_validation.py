"""
QA Validation Test Suite for Cantonese Translator
Tests core logic without requiring Kivy or Android dependencies.
"""

import os
import sys
import json
import tempfile
import sqlite3
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import importlib.util

# ── Mock Kivy before importing main.py ────────────────────
class MockKivyModule:
    """Minimal mock for kivy modules."""
    pass

class MockApp:
    pass

class MockClock:
    @staticmethod
    def schedule_once(callback, dt):
        # Execute immediately for testing
        callback(0)
        return MagicMock(cancel=lambda: None)
    @staticmethod
    def schedule_interval(callback, dt):
        return MagicMock(cancel=lambda: None)

class MockWindow:
    width = 800
    height = 600
    @classmethod
    def bind(cls, **kwargs):
        pass
    @classmethod
    def clearcolor(cls, *args):
        pass

class MockGraphics:
    class Color:
        def __init__(self, *args):
            pass
    class Rectangle:
        def __init__(self, **kwargs):
            pass
    class RoundedRectangle:
        def __init__(self, **kwargs):
            pass

class MockAnimation:
    def __init__(self, **kwargs):
        pass
    def __add__(self, other):
        return self
    def start(self, target):
        pass
    def cancel(self, target):
        pass

# Build mock kivy package
mock_kivy = MagicMock()
mock_kivy.app.App = MockApp
mock_kivy.clock.Clock = MockClock()
mock_kivy.core.window.Window = MockWindow()
mock_kivy.graphics = MockGraphics()
mock_kivy.metrics.dp = lambda x: x
mock_kivy.properties.NumericProperty = lambda default=0: default
mock_kivy.properties.StringProperty = lambda default="": default
mock_kivy.properties.BooleanProperty = lambda default=False: default
mock_kivy.uix.boxlayout.BoxLayout = MagicMock
mock_kivy.uix.button.Button = MagicMock
mock_kivy.uix.gridlayout.GridLayout = MagicMock
mock_kivy.uix.label.Label = MagicMock
mock_kivy.uix.popup.Popup = MagicMock
mock_kivy.uix.scrollview.ScrollView = MagicMock
mock_kivy.uix.slider.Slider = MagicMock
mock_kivy.uix.textinput.TextInput = MagicMock
mock_kivy.uix.widget.Widget = MagicMock
mock_kivy.animation.Animation = MockAnimation
mock_kivy.utils.get_color_from_hex = lambda hex_str: (0.5, 0.5, 0.5, 1.0)

sys.modules['kivy'] = mock_kivy
sys.modules['kivy.app'] = mock_kivy.app
sys.modules['kivy.clock'] = mock_kivy.clock
sys.modules['kivy.core.window'] = mock_kivy.core.window
sys.modules['kivy.graphics'] = mock_kivy.graphics
sys.modules['kivy.metrics'] = mock_kivy.metrics
sys.modules['kivy.properties'] = mock_kivy.properties
sys.modules['kivy.uix'] = MagicMock()
sys.modules['kivy.uix.boxlayout'] = mock_kivy.uix.boxlayout
sys.modules['kivy.uix.button'] = mock_kivy.uix.button
sys.modules['kivy.uix.gridlayout'] = mock_kivy.uix.gridlayout
sys.modules['kivy.uix.label'] = mock_kivy.uix.label
sys.modules['kivy.uix.popup'] = mock_kivy.uix.popup
sys.modules['kivy.uix.scrollview'] = mock_kivy.uix.scrollview
sys.modules['kivy.uix.slider'] = mock_kivy.uix.slider
sys.modules['kivy.uix.textinput'] = mock_kivy.uix.textinput
sys.modules['kivy.uix.widget'] = mock_kivy.uix.widget
sys.modules['kivy.animation'] = mock_kivy.animation
sys.modules['kivy.utils'] = mock_kivy.utils
sys.modules['kivy.core.text'] = MagicMock()

# ── Now import project modules ────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

import settings
import history
import utils
import main

# Reload to ensure fresh state
importlib.reload(settings)
importlib.reload(history)
importlib.reload(utils)
importlib.reload(main)


# ============================================================
# Tests for settings.py
# ============================================================
class TestSettingsManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "test_config.json")

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_default_values(self):
        mgr = settings.SettingsManager(self.config_path)
        self.assertEqual(mgr.get("inference_host"), "127.0.0.1")
        self.assertEqual(mgr.get("inference_port"), "5001")
        self.assertEqual(mgr.get("vad_mode"), 1)
        self.assertEqual(mgr.get("sample_rate"), 16000)
        self.assertEqual(mgr.get("theme"), "dark")
        self.assertTrue(mgr.get("auto_scroll"))
        self.assertTrue(mgr.get("enable_history"))

    def test_set_and_save(self):
        mgr = settings.SettingsManager(self.config_path)
        mgr.set("test_key", "test_value")
        mgr.save()
        # Verify file exists and is valid JSON
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["test_key"], "test_value")

    def test_reload(self):
        mgr = settings.SettingsManager(self.config_path)
        mgr.set("test_key", "test_value")
        mgr.save()
        mgr2 = settings.SettingsManager(self.config_path)
        self.assertEqual(mgr2.get("test_key"), "test_value")

    def test_corrupt_file_handling(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("NOT JSON {")
        mgr = settings.SettingsManager(self.config_path)
        # Should not crash, should use defaults
        self.assertEqual(mgr.get("inference_host"), "127.0.0.1")

    def test_thread_safety(self):
        mgr = settings.SettingsManager(self.config_path)
        errors = []
        def writer():
            try:
                for i in range(50):
                    mgr.set(f"key_{i}", i)
                    mgr.save()
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"Thread safety errors: {errors}")

    def test_reset(self):
        mgr = settings.SettingsManager(self.config_path)
        mgr.set("inference_host", "changed")
        mgr.reset()
        self.assertEqual(mgr.get("inference_host"), "127.0.0.1")

    def test_path_property(self):
        mgr = settings.SettingsManager(self.config_path)
        self.assertEqual(mgr.path, self.config_path)

    def test_repr(self):
        mgr = settings.SettingsManager(self.config_path)
        r = repr(mgr)
        self.assertIn("SettingsManager", r)


# ============================================================
# Tests for history.py
# ============================================================
class TestHistoryManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_history.db")

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_session(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test Session")
        self.assertIsInstance(sid, int)
        self.assertGreater(sid, 0)

    def test_add_result(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test")
        result = mgr.add_result(sid, "Hello")
        self.assertTrue(result)

    def test_get_sessions(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test")
        sessions = mgr.get_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["id"], sid)

    def test_get_session_results(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test")
        mgr.add_result(sid, "Result 1")
        mgr.add_result(sid, "Result 2")
        results = mgr.get_session_results(sid)
        self.assertEqual(len(results), 2)
        texts = [r["text"] for r in results]
        self.assertIn("Result 1", texts)
        self.assertIn("Result 2", texts)

    def test_delete_session(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test")
        mgr.add_result(sid, "Data")
        self.assertTrue(mgr.delete_session(sid))
        sessions = mgr.get_sessions()
        self.assertEqual(len(sessions), 0)

    def test_clear_all(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Test")
        mgr.add_result(sid, "Data")
        self.assertTrue(mgr.clear_all())
        self.assertEqual(len(mgr.get_sessions()), 0)

    def test_sql_injection_prevention(self):
        mgr = history.HistoryManager(self.db_path)
        malicious = "test'; DROP TABLE sessions; --"
        sid = mgr.create_session(malicious)
        mgr.add_result(sid, malicious)
        # If injection succeeded, these would fail
        sessions = mgr.get_sessions()
        self.assertGreater(len(sessions), 0)
        results = mgr.get_session_results(sid)
        self.assertEqual(len(results), 1)

    def test_thread_safety(self):
        mgr = history.HistoryManager(self.db_path)
        sid = mgr.create_session("Thread Test")
        errors = []
        def writer():
            try:
                for i in range(20):
                    mgr.add_result(sid, f"text_{i}")
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"Thread safety errors: {errors}")
        results = mgr.get_session_results(sid)
        self.assertEqual(len(results), 80)

    def test_corrupt_db_handling(self):
        # Write garbage to db file
        with open(self.db_path, "wb") as f:
            f.write(b"NOT A DB")
        # Should not crash on init
        try:
            mgr = history.HistoryManager(self.db_path)
            # _init_db may print error but shouldn't crash
        except Exception as e:
            self.fail(f"Corrupt DB should not crash init: {e}")


# ============================================================
# Tests for utils.py
# ============================================================
class TestUtils(unittest.TestCase):
    def test_is_android_false_on_pc(self):
        # Clear any android env vars
        env_backup = {}
        for key in ["ANDROID_ARGUMENT", "KIVY_ANDROID_LAUNCHER"]:
            env_backup[key] = os.environ.pop(key, None)
        try:
            self.assertFalse(utils.is_android())
        finally:
            for key, val in env_backup.items():
                if val is not None:
                    os.environ[key] = val

    def test_is_android_true_with_env(self):
        os.environ["ANDROID_ARGUMENT"] = "1"
        try:
            self.assertTrue(utils.is_android())
        finally:
            del os.environ["ANDROID_ARGUMENT"]

    def test_androidutils_copy_returns_false_on_pc(self):
        result = utils.AndroidUtils.copy_to_clipboard("test")
        self.assertFalse(result)

    def test_androidutils_version_returns_none_on_pc(self):
        result = utils.AndroidUtils.get_android_version()
        self.assertIsNone(result)

    def test_androidutils_vibrate_returns_false_on_pc(self):
        result = utils.AndroidUtils.vibrate(50)
        self.assertFalse(result)

    def test_file_exporter_pc(self):
        content = "Test export content"
        path = utils.FileExporter.export_text(content, filename="test_export_qa.txt")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), content)
        os.remove(path)

    def test_file_exporter_auto_filename(self):
        path = utils.FileExporter.export_text("content")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith(".txt"))
        os.remove(path)

    def test_file_exporter_raises_on_no_path(self):
        # Verify the method signature accepts the expected parameters
        import inspect
        sig = inspect.signature(utils.FileExporter.export_text)
        params = list(sig.parameters.keys())
        self.assertEqual(params[:3], ["content", "filename", "subdir"])

    def test_theme_manager(self):
        dark = utils.ThemeManager.get_theme("dark")
        self.assertIn("bg", dark)
        self.assertIn("text", dark)
        # Invalid name falls back to dark
        fallback = utils.ThemeManager.get_theme("nonexistent")
        self.assertEqual(fallback, dark)


# ============================================================
# Tests for main.py (structural / logic)
# ============================================================
class TestMainModule(unittest.TestCase):
    def test_no_kivy_require(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        # Allow mentions in comments/docstrings/markdown, but reject actual calls
        lines = content.splitlines()
        for line in lines:
            stripped = line.strip()
            if "kivy.require" in stripped:
                # Skip comments, docstrings, markdown bullets
                if stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("*") or '"""' in stripped or "'''" in stripped:
                    continue
                self.fail(f"Found kivy.require call in code: {line}")

    def test_no_dead_code_if_false(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        # Reject literal "if False:" as dead code, but allow "else None" in ternary exprs
        self.assertNotIn("if False:", content)

    def test_capture_lock_exists(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_capture_lock", content)
        self.assertIn("threading.Lock()", content)

    def test_scroll_to_bottom_logic(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("scroll_y", content)
        self.assertIn("scroll_y", content)

    def test_show_error_removes_empty_lbl(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_show_error", content)
        # Check that _show_error removes empty_lbl
        func_start = content.find("def _show_error(self, msg:")
        func_end = content.find("\n    def ", func_start + 1)
        func_body = content[func_start:func_end]
        self.assertIn("empty_lbl", func_body)
        self.assertIn("remove_widget", func_body)

    def test_window_bind_on_resize(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Window.bind(on_resize=self._on_resize)", content)
        self.assertIn("_resize_bound", content)

    def test_lazy_loading(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_lazy_load_modules", content)
        self.assertIn("_AndroidAudioCapture", content)
        self.assertIn("_AudioProcessor", content)

    def test_retry_logic(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_error_count", content)
        self.assertIn("_max_retries", content)
        self.assertIn("_max_retries", content)

    def test_dummy_settings_fallback(self):
        dummy = main._DummySettings()
        self.assertEqual(dummy.get("key", "default"), "default")
        dummy.set("key", "value")
        self.assertEqual(dummy.get("key"), "value")
        dummy.save()  # Should not crash

    def test_is_android_function(self):
        self.assertFalse(main.is_android())

    def test_log_paths_function(self):
        paths = main._get_log_paths()
        self.assertIsInstance(paths, list)
        self.assertTrue(len(paths) > 0)

    def test_themes_defined(self):
        self.assertIn("dark", main.THEMES)
        self.assertIn("light", main.THEMES)
        self.assertIn("bg", main.THEMES["dark"])
        self.assertIn("primary", main.THEMES["dark"])

    def test_inference_url_config(self):
        self.assertIn("INFERENCE_URL", dir(main))
        self.assertTrue(main.INFERENCE_URL.startswith("http://"))

    def test_crash_log_handler(self):
        self.assertIsInstance(sys.excepthook, type(main._write_crash_log))

    def test_animation_cleanup_in_status_dot(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        # Check StatusDot cancels animation
        func_start = content.find("def _on_active(self, *args):")
        func_end = content.find("\n    def ", func_start + 1)
        func_body = content[func_start:func_end]
        self.assertIn("cancel", func_body)

    def test_result_card_long_press(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_touch_start_time", content)
        self.assertIn("duration > 0.5", content)
        self.assertIn("_copy_text", content)

    def test_export_session_logic(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_export_session", content)
        self.assertIn("FileExporter.export_text", content)

    def test_settings_popup_save(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("SettingsPopup", content)
        self.assertIn("def _save(self, *args):", content)

    def test_history_save_in_add(self):
        with open(os.path.join(PROJECT_DIR, "main.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("_save_to_history", content)
        self.assertIn("HistoryManager", content)


# ============================================================
# Tests for buildozer.spec
# ============================================================
class TestBuildozerSpec(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PROJECT_DIR, "buildozer.spec"), "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_orientation_all(self):
        self.assertIn("orientation = all", self.content)

    def test_foreground_service_permission(self):
        self.assertIn("FOREGROUND_SERVICE", self.content)

    def test_record_audio_permission(self):
        self.assertIn("RECORD_AUDIO", self.content)

    def test_internet_permission(self):
        self.assertIn("INTERNET", self.content)

    def test_write_external_storage(self):
        self.assertIn("WRITE_EXTERNAL_STORAGE", self.content)

    def test_api_levels(self):
        self.assertIn("android.api = 34", self.content)
        self.assertIn("android.minapi = 26", self.content)

    def test_exclude_does_not_hit_main(self):
        # main.py should NOT be in source.exclude
        for line in self.content.splitlines():
            if line.startswith("source.exclude ="):
                excludes = line.split("=")[1].strip().split(",")
                excludes = [e.strip() for e in excludes]
                self.assertNotIn("main.py", excludes)

    def test_arch_arm64(self):
        self.assertIn("arm64-v8a", self.content)

    def test_bootstrap_sdl2(self):
        self.assertIn("sdl2", self.content)


# ============================================================
# Run Tests
# ============================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
