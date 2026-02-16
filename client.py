import asyncio
import json
import logging
import threading
import time
import datetime
import wave
import os
import sys
from pathlib import Path
import numpy as np
import sounddevice as sd
import websockets
import tkinter as tk
from tkinter import messagebox

# =====================================================
# CONFIG
# =====================================================
# SERVER_HOST = "localhost"  # change to your server's IP or hostname
SERVER_HOST = "99.49.245.187"
SERVER_PORT = 8000
WS_URI = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws"
API_BASE = f"http://{SERVER_HOST}:{SERVER_PORT}"
INPUT_SR = 16000
OUTPUT_SR = 24000
BLOCK_SIZE = 480
CHANNELS = 1
VB_CABLE_NAME = "CABLE Input"
TOKEN_FILE = ".voice_client_token"
RECONNECT_DELAYS = [1, 2, 4, 4, 4,4,4,4]  # seconds – exponential backoff
RECORDINGS_DIR = Path("recordings")


# =====================================================
# LOGGING
# =====================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("voice-client")

# =====================================================
# AUTH MANAGER (unchanged)
# =====================================================
class AuthManager:
    def __init__(self):
        self.token = None
        self.user = None
        self.load_token()

    def load_token(self):
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, 'r') as f:
                    data = json.load(f)
                    self.token = data.get('token')
                    self.user = data.get('user')
                    logger.info("Loaded saved credentials")
        except Exception as e:
            logger.warning(f"Could not load token: {e}")

    def save_token(self, token, user):
        self.token = token
        self.user = user
        try:
            with open(TOKEN_FILE, 'w') as f:
                json.dump({'token': token, 'user': user}, f)
        except Exception as e:
            logger.warning(f"Could not save token: {e}")

    def clear_token(self):
        self.token = None
        self.user = None
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)

    def is_authenticated(self):
        return self.token is not None

# =====================================================
# LOGIN UI (improved error messages)
# =====================================================
class LoginWindow:
    def __init__(self, auth_manager: AuthManager):
        self.auth = auth_manager
        self.result = False

        self.root = tk.Tk()
        self.root.title("Upthink Voice - Login")
        self.root.geometry("360x340")
        self.root.configure(bg="#0f172a")
        self.root.resizable(False, False)

        self.root.eval('tk::PlaceWindow . center')

        tk.Label(self.root, text="Login", font=("Segoe UI", 18, "bold"),
                 bg="#0f172a", fg="#e2e8f0").pack(pady=20)

        tk.Label(self.root, text="Username:", bg="#0f172a", fg="#94a3b8").pack()
        self.username_entry = tk.Entry(self.root, width=28, font=("Segoe UI", 11))
        self.username_entry.pack(pady=5)
        self.username_entry.focus()

        tk.Label(self.root, text="Password:", bg="#0f172a", fg="#94a3b8").pack()
        self.password_entry = tk.Entry(self.root, width=28, font=("Segoe UI", 11), show="•")
        self.password_entry.pack(pady=5)
        self.password_entry.bind('<Return>', lambda e: self.login())

        btn_frame = tk.Frame(self.root, bg="#0f172a")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="Login", command=self.login,
                  bg="#3b82f6", fg="white", font=("Segoe UI", 10, "bold"),
                  width=12, relief="flat").pack(side=tk.LEFT, padx=8)

        tk.Button(btn_frame, text="Exit", command=self.root.quit,
                  bg="#475569", fg="white", font=("Segoe UI", 10),
                  width=12, relief="flat").pack(side=tk.LEFT, padx=8)

        self.status_label = tk.Label(self.root, text="", bg="#0f172a", fg="#f87171",
                                     font=("Segoe UI", 10), wraplength=320)
        self.status_label.pack(pady=10)

    def login(self):
        import urllib.request
        import urllib.error

        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.status_label.config(text="Username and password are required", fg="#f87171")
            return

        self.status_label.config(text="Connecting to server...", fg="#94a3b8")

        try:
            req = urllib.request.Request(
                f"{API_BASE}/auth/login",
                data=json.dumps({"username": username, "password": password}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
                self.auth.save_token(data.get('access_token'), data.get('user'))
                self.result = True
                self.root.destroy()

        except urllib.error.HTTPError as e:
            if e.code == 401:
                msg = "Invalid username or password"
            elif e.code == 429:
                msg = "Too many login attempts – try again later"
            else:
                msg = f"Server error ({e.code})"
            self.status_label.config(text=msg, fg="#f87171")
        except urllib.error.URLError as e:
            self.status_label.config(text=f"Cannot reach server: {str(e.reason)}", fg="#f87171")
        except Exception as e:
            self.status_label.config(text=f"Login failed: {str(e)[:60]}", fg="#f87171")
            logger.exception("Login exception")

    def run(self):
        self.root.mainloop()
        return self.result

# =====================================================
# MAIN FLOATING UI – better status messages, added minimize button
# =====================================================
class VoiceUI:
    def __init__(self, auth_manager: AuthManager):
        self.auth = auth_manager
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.geometry("520x70+400+300")
        self.root.configure(bg="#000000")

        self.canvas = tk.Canvas(self.root, width=520, height=70, bg="#000000", highlightthickness=0)
        self.canvas.pack()

        self._create_rounded_rect(8, 8, 504, 54, radius=26, fill="#18181b", outline="#334155", width=2)

        self.status_dot = self.canvas.create_oval(24, 24, 46, 46, fill="#ef4444", outline="")
        self.text_id = self.canvas.create_text(260, 35, text="Starting...", fill="#e2e8f0",
                                               font=("Segoe UI", 13), anchor="center", width=440)

        self.min_btn = self.canvas.create_text(460, 35, text="─", fill="#94a3b8",
                                               font=("Arial", 26, "bold"), anchor="center", tags="min")
        self.canvas.tag_bind("min", "<Button-1>", lambda e: self.root.withdraw())
        self.canvas.tag_bind("min", "<Enter>", lambda e: self.canvas.itemconfig("min", fill="#ffffff"))
        self.canvas.tag_bind("min", "<Leave>", lambda e: self.canvas.itemconfig("min", fill="#94a3b8"))

        self.close_btn = self.canvas.create_text(490, 35, text="×", fill="#94a3b8",
                                                 font=("Arial", 26, "bold"), anchor="center", tags="close")
        self.canvas.tag_bind("close", "<Button-1>", lambda e: self.root.destroy())
        self.canvas.tag_bind("close", "<Enter>", lambda e: self.canvas.itemconfig("close", fill="#f87171"))
        self.canvas.tag_bind("close", "<Leave>", lambda e: self.canvas.itemconfig("close", fill="#94a3b8"))

        # Dragging
        self.canvas.bind("<Button-1>", self.start_move)
        self.canvas.bind("<B1-Motion>", self.do_move)
        for tag in [self.text_id, self.status_dot]:
            self.canvas.tag_bind(tag, "<Button-1>", self.start_move)
            self.canvas.tag_bind(tag, "<B1-Motion>", self.do_move)

        self._offset_x = self._offset_y = 0
        self.connected = False
        self.speaking = False
        self.speak_timer = None

    def _create_rounded_rect(self, x, y, w, h, radius, **kwargs):
        points = [
            x+radius, y, x+w-radius, y, x+w, y, x+w, y+radius,
            x+w, y+h-radius, x+w, y+h, x+w-radius, y+h, x+radius, y+h,
            x, y+h, x, y+radius, x, y,
        ]
        self.canvas.create_polygon(points, smooth=True, **kwargs)

    def start_move(self, event):
        current_tags = self.canvas.gettags("current")
        if "close" in current_tags or "min" in current_tags:
            return
        self._offset_x = event.x
        self._offset_y = event.y

    def do_move(self, event):
        x = self.root.winfo_x() + (event.x - self._offset_x)
        y = self.root.winfo_y() + (event.y - self._offset_y)
        self.root.geometry(f"+{x}+{y}")

    def set_status(self, text: str, color: str = "#e2e8f0"):
        self.canvas.itemconfig(self.text_id, text=text, fill=color)

    def set_connected(self, connected: bool):
        self.connected = connected
        if not connected:
            self.speaking = False
            if self.speak_timer:
                self.root.after_cancel(self.speak_timer)
            self.canvas.itemconfig(self.status_dot, fill="#ef4444")
        else:
            self.canvas.itemconfig(self.status_dot, fill="#22c55e" if not self.speaking else "#60a5fa")

    def indicate_speaking(self):
        if not self.connected:
            return
        self.canvas.itemconfig(self.status_dot, fill="#60a5fa")
        self.speaking = True
        if self.speak_timer:
            self.root.after_cancel(self.speak_timer)
        self.speak_timer = self.root.after(700, self._stop_speaking)

    def _stop_speaking(self):
        self.speak_timer = None
        self.speaking = False
        if self.connected:
            self.canvas.itemconfig(self.status_dot, fill="#22c55e")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        self.root.destroy()
        os._exit(0)

# =====================================================
# AUDIO (improved with buffering)
# =====================================================
def find_output_device(name_part):
    for i, dev in enumerate(sd.query_devices()):
        if name_part.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
            return i
    logger.warning("VB-Cable not found – using default output")
    return sd.default.device[1]  # fallback to system default output

class AudioOutput:
    def __init__(self):
        device = find_output_device(VB_CABLE_NAME)
        self.stream = sd.RawOutputStream(
            samplerate=OUTPUT_SR, device=device, channels=CHANNELS,
            dtype="int16", latency="low"
        )

    def start(self): self.stream.start()
    def play(self, data): self.stream.write(data)
    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

class AudioSender:
    def __init__(self, ws, loop):
        self.ws = ws
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=10)
        self.stream = None

    def _enqueue(self, data):
        if self.queue.full():
            return  # drop if full
        self.queue.put_nowait(data)

    def callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Input status: {status}")
        data = indata.tobytes()
        self.loop.call_soon_threadsafe(self._enqueue, data)

    async def send_loop(self):
        while True:
            data = await self.queue.get()
            try:
                await self.ws.send(data)
            finally:
                self.queue.task_done()

    def start(self):
        self.stream = sd.InputStream(
            samplerate=INPUT_SR, blocksize=BLOCK_SIZE, channels=CHANNELS,
            dtype='int16', callback=self.callback, latency="low"
        )
        self.stream.start()

    def stop(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
                self.stream = None
        except Exception:
            pass

# =====================================================
# NETWORK – Auto-reconnect + better error handling
# =====================================================
async def connect_and_run(ui: VoiceUI, auth: AuthManager):
    audio_out = None
    sender = None
    send_task = None
    wav_file = None
    flush_counter = 0
    FLUSH_INTERVAL = 10  # flush every 10 chunks

    while True:
        try:
            ui.root.after(0, ui.set_status, "Connecting to server...", "#fbbf24")
            async with websockets.connect(WS_URI, ping_interval=20, ping_timeout=15) as ws:
                logger.info("WebSocket connected")
                ui.root.after(0, ui.set_status, "Authenticating...", "#94a3b8")
                await ws.send(json.dumps({"type": "auth", "token": auth.token}))

                try:
                    auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    data = json.loads(auth_resp)
                    if data.get("type") == "error":
                        ui.root.after(0, ui.set_status, "Auth failed – check credentials", "#f87171")
                        return  # fatal – probably bad token
                    logger.info("Authenticated successfully")
                except asyncio.TimeoutError:
                    ui.root.after(0, ui.set_status, "Authentication timeout", "#f87171")
                    raise

                ui.root.after(0, ui.set_connected, True)
                ui.root.after(0, ui.set_status, "Connected – Listening...", "#22c55e")

                audio_out = AudioOutput()
                audio_out.start()

                sender = AudioSender(ws, asyncio.get_running_loop())
                sender.start()
                send_task = asyncio.get_running_loop().create_task(sender.send_loop())

                # Centralized recording
                now = datetime.datetime.now()
                day_folder = RECORDINGS_DIR / now.strftime("%Y-%m-%d")
                day_folder.mkdir(parents=True, exist_ok=True)
                filename = day_folder / f"received_{now.strftime('%H-%M-%S')}.wav"

                wav_file = wave.open(str(filename), "wb")
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(OUTPUT_SR)
                logger.info(f"Started recording to {filename}")

                async for message in ws:
                    if isinstance(message, bytes):
                        ui.root.after(0, ui.indicate_speaking)
                        audio_out.play(message)
                        if wav_file:
                            wav_file.writeframes(message)
                            flush_counter += 1
                            if flush_counter >= FLUSH_INTERVAL:
                                try:
                                    wav_file._file.flush()
                                    os.fsync(wav_file._file.fileno())
                                except Exception as e:
                                    logger.warning(f"Flush failed: {e}")
                                flush_counter = 0
                    else:
                        try:
                            data = json.loads(message)
                            if data.get("type") == "text":
                                ui.root.after(0, ui.set_status, data["content"])
                        except:
                            pass

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket disconnected by server")
            ui.root.after(0, ui.set_status, "Connection closed by server", "#f87171")
        except Exception as e:
            logger.exception("Connection error")
            ui.root.after(0, ui.set_status, f"Error: {str(e)[:60]}", "#f87171")

        finally:
            if wav_file:
                try:
                    wav_file.close()
                    logger.info("Stopped recording")
                except Exception:
                    logger.warning("Error closing WAV file")
                wav_file = None
            if sender:
                sender.stop()
            if send_task:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
            if audio_out:
                audio_out.stop()

            ui.root.after(0, ui.set_connected, False)
            ui.root.after(0, ui.set_status, "Disconnected", "#ef4444")

        # Safer reconnect logic
        for i, delay in enumerate(RECONNECT_DELAYS):
            if not await asyncio.to_thread(ui.root.winfo_exists):
                logger.info("UI window closed – stopping reconnect attempts")
                return
            ui.root.after(0, ui.set_status, f"Reconnecting in {delay}s ({i+1}/{len(RECONNECT_DELAYS)})...", "#fbbf24")
            await asyncio.sleep(delay)

async def main_async(ui: VoiceUI, auth: AuthManager):
    task = asyncio.create_task(connect_and_run(ui, auth))

    def on_window_close():
        task.cancel()
        ui.root.quit()

    ui.root.protocol("WM_DELETE_WINDOW", on_window_close)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Client shutdown requested")
    except Exception as e:
        logger.exception("Fatal error in main loop")

# =====================================================
# MAIN ENTRY
# =====================================================
def main():
    RECORDINGS_DIR.mkdir(exist_ok=True)
    auth = AuthManager()

    if not auth.is_authenticated():
        login = LoginWindow(auth)
        if not login.run() or not auth.is_authenticated():
            return

    ui = VoiceUI(auth)

    # Start asyncio in background thread
    def run_async():
        try:
            asyncio.run(main_async(ui, auth))
        except Exception as e:
            logger.exception("Async loop crashed")
            ui.root.after(0, lambda: messagebox.showerror("Fatal Error", str(e)))

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()

    ui.run()

if __name__ == "__main__":
    main()

# =====================================================
# Packaging suggestion (run these in terminal):
# =====================================================
# pip install pyinstaller
# pyinstaller --onefile --noconsole --icon=app.ico --name "VoiceClient" client.py
#   → creates dist/VoiceClient.exe (Windows) or equivalent