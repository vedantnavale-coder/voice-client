import time
import subprocess
import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ReloadHandler(FileSystemEventHandler):
    def __init__(self, script):
        self.script = script
        self.process = None
        self.start_process()
    
    def start_process(self):
        if self.process:
            self.process.terminate()
        self.process = subprocess.Popen([sys.executable, self.script])
    
    def on_modified(self, event):
        if event.src_path.endswith('.py'):
            print(f"\n🔄 Change detected in {event.src_path}")
            self.start_process()

if __name__ == "__main__":
    handler = ReloadHandler("client.py")
    observer = Observer()
    observer.schedule(handler, path=".", recursive=True)
    observer.start()
    print("👀 Watching for changes... Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


# =====================================================
# Packaging suggestion (run these in terminal):
# =====================================================
# pip install pyinstaller
# pyinstaller --onefile --noconsole --icon=app.ico --name "VoiceClient" client.py
#   → creates dist/VoiceClient.exe (Windows) or equivalent