import asyncio
import json
import logging
import threading
import datetime
import wave
import os
import sys
import platform
import time
import signal
import atexit
from pathlib import Path
import numpy as np
import sounddevice as sd
import websockets
import tkinter as tk
from tkinter import messagebox

# PyInstaller compatibility
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    BASE_DIR = Path(sys._MEIPASS)
    APP_DIR = Path(sys.executable).parent
else:
    # Running as script
    BASE_DIR = Path(__file__).parent
    APP_DIR = BASE_DIR

# =====================================================
# PLATFORM DETECTION
# =====================================================
IS_MAC = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# =====================================================
# CONFIG
# =====================================================
SERVER_HOST = "99.49.245.187"
# SERVER_HOST = "localhost"
SERVER_PORT = 8000
WS_URI = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws"
API_BASE = f"http://{SERVER_HOST}:{SERVER_PORT}"

INPUT_SR = 16000
OUTPUT_SR = 24000
BLOCK_SIZE = 480
CHANNELS = 1

# CRITICAL: Output device for AI audio - send to CABLE INPUT so Google Meet can capture from CABLE OUTPUT
OUTPUT_DEVICE_NAME = "BlackHole 2ch" if IS_MAC else "CABLE Input"

TOKEN_FILE = APP_DIR / ".voice_client_token"
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_BASE_DELAY = 1
RECONNECT_MAX_DELAY = 60

RECORDINGS_DIR = APP_DIR / "recordings"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
LOG_FILE = APP_DIR / "voice_client.log"

UI_FONT = "Helvetica" if IS_MAC else "Segoe UI"
UI_FONT_BOLD = "Helvetica-Bold" if IS_MAC else "Segoe UI"
ICON_PATH = BASE_DIR / "app.ico" if (BASE_DIR / "app.ico").exists() else None

# Audio processing settings
VAD_THRESHOLD = 0.001  # Voice activity detection threshold (lowered for sensitivity)
NOISE_GATE_THRESHOLD = 0.0005  # Noise gate threshold (lowered)
ADAPTIVE_BUFFER_MIN = 480
ADAPTIVE_BUFFER_MAX = 1920

# =====================================================
# LOGGING WITH ROTATION
# =====================================================
class RotatingFileHandler(logging.FileHandler):
    def __init__(self, filename, max_bytes, encoding=None):
        self.max_bytes = max_bytes
        os.makedirs(os.path.dirname(filename) if os.path.dirname(filename) else '.', exist_ok=True)
        super().__init__(filename, encoding=encoding)
    
    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename):
                if os.path.getsize(self.baseFilename) > self.max_bytes:
                    self.stream.close()
                    backup = str(self.baseFilename) + ".old"
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.rename(self.baseFilename, backup)
                    self.stream = self._open()
            super().emit(record)
        except Exception:
            pass  # Prevent logging errors from crashing app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(str(LOG_FILE), MAX_LOG_SIZE, encoding='utf-8')
    ]
)
logger = logging.getLogger("voice-client")

# =====================================================
# AUDIO PROCESSING
# =====================================================
class AudioProcessor:
    """Handles noise suppression, VAD, and echo cancellation"""
    
    def __init__(self):
        self.noise_profile = None
        self.frame_count = 0
        
    def apply_noise_gate(self, audio_data: np.ndarray) -> np.ndarray:
        """Simple noise gate to remove low-level noise"""
        audio_float = audio_data.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float ** 2))
        
        if rms < NOISE_GATE_THRESHOLD:
            return np.zeros_like(audio_data)
        return audio_data
    
    def detect_voice_activity(self, audio_data: np.ndarray) -> bool:
        """Detect if audio contains voice"""
        audio_float = audio_data.astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float ** 2))
        return rms > VAD_THRESHOLD
    
    def process_input(self, audio_data: np.ndarray) -> tuple[np.ndarray, bool]:
        """Process input audio with noise gate and VAD"""
        has_voice = self.detect_voice_activity(audio_data)
        if has_voice:
            processed = self.apply_noise_gate(audio_data)
            return processed, True
        return np.zeros_like(audio_data), False

# =====================================================
# ADAPTIVE BUFFER MANAGER
# =====================================================
class AdaptiveBufferManager:
    """Manages buffer size based on network conditions"""
    
    def __init__(self):
        self.current_size = BLOCK_SIZE
        self.latency_samples = []
        self.max_samples = 10
        
    def update_latency(self, latency_ms: float):
        """Update buffer size based on network latency"""
        self.latency_samples.append(latency_ms)
        if len(self.latency_samples) > self.max_samples:
            self.latency_samples.pop(0)
        
        avg_latency = sum(self.latency_samples) / len(self.latency_samples)
        
        if avg_latency > 200:
            self.current_size = min(ADAPTIVE_BUFFER_MAX, self.current_size + 240)
        elif avg_latency < 50:
            self.current_size = max(ADAPTIVE_BUFFER_MIN, self.current_size - 240)
        
        logger.debug(f"Buffer size adjusted to {self.current_size} (latency: {avg_latency:.1f}ms)")
    
    def get_buffer_size(self) -> int:
        return self.current_size

# =====================================================
# DEVICE MONITOR
# =====================================================
class AudioDeviceMonitor:
    """Monitors audio device changes and handles hot-swapping"""
    
    def __init__(self):
        self.last_devices = self._get_device_list()
        self.callbacks = []
        
    def _get_device_list(self) -> list:
        try:
            return [d['name'] for d in sd.query_devices()]
        except:
            return []
    
    def register_callback(self, callback):
        self.callbacks.append(callback)
    
    def check_devices(self):
        """Check if devices have changed"""
        current_devices = self._get_device_list()
        if current_devices != self.last_devices:
            logger.info("Audio device change detected")
            self.last_devices = current_devices
            for callback in self.callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Device callback error: {e}")

# =====================================================
# AUTHENTICATION
# =====================================================
class AuthManager:
    def __init__(self):
        self.token = None
        self.user = None
        self.load_token()

    def load_token(self):
        if not TOKEN_FILE.exists():
            return
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                data = json.load(f)
                self.token = data.get("token")
                self.user = data.get("user")
            logger.info("Loaded saved token")
        except Exception as e:
            logger.warning(f"Failed to load token: {e}")

    def save_token(self, token: str, user: dict):
        self.token = token
        self.user = user
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"token": token, "user": user}, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save token: {e}")

    def clear_token(self):
        self.token = None
        self.user = None
        try:
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
        except Exception as e:
            logger.warning(f"Failed to clear token: {e}")

    def is_authenticated(self) -> bool:
        return bool(self.token)

# =====================================================
# LOGIN WINDOW
# =====================================================
class LoginWindow:
    def __init__(self, auth: AuthManager):
        self.auth = auth
        self.success = False

        self.root = tk.Tk()
        self.root.title("Upthink Voice - Login")
        if ICON_PATH and ICON_PATH.exists():
            try:
                self.root.iconbitmap(ICON_PATH)
            except Exception:
                pass  # Icon loading is optional
        self.root.geometry("360x340")
        self.root.configure(bg="#0f172a")
        self.root.resizable(False, False)

        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (360 // 2)
        y = (self.root.winfo_screenheight() // 2) - (320 // 2)
        self.root.geometry(f"360x340+{x}+{y}")

        tk.Label(self.root, text="Login", font=(UI_FONT_BOLD, 18),
                 bg="#0f172a", fg="#e2e8f0").pack(pady=20)

        tk.Label(self.root, text="Username:", bg="#0f172a", fg="#94a3b8").pack()
        self.username_entry = tk.Entry(self.root, width=28, font=(UI_FONT, 11))
        self.username_entry.pack(pady=5)
        self.username_entry.focus()

        tk.Label(self.root, text="Password:", bg="#0f172a", fg="#94a3b8").pack()
        self.password_entry = tk.Entry(self.root, width=28, font=(UI_FONT, 11), show="•")
        self.password_entry.pack(pady=5)
        self.password_entry.bind("<Return>", lambda e: self.login())

        btn_frame = tk.Frame(self.root, bg="#0f172a")
        btn_frame.pack(pady=20)

        tk.Button(btn_frame, text="Login", command=self.login,
                  bg="#3b82f6", fg="white", font=(UI_FONT_BOLD, 10),
                  width=12, relief="flat").pack(side=tk.LEFT, padx=8)

        tk.Button(btn_frame, text="Exit", command=self.root.quit,
                  bg="#475569", fg="white", font=(UI_FONT, 10),
                  width=12, relief="flat").pack(side=tk.LEFT, padx=8)

        self.status_label = tk.Label(self.root, text="", bg="#0f172a", fg="#f87171",
                                     font=(UI_FONT, 10), wraplength=320)
        self.status_label.pack(pady=10)

    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password:
            self.status_label.config(text="Username and password required", fg="#f87171")
            return

        self.status_label.config(text="Connecting...", fg="#94a3b8")
        self.root.update()

        import urllib.request
        import urllib.error

        try:
            req = urllib.request.Request(
                f"{API_BASE}/auth/login",
                data=json.dumps({"username": username, "password": password}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
                token = data.get("access_token")
                user = data.get("user")
                if not token:
                    raise ValueError("No access_token in response")
                self.auth.save_token(token, user)
                self.success = True
                self.root.destroy()

        except urllib.error.HTTPError as e:
            if e.code == 401:
                msg = "Invalid username or password"
            elif e.code == 429:
                msg = "Too many attempts – try again later"
            else:
                msg = f"Server error ({e.code})"
            self.status_label.config(text=msg, fg="#f87171")
        except Exception as e:
            self.status_label.config(text=f"Login failed: {str(e)}", fg="#f87171")
            logger.exception("Login failed")

    def run(self) -> bool:
        self.root.mainloop()
        return self.success

# =====================================================
# FLOATING STATUS WINDOW
# =====================================================
class VoiceUI:
    def __init__(self, auth: AuthManager):
        self.auth = auth
        self.root = tk.Tk()
        self.root.title("Upthink Voice")
        if ICON_PATH and ICON_PATH.exists():
            try:
                self.root.iconbitmap(ICON_PATH)
            except Exception:
                pass  # Icon loading is optional
        self.root.attributes("-topmost", True)
        self.root.geometry("300x250+400+300")
        self.root.configure(bg="#18181b")

        if IS_WINDOWS:
            self.root.after(50, self._windows_fix_taskbar)

        # Status dot
        dot_frame = tk.Frame(self.root, bg="#18181b")
        dot_frame.pack(pady=5)
        
        self.dot_canvas = tk.Canvas(dot_frame, width=20, height=20, bg="#18181b", highlightthickness=0)
        self.dot_canvas.pack()
        self.status_dot = self.dot_canvas.create_oval(3, 3, 17, 17, fill="#ef4444", outline="")

        # Text frame with custom scrollbar
        text_frame = tk.Frame(self.root, bg="#18181b")
        text_frame.pack(padx=8, pady=5, fill=tk.BOTH, expand=True)
        
        self.text_area = tk.Text(
            text_frame, wrap=tk.WORD, width=32, height=12,
            bg="#0f172a", fg="#e2e8f0", font=(UI_FONT, 10),
            relief="flat", borderwidth=1
        )
        
        scrollbar = tk.Scrollbar(text_frame, command=self.text_area.yview, width=10)
        self.text_area.config(yscrollcommand=scrollbar.set)
        
        self.text_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.text_area.config(state=tk.DISABLED)

        self.connected = False
        self.speaking = False
        self.speak_timer = None

    def _windows_fix_taskbar(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style & ~0x00000080) | 0x00040000
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
            self.root.withdraw()
            self.root.deiconify()
        except:
            pass

    def set_connected(self, connected: bool):
        self.connected = connected
        if not connected:
            self.speaking = False
            if self.speak_timer:
                self.root.after_cancel(self.speak_timer)
                self.speak_timer = None
            self.dot_canvas.itemconfig(self.status_dot, fill="#ef4444")
        else:
            color = "#60a5fa" if self.speaking else "#22c55e"
            self.dot_canvas.itemconfig(self.status_dot, fill=color)

    def indicate_speaking(self):
        if not self.connected:
            return
        self.speaking = True
        self.dot_canvas.itemconfig(self.status_dot, fill="#60a5fa")
        if self.speak_timer:
            self.root.after_cancel(self.speak_timer)
        self.speak_timer = self.root.after(800, self._stop_speaking_indicator)

    def _stop_speaking_indicator(self):
        self.speak_timer = None
        self.speaking = False
        if self.connected:
            self.dot_canvas.itemconfig(self.status_dot, fill="#22c55e")

    def set_status(self, text: str, color: str = "#e2e8f0"):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        self.text_area.insert(tk.END, text)
        self.text_area.tag_add("color", "1.0", tk.END)
        self.text_area.tag_config("color", foreground=color)
        self.text_area.config(state=tk.DISABLED)
        self.text_area.see(tk.END)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def on_close(self):
        logger.info("User closed floating window → shutting down")
        try:
            sd.stop()
        except Exception as e:
            logger.error(f"Error stopping audio: {e}")
        
        try:
            self.root.quit()
            self.root.destroy()
        except Exception as e:
            logger.error(f"Error closing window: {e}")
        
        # Graceful shutdown
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)

# =====================================================
# AUDIO SENDER (Microphone → WebSocket)
# =====================================================
class AudioSender:
    def __init__(self, ws, loop: asyncio.AbstractEventLoop):
        self.ws = ws
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=25)
        self.running = True
        self.stream = None
        self.processor = AudioProcessor()
        self.buffer_manager = AdaptiveBufferManager()
        self.last_send_time = None

    def callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"mic status: {status}")
        
        try:
            # ALWAYS send all audio - no filtering
            self.loop.call_soon_threadsafe(self._put_nowait, indata.tobytes())
        except Exception as e:
            logger.error(f"Audio processing error: {e}")

    def _put_nowait(self, data: bytes):
        if not self.running:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.queues.QueueFull:
            pass

    async def send_loop(self):
        while self.running:
            try:
                chunk = await asyncio.wait_for(self.queue.get(), timeout=1.5)
                
                send_start = asyncio.get_event_loop().time()
                await self.ws.send(chunk)
                send_duration = (asyncio.get_event_loop().time() - send_start) * 1000
                
                # Update buffer based on send latency
                self.buffer_manager.update_latency(send_duration)
                
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"sender error: {e}")
                break

    def start(self):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # CRITICAL: Use default input device (microphone)
                # This ensures we capture from mic, not from system audio
                self.stream = sd.InputStream(
                    device=None,  # Use default input device
                    samplerate=INPUT_SR,
                    blocksize=self.buffer_manager.get_buffer_size(),
                    channels=CHANNELS,
                    dtype='int16',
                    latency="low",
                    callback=self.callback
                )
                self.stream.start()
                logger.info(f"Microphone input stream started (device: default)")
                return
            except Exception as e:
                logger.error(f"Cannot open microphone (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                else:
                    raise

    def stop(self):
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
                logger.info("Microphone stream closed")
            except:
                pass
            self.stream = None

# =====================================================
# MAIN CONNECTION LOGIC
# =====================================================
async def connect_and_run(ui: VoiceUI, auth: AuthManager):
    audio_out = None
    sender = None
    send_task = None
    wav_file = None
    device_monitor = AudioDeviceMonitor()
    reconnect_attempt = 0

    def on_device_change():
        """Handle audio device hot-swap"""
        nonlocal audio_out, sender
        logger.info("Attempting to reinitialize audio devices...")
        # Devices will be reinitialized on next connection

    device_monitor.register_callback(on_device_change)

    while reconnect_attempt < MAX_RECONNECT_ATTEMPTS:
        try:
            delay = min(RECONNECT_BASE_DELAY * (2 ** reconnect_attempt), RECONNECT_MAX_DELAY)
            if reconnect_attempt > 0:
                ui.set_status(f"Reconnecting in {delay}s... (attempt {reconnect_attempt + 1}/{MAX_RECONNECT_ATTEMPTS})", "#fbbf24")
                await asyncio.sleep(delay)
            
            ui.set_status("Connecting...", "#fbbf24")
            async with websockets.connect(
                WS_URI,
                ping_interval=20,
                ping_timeout=15,
                max_size=8_000_000,
            ) as ws:

                logger.info("WebSocket connected")
                ui.set_status("Authenticating...", "#94a3b8")

                await ws.send(json.dumps({"type": "auth", "token": auth.token}))

                try:
                    auth_resp = await asyncio.wait_for(ws.recv(), 10)
                    data = json.loads(auth_resp)
                    if data.get("type") == "error":
                        ui.set_status("Auth failed – re-login required", "#f87171")
                        auth.clear_token()
                        return
                    logger.info("Authenticated")
                except asyncio.TimeoutError:
                    ui.set_status("Authentication timeout", "#f87171")
                    raise

                # Reset reconnect counter on successful connection
                reconnect_attempt = 0
                
                ui.set_connected(True)
                ui.set_status("Connected – listening", "#22c55e")

                # ── Output audio ONLY to virtual cable (NOT to PC speakers) ──────────────
                virtual_cable_idx = None
                audio_out_cable = None
                
                try:
                    devs = sd.query_devices()
                    for i, d in enumerate(devs):
                        if OUTPUT_DEVICE_NAME.lower() in d["name"].lower() and d["max_output_channels"] > 0:
                            virtual_cable_idx = i
                            logger.info(f"Found virtual cable: {d['name']} (index: {i})")
                            break
                    
                    if virtual_cable_idx is None:
                        raise ValueError(f"Virtual cable '{OUTPUT_DEVICE_NAME}' not found! Please install VB-Audio Virtual Cable.")
                
                except Exception as e:
                    logger.error(f"Output device error: {e}")
                    ui.set_status(f"Error: Virtual cable not found", "#f87171")
                    await asyncio.sleep(5)
                    continue

                # Open ONLY virtual cable output (NO speakers) with retry
                for retry in range(3):
                    try:
                        audio_out_cable = sd.RawOutputStream(
                            samplerate=OUTPUT_SR,
                            device=virtual_cable_idx,
                            channels=CHANNELS,
                            dtype="int16",
                            latency="low"
                        )
                        audio_out_cable.start()
                        logger.info("Audio output ONLY to virtual cable (NOT to PC speakers)")
                        break
                    except Exception as e:
                        logger.error(f"Failed to open output device (attempt {retry+1}/3): {e}")
                        if retry < 2:
                            await asyncio.sleep(0.5)
                        else:
                            ui.set_status(f"Error: Cannot open audio output", "#f87171")
                            await asyncio.sleep(5)
                            continue

                # ── Microphone input ───────────────────────────────────
                sender = AudioSender(ws, asyncio.get_running_loop())
                sender.start()
                send_task = asyncio.create_task(sender.send_loop())

                # ── Recording (received audio) ─────────────────────────
                RECORDINGS_DIR.mkdir(exist_ok=True)
                now = datetime.datetime.now()
                day_dir = RECORDINGS_DIR / now.strftime("%Y-%m-%d")
                day_dir.mkdir(exist_ok=True)
                fname = day_dir / f"voice_{now.strftime('%H%M%S')}.wav"

                wav_file = wave.open(str(fname), "wb")
                wav_file.setnchannels(CHANNELS)
                wav_file.setsampwidth(2)
                wav_file.setframerate(OUTPUT_SR)
                logger.info(f"Recording → {fname}")

                # Monitor device changes
                device_check_interval = 5
                last_device_check = asyncio.get_event_loop().time()

                async for msg in ws:
                    # Periodic device check
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_device_check > device_check_interval:
                        device_monitor.check_devices()
                        last_device_check = current_time

                    if isinstance(msg, bytes):
                        ui.indicate_speaking()
                        # Write ONLY to virtual cable (NOT to speakers)
                        try:
                            if audio_out_cable:
                                audio_out_cable.write(msg)
                        except Exception as e:
                            logger.error(f"Audio output error: {e}")
                        
                        try:
                            if wav_file:
                                wav_file.writeframes(msg)
                        except Exception as e:
                            logger.error(f"Recording error: {e}")
                    elif isinstance(msg, str):
                        try:
                            data = json.loads(msg)
                            if data.get("type") == "text" and "content" in data:
                                ui.set_status(data["content"])
                        except:
                            pass

        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by server")
            ui.set_status("Disconnected by server", "#f87171")
            reconnect_attempt += 1
        except Exception as e:
            logger.exception("Connection failure")
            ui.set_status(f"Error: {str(e)[:70]}", "#f87171")
            reconnect_attempt += 1

        finally:
            # Cleanup in proper order
            if wav_file:
                try:
                    wav_file.close()
                    logger.info("Recording file closed")
                except Exception as e:
                    logger.error(f"Error closing recording: {e}")
            
            if sender:
                try:
                    sender.stop()
                except Exception as e:
                    logger.error(f"Error stopping sender: {e}")
            
            if send_task:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error canceling send task: {e}")
            
            if audio_out:
                try:
                    audio_out.stop()
                    audio_out.close()
                except Exception as e:
                    logger.error(f"Error closing audio_out: {e}")
            
            if 'audio_out_cable' in locals() and audio_out_cable:
                try:
                    audio_out_cable.stop()
                    audio_out_cable.close()
                    logger.info("Audio output closed")
                except Exception as e:
                    logger.error(f"Error closing audio output: {e}")

            ui.set_connected(False)
            ui.set_status("Disconnected", "#ef4444")

    # Max reconnect attempts reached
    ui.set_status(f"Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts", "#f87171")
    logger.error("Max reconnect attempts reached")

# =====================================================
# CLEANUP HANDLER
# =====================================================
def cleanup_on_exit():
    """Ensure clean shutdown"""
    try:
        sd.stop()
        logger.info("Audio devices stopped")
    except Exception:
        pass

atexit.register(cleanup_on_exit)

# =====================================================
# ENTRY POINT
# =====================================================
def main():
    # Ensure directories exist
    try:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        APP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        logger.info("Interrupt received, shutting down...")
        cleanup_on_exit()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    auth = AuthManager()

    if not auth.is_authenticated():
        login_win = LoginWindow(auth)
        if not login_win.run() or not auth.is_authenticated():
            return

    ui = VoiceUI(auth)

    def run_async_loop():
        try:
            if IS_WINDOWS:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(connect_and_run(ui, auth))
        except Exception as e:
            logger.exception("Main async loop crashed")
            try:
                ui.root.after(0, lambda: messagebox.showerror("Fatal Error", str(e)))
            except Exception:
                pass

    threading.Thread(target=run_async_loop, daemon=True).start()
    
    try:
        ui.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    except Exception as e:
        logger.exception("UI crashed")
    finally:
        cleanup_on_exit()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error in main")
        sys.exit(1)