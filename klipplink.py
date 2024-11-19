import sys
import json
import os
import time
import tldextract
import urllib.parse
import pyperclip
import keyboard
import logging
from logging.handlers import RotatingFileHandler
from PyQt5.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, QMenu, QAction, 
                             QMessageBox, QLabel, QVBoxLayout, QWidget, QPushButton, 
                             QLineEdit, QComboBox, QSlider, QInputDialog, QDesktopWidget)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QObject, QTimer
from functools import wraps
import asyncio

# Requirements file
requirements = """
pyperclip==1.8.2
keyboard==0.13.5
tldextract==3.1.2
PyQt5==5.15.4
"""

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TrayIconApp")
handler = RotatingFileHandler('TrayIconApp.log', maxBytes=10000, backupCount=5)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

# Retry decorator with exponential backoff
def retry_with_backoff(max_retries=3, initial_delay=1, max_delay=60, factor=2, jitter=True):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            delay = initial_delay
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.info(f"Retry attempt {retries + 1} for {func.__name__}. Error: {str(e)}")
                    if retries == max_retries - 1:
                        raise
                    if jitter:
                        # Adding jitter to reduce the chance of synchronized retries
                        delay = min(max_delay, delay * factor + random.uniform(0, 1))
                    else:
                        delay = min(max_delay, delay * factor)
                    time.sleep(delay)
                    retries += 1
        return wrapper
    return decorator

class ClipboardMonitor(QObject):
    clipboard_changed = pyqtSignal(str)

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.last_clipboard_content = None
        self.setup_clipboard_listener()

    def setup_clipboard_listener(self):
        self.monitor_thread = QThread()
        self.monitor_thread.started.connect(self._monitor_clipboard)
        self.monitor_thread.start()

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def _monitor_clipboard(self):
        while True:
            try:
                current_content = pyperclip.paste()
                if current_content != self.last_clipboard_content:
                    self.last_clipboard_content = current_content
                    self.clipboard_changed.emit(current_content)
            except pyperclip.PyperclipException as e:
                self.main_window.trace_source(f"Clipboard access failed: {str(e)}")
            time.sleep(0.5)

class URLThread(QThread):
    urlOpened = pyqtSignal(str, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    async def run(self):
        try:
            await asyncio.sleep(0.1)  # Simulate async operation
            QDesktopServices.openUrl(QUrl(self.url))
            self.urlOpened.emit("URL opened successfully", self.url)
        except Exception as e:
            self.urlOpened.emit("Error opening URL", str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.load_configuration()
        self.setup_trace_source()
        self.setup_tray_icon()
        self.setup_clipboard_monitor()
        self.setup_hotkey()

    def initUI(self):
        self.setWindowTitle("Tray Icon App")
        self.setGeometry(100, 100, 800, 450)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        label = QLabel("This application runs in the system tray.", self)
        layout.addWidget(label)

        self.show()

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def load_configuration(self):
        if not os.path.exists("config.json"):
            default_config = {
                "hotkey": "ctrl",
                "double_press_delay": 0.3,
                "condition_type": "contains",
                "condition_value": "example",
                "enable_domain_replacement": True,
                "replace_domain": {"old": "old.com", "new": "new.com"},
                "suffix": "/suffix",
                "allowed_protocols": ["http", "https"]
            }
            with open("config.json", "w") as config_file:
                json.dump(default_config, config_file, indent=4)
        
        with open("config.json", "r") as config_file:
            self.config = json.load(config_file)

    def setup_trace_source(self):
        self.trace_source = lambda message: logger.info(message)

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("path/to/icon.png"))
        self.tray_icon.setVisible(True)
        menu = QMenu(self)

        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        menu.addAction(show_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.quit_application)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def setup_clipboard_monitor(self):
        self.clipboard_monitor = ClipboardMonitor(self)
        self.clipboard_monitor.clipboard_changed.connect(self.on_clipboard_change)

    def on_clipboard_change(self, content):
        if self.is_valid_and_ready_url(content):
            self.open_url(content)

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def setup_hotkey(self):
        key = self.config["hotkey"].capitalize()
        try:
            keyboard.add_hotkey(f'{key}+c', self.handle_hotkey)
        except ValueError:
            new_hotkey, ok = QInputDialog.getText(self, 'Hotkey Conflict', 'Please enter a new hotkey:')
            if ok and new_hotkey:
                self.config["hotkey"] = new_hotkey.lower()
                keyboard.add_hotkey(f'{new_hotkey}+c', self.handle_hotkey)
            else:
                QMessageBox.warning(self, "Hotkey Registration", "Hotkey not registered due to conflict.")

    def handle_hotkey(self):
        try:
            if pyperclip.paste():
                url = pyperclip.paste()
                self.trace_source(f"URL from clipboard: {url}")
                if self.is_valid_and_ready_url(url):
                    self.open_url(url)
        except pyperclip.PyperclipException as e:
            self.trace_source(f"No text found in clipboard: {str(e)}")

    def is_valid_and_ready_url(self, url):
        decoded_url = urllib.parse.unquote(url)
        result = urllib.parse.urlparse(decoded_url)
        if all([result.scheme, result.netloc]):
            domain = tldextract.extract(result.netloc).domain
            return self.check_domain_conditions(domain, self.config)
        return False

    def check_domain_conditions(self, domain, config):
        allowed_protocols = config.get("allowed_protocols", ["http", "https"])
        if result.scheme not in allowed_protocols:
            return False
        condition_type = config.get("condition_type")
        condition_value = config.get("condition_value", "")
        return {
            "contains": lambda x: condition_value in x,
            "startswith": lambda x: x.startswith(condition_value),
            "endswith": lambda x: x.endswith(condition_value),
        }.get(condition_type, lambda x: False)(domain)

    def open_url(self, url):
        modified_url = self.modify_url(url)
        self.trace_source(f"Opening URL: {modified_url}")
        thread = URLThread(modified_url)
        thread.urlOpened.connect(self.show_url_result)
        thread.start()

    def show_url_result(self, message, url):
        QMessageBox.information(self, "URL Result", f"{message}: {url}")

    def modify_url(self, url):
        if self.config.get("enable_domain_replacement", False):
            old_domain = self.config["replace_domain"].get("old", "")
            new_domain = self.config["replace_domain"].get("new", "")
            url = url.replace(old_domain, new_domain)

        suffix = self.config.get("suffix", "")
        if suffix and not url.endswith(suffix):
            url += suffix

        self.trace_source(f"Modified URL: {url}")
        return url

    def open_settings(self):
        settings_window = SettingsWindow(self.config, self.trace_source)
        settings_window.exec_()
        self.load_configuration()  # Reload config after settings window closes

    def quit_application(self):
        QApplication.instance().quit()

class SettingsWindow(QMainWindow):
    def __init__(self, config, trace_source):
        super().__init__()
        self.config = config
        self.trace_source = trace_source
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Configuration")
        self.setGeometry(100, 100, 600, 500)
        
        layout = QVBoxLayout()
        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.hotkey_edit = QLineEdit()
        self.delay_slider = QSlider(Qt.Horizontal)
        self.condition_combo = QComboBox()
        self.condition_value_edit = QLineEdit()
        self.domain_replacement_check = QPushButton("Toggle Domain Replacement")
        self.old_domain_edit = QLineEdit()
        self.new_domain_edit = QLineEdit()
        self.suffix_edit = QLineEdit()
        self.save_button = QPushButton("Save")

        self.condition_combo.addItems(["contains", "startswith", "endswith"])
        self.domain_replacement_check.clicked.connect(self.toggle_domain_replacement)
        self.save_button.clicked.connect(self.save_configuration)

        layout.addWidget(QLabel("Hotkey:"))
        layout.addWidget(self.hotkey_edit)
        layout.addWidget(QLabel("Double Press Delay (seconds):"))
        layout.addWidget(self.delay_slider)
        layout.addWidget(QLabel("URL Condition:"))
        layout.addWidget(self.condition_combo)
        layout.addWidget(QLabel("Condition Value:"))
        layout.addWidget(self.condition_value_edit)
        layout.addWidget(self.domain_replacement_check)
        layout.addWidget(QLabel("Old Domain:"))
        layout.addWidget(self.old_domain_edit)
        layout.addWidget(QLabel("New Domain:"))
        layout.addWidget(self.new_domain_edit)
        layout.addWidget(QLabel("Suffix:"))
        layout.addWidget(self.suffix_edit)
        layout.addWidget(self.save_button)

        self.load_settings()

    def load_settings(self):
        self.hotkey_edit.setText(self.config.get("hotkey", ""))
        self.delay_slider.setValue(int(self.config.get("double_press_delay", 0.3) * 10))
        self.condition_combo.setCurrentText(self.config.get("condition_type", "contains"))
        self.condition_value_edit.setText(self.config.get("condition_value", ""))
        self.domain_replacement_check.setChecked(self.config.get("enable_domain_replacement", True))
        self.old_domain_edit.setText(self.config["replace_domain"].get("old", ""))
        self.new_domain_edit.setText(self.config["replace_domain"].get("new", ""))
        self.suffix_edit.setText(self.config.get("suffix", ""))
        self.toggle_domain_replacement()  # This will update the UI based on the current setting

    def toggle_domain_replacement(self):
        enabled = self.domain_replacement_check.isChecked()
        self.old_domain_edit.setEnabled(enabled)
        self.new_domain_edit.setEnabled(enabled)

    @retry_with_backoff(max_retries=3, initial_delay=1, max_delay=10)
    def save_configuration(self):
        new_config = {
            "hotkey": self.hotkey_edit.text(),
            "double_press_delay": self.delay_slider.value() / 10.0,
            "condition_type": self.condition_combo.currentText(),
            "condition_value": self.condition_value_edit.text(),
            "enable_domain_replacement": self.domain_replacement_check.isChecked(),
            "replace_domain": {
                "old": self.old_domain_edit.text(),
                "new": self.new_domain_edit.text()
            },
            "suffix": self.suffix_edit.text()
        }
        try:
            with open("config.json", "w") as config_file:
                json.dump(new_config, config_file, indent=4)
            QMessageBox.information(self, "Success", "Settings saved successfully!")
            self.trace_source("Configuration saved successfully.")
            self.close()
        except Exception as e:
            self.trace_source(f"Error saving configuration: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to save settings: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_window = MainWindow()
    sys.exit(app.exec_())