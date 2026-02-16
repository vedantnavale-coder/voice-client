import pyaudio
import numpy as np
from faster_whisper import WhisperModel
import tkinter as tk
from tkinter import filedialog, messagebox, Canvas, Frame, Text
import threading
import queue
from datetime import datetime
import re
import os

# ────────────────────────────────────────────────
#  Config
# ────────────────────────────────────────────────
CABLE_OUTPUT_INDEX = 3
MODEL_SIZE         = "base.en"
COMPUTE_TYPE       = "int8"
RATE               = 16000
CHUNK              = 1024
BUFFER_DURATION    = 2.0
OVERLAP_DURATION   = 0.5
NOISE_GATE_THRESH  = 0.005
MAX_TRANSCRIPT_LINES = 500

# Auto-save folder
TRANSCRIPT_FOLDER = os.path.join(os.path.dirname(__file__), "transcripts")

# Refined dark palette
BG       = "#0f0f0f"
CARD     = "#1a1a1a"
TEXT     = "#e8e8e8"
SUBTEXT  = "#888888"
ACCENT   = "#5c8aff"
ACCENT_D = "#4a6fd9"
DANGER   = "#ff5f57"
DANGER_H = "#ff7b73"
SUCCESS  = "#28c840"
SCROLL   = "#2d2d2d"
SCROLL_H = "#3d3d3d"
BORDER   = "#2a2a2a"

class WhisperTranscriber:
    def __init__(self):
        print(f"Loading {MODEL_SIZE} ({COMPUTE_TYPE})...")
        self.model = WhisperModel(
            MODEL_SIZE,
            device="cpu",
            compute_type=COMPUTE_TYPE,
            cpu_threads=4,
            num_workers=1
        )
        print("Model loaded.")

    def transcribe(self, audio_np):
        if len(audio_np) == 0:
            return ""
            
        # Normalize audio
        audio_np = audio_np.astype(np.float32)
        max_val = np.abs(audio_np).max()
        if max_val > 0:
            audio_np = audio_np / max_val * 0.95
        
        # Noise gate
        energy = np.sqrt(np.mean(audio_np ** 2))
        if energy < NOISE_GATE_THRESH:
            return ""
        
        try:
            segments, info = self.model.transcribe(
                audio_np,
                language="en",
                beam_size=5,
                best_of=5,
                temperature=0.0,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                    threshold=0.5
                ),
                condition_on_previous_text=True,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4
            )
            text = " ".join(s.text.strip() for s in segments if s.text.strip())
            # Clean up
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        except Exception as e:
            print(f"Transcription error: {e}")
            return ""


class ModernCaptionApp:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.geometry("800x450+200+150")
        self.root.configure(bg=BG)
        self.root.attributes('-alpha', 0.0)
        
        self._setup_window_effects()
        
        self.root.deiconify()
        self.root.update()
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.root.focus_force()
        
        self._fade_in()

        self.running = True
        self.transcript_lines = []  # Permanent storage
        self.current_partial = ""    # Current in-progress transcription
        self.msg_queue = queue.Queue(maxsize=100)
        self._drag_data = {"x": 0, "y": 0, "dragging": False}
        self._resize_data = {"edge": None, "x": 0, "y": 0}
        self.min_width = 400
        self.min_height = 250
        
        # Auto-save setup
        self._setup_autosave()
        self.session_start = datetime.now()
        self.current_file = None

        self._build_ui()
        self._setup_bindings()
        
        threading.Thread(target=self._audio_loop, daemon=True).start()
        self.root.after(50, self._process_queue)
    
    def _setup_autosave(self):
        """Create transcripts folder if it doesn't exist"""
        try:
            os.makedirs(TRANSCRIPT_FOLDER, exist_ok=True)
            print(f"Auto-save folder: {TRANSCRIPT_FOLDER}")
        except Exception as e:
            print(f"Failed to create transcripts folder: {e}")
    
    def _autosave(self):
        """Auto-save transcript to file"""
        if not self.transcript_lines:
            return
        
        try:
            # Create filename if not exists
            if not self.current_file:
                timestamp = self.session_start.strftime("%Y%m%d_%H%M%S")
                self.current_file = os.path.join(TRANSCRIPT_FOLDER, f"transcript_{timestamp}.txt")
            
            # Write all lines to file
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write("\n".join(self.transcript_lines))
        except Exception as e:
            print(f"Auto-save failed: {e}")
        
    def _setup_window_effects(self):
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = wintypes.HWND(int(self.root.frame(), 16))
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_CORNER_PREFERENCE = 33
            dwm = ctypes.windll.dwmapi
            dark_mode = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 
                                      ctypes.byref(dark_mode), ctypes.sizeof(dark_mode))
            corner_pref = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_CORNER_PREFERENCE,
                                      ctypes.byref(corner_pref), ctypes.sizeof(corner_pref))
        except:
            pass
        try:
            self.root.attributes('-transparentcolor', BG)
        except:
            pass

    def _fade_in(self):
        alpha = self.root.attributes('-alpha')
        if alpha < 0.95:
            self.root.attributes('-alpha', alpha + 0.1)
            self.root.after(20, self._fade_in)
        else:
            self.root.attributes('-alpha', 0.98)
            self.root.after(100, lambda: self.root.attributes('-topmost', False))

    def _build_ui(self):
        self.container = Frame(self.root, bg=BORDER, bd=1)
        self.container.pack(fill="both", expand=True, padx=1, pady=1)
        
        inner = Frame(self.container, bg=CARD)
        inner.pack(fill="both", expand=True)
        
        # Title bar
        title_bar = Frame(inner, bg=CARD, height=40)
        title_bar.pack(fill="x", padx=0, pady=0)
        title_bar.pack_propagate(False)
        
        drag_area = Frame(title_bar, bg=CARD, cursor="fleur")
        drag_area.pack(side="left", fill="y", expand=True)
        drag_area.bind("<Button-1>", self._start_drag)
        drag_area.bind("<B1-Motion>", self._on_drag)
        
        icon_canvas = Canvas(drag_area, width=20, height=20, bg=CARD, highlightthickness=0)
        icon_canvas.pack(side="left", padx=(16, 8), pady=10)
        self.pulse_id = icon_canvas.create_oval(2, 2, 18, 18, fill=ACCENT, outline="")
        self._pulse_animation(icon_canvas)
        
        title_label = tk.Label(drag_area, text="Live Transcription", font=("Segoe UI", 11, "bold"),
                              fg=SUBTEXT, bg=CARD)
        title_label.pack(side="left", padx=4)
        title_label.bind("<Button-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._on_drag)
        
        controls = Frame(title_bar, bg=CARD)
        controls.pack(side="right", padx=8)
        
        min_btn = self._create_window_button(controls, "−", self._minimize)
        min_btn.pack(side="left", padx=2)
        
        close_btn = self._create_window_button(controls, "×", self.quit, hover_color=DANGER_H, 
                                              default_color=DANGER)
        close_btn.pack(side="left", padx=2)
        
        # Content
        content = Frame(inner, bg=CARD)
        content.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        
        # Text area
        text_container = Frame(content, bg=CARD)
        text_container.pack(fill="both", expand=True)
        
        self.text_area = Text(
            text_container,
            wrap=tk.WORD,
            font=("Segoe UI", 13, "normal"),
            bg=CARD,
            fg=TEXT,
            insertbackground=ACCENT,
            selectbackground="#3a4a6a",
            selectforeground="white",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=16,
            pady=12,
            spacing1=0,
            spacing2=2,
            spacing3=4,
            state="disabled"  # Start disabled to prevent user editing
        )
        self.text_area.pack(side="left", fill="both", expand=True)
        
        # Scrollbar
        scrollbar_frame = Frame(text_container, bg=CARD, width=12)
        scrollbar_frame.pack(side="right", fill="y", padx=(8, 0))
        scrollbar_frame.pack_propagate(False)
        
        self.scrollbar_canvas = Canvas(scrollbar_frame, bg=CARD, highlightthickness=0, 
                                       width=12, bd=0)
        self.scrollbar_canvas.pack(fill="both", expand=True)
        
        self.scrollbar_canvas.create_rectangle(4, 0, 8, 1000, fill=SCROLL, outline="", tags="track")
        self.thumb_id = self.scrollbar_canvas.create_rectangle(4, 0, 8, 60, fill=SCROLL_H, 
                                                               outline="", tags="thumb")
        
        self.text_area.bind("<MouseWheel>", self._on_mousewheel)
        self.text_area.bind("<Enter>", lambda e: self.text_area.focus_set())
        self.text_area.bind("<<Modified>>", self._update_scrollbar)
        self.text_area.bind("<Configure>", self._update_scrollbar)
        
        # Toolbar
        toolbar = Frame(content, bg=CARD, height=36)
        toolbar.pack(fill="x", pady=(8, 0))
        toolbar.pack_propagate(False)
        
        self.status_label = tk.Label(toolbar, text="● Recording", font=("Segoe UI", 9),
                                    fg=SUCCESS, bg=CARD)
        self.status_label.pack(side="left")
        
        # Word count
        self.count_label = tk.Label(toolbar, text="0 words", font=("Segoe UI", 9),
                                   fg=SUBTEXT, bg=CARD)
        self.count_label.pack(side="left", padx=(16, 0))
        
        # Clear button
        clear_btn = self._create_tool_button(toolbar, "Clear", self._clear_transcript, "#666")
        clear_btn.pack(side="right", padx=(0, 8))
        
        # Save button
        save_btn = Frame(toolbar, bg=ACCENT, cursor="hand2", width=90, height=28)
        save_btn.pack(side="right")
        save_btn.pack_propagate(False)
        
        save_inner = Frame(save_btn, bg=ACCENT)
        save_inner.place(relx=0.5, rely=0.5, anchor="center")
        
        save_icon = Canvas(save_inner, width=12, height=12, bg=ACCENT, highlightthickness=0)
        save_icon.pack(side="left", padx=(0, 4))
        save_icon.create_line(6, 2, 6, 8, fill="white", width=2)
        save_icon.create_line(3, 6, 6, 9, fill="white", width=2)
        save_icon.create_line(9, 6, 6, 9, fill="white", width=2)
        
        save_text = tk.Label(save_inner, text="Save", font=("Segoe UI", 9, "bold"),
                            fg="white", bg=ACCENT, cursor="hand2")
        save_text.pack(side="left")
        
        for widget in [save_btn, save_inner, save_icon, save_text]:
            widget.bind("<Enter>", lambda e: self._set_save_hover(save_btn, save_icon, save_text, True))
            widget.bind("<Leave>", lambda e: self._set_save_hover(save_btn, save_icon, save_text, False))
            widget.bind("<Button-1>", self._save)

    def _create_window_button(self, parent, text, command, hover_color="#666", default_color="#888"):
        btn = Frame(parent, bg=CARD, width=30, height=30, cursor="hand2")
        btn.pack_propagate(False)
        
        label = tk.Label(btn, text=text, font=("Segoe UI", 12 if text == "×" else 14, "bold"),
                        fg=default_color, bg=CARD)
        label.place(relx=0.5, rely=0.5, anchor="center")
        
        def on_enter(e):
            label.config(fg=hover_color)
            btn.config(bg="#3a2525" if text == "×" else "#2a2a2a")
                
        def on_leave(e):
            label.config(fg=default_color)
            btn.config(bg=CARD)
        
        for w in [btn, label]:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda e: command())
        
        return btn

    def _create_tool_button(self, parent, text, command, color):
        btn = Frame(parent, bg=CARD, cursor="hand2")
        lbl = tk.Label(btn, text=text, font=("Segoe UI", 9), fg=color, bg=CARD, cursor="hand2")
        lbl.pack(padx=8, pady=4)
        
        def on_enter(e):
            lbl.config(fg="#aaa")
        def on_leave(e):
            lbl.config(fg=color)
            
        for w in [btn, lbl]:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", lambda e: command())
        return btn

    def _pulse_animation(self, canvas):
        if not self.running:
            return
        try:
            current = canvas.itemcget(self.pulse_id, "fill")
            new_color = ACCENT if current == "#3a5a9a" else "#3a5a9a"
            canvas.itemconfig(self.pulse_id, fill=new_color)
            self.root.after(1000, lambda: self._pulse_animation(canvas))
        except:
            pass

    def _set_save_hover(self, btn, icon, text, active):
        color = ACCENT_D if active else ACCENT
        btn.config(bg=color)
        icon.config(bg=color)
        text.config(bg=color)
        icon.delete("all")
        icon.create_line(6, 2, 6, 8, fill="white", width=2)
        icon.create_line(3, 6, 6, 9, fill="white", width=2)
        icon.create_line(9, 6, 6, 9, fill="white", width=2)

    def _setup_bindings(self):
        self.root.bind("<Command-s>", self._save)
        self.root.bind("<Control-s>", self._save)
        self.root.bind("<Control-c>", lambda e: self._copy_selection())
        self.root.bind("<Escape>", lambda e: self.quit())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        
        self.container.bind("<Motion>", self._check_resize_cursor)
        self.container.bind("<Button-1>", self._start_resize)
        self.container.bind("<B1-Motion>", self._on_resize)
        self.container.bind("<ButtonRelease-1>", self._stop_resize)

    def _start_drag(self, event):
        self._drag_data["x"] = event.x_root - self.root.winfo_x()
        self._drag_data["y"] = event.y_root - self.root.winfo_y()
        self._drag_data["dragging"] = True

    def _on_drag(self, event):
        if not self._drag_data["dragging"] or self._resize_data["edge"]:
            return
        x = event.x_root - self._drag_data["x"]
        y = event.y_root - self._drag_data["y"]
        self.root.geometry(f"+{x}+{y}")
    
    def _check_resize_cursor(self, event):
        if self._resize_data["edge"]:
            return
        w, h, edge = self.root.winfo_width(), self.root.winfo_height(), 6
        cursor = ""
        if event.x >= w - edge and event.y >= h - edge:
            cursor = "size_nw_se"
        elif event.x >= w - edge:
            cursor = "size_we"
        elif event.y >= h - edge:
            cursor = "size_ns"
        self.container.config(cursor=cursor)
    
    def _start_resize(self, event):
        w, h, edge = self.root.winfo_width(), self.root.winfo_height(), 6
        self._resize_data.update({"x": event.x_root, "y": event.y_root, "edge": None})
        
        if event.x >= w - edge and event.y >= h - edge:
            self._resize_data["edge"] = "se"
        elif event.x >= w - edge:
            self._resize_data["edge"] = "e"
        elif event.y >= h - edge:
            self._resize_data["edge"] = "s"
    
    def _on_resize(self, event):
        if not self._resize_data["edge"]:
            return
        dx = event.x_root - self._resize_data["x"]
        dy = event.y_root - self._resize_data["y"]
        w, h = self.root.winfo_width(), self.root.winfo_height()
        edge = self._resize_data["edge"]
        new_w, new_h = w, h
        
        if "e" in edge:
            new_w = max(self.min_width, w + dx)
        if "s" in edge:
            new_h = max(self.min_height, h + dy)
        
        self.root.geometry(f"{new_w}x{new_h}")
        self._resize_data["x"], self._resize_data["y"] = event.x_root, event.y_root
    
    def _stop_resize(self, event):
        self._resize_data["edge"] = None
        self.container.config(cursor="")

    def _minimize(self):
        self.root.iconify()

    def _on_mousewheel(self, event):
        self.text_area.yview_scroll(int(-1*(event.delta/120)), "units")
        self._update_scrollbar()

    def _update_scrollbar(self, event=None):
        try:
            first, last = self.text_area.yview()
            height = self.scrollbar_canvas.winfo_height()
            content_height = self.text_area.winfo_height()
            thumb_height = max(30, int(height * (last - first)))
            thumb_pos = int(first * height)
            self.scrollbar_canvas.coords(self.thumb_id, 4, thumb_pos, 8, thumb_pos + thumb_height)
            self.scrollbar_canvas.itemconfig(self.thumb_id, fill=SCROLL_H if thumb_height < height else SCROLL)
        except:
            pass

    def _append_text(self, text, is_partial=False):
        """Thread-safe text append with partial line handling"""
        self.text_area.config(state="normal")
        
        if is_partial:
            # Remove previous partial line if exists
            if hasattr(self, '_partial_line_start'):
                try:
                    self.text_area.delete(self._partial_line_start, "end-1c")
                except:
                    pass
            # Insert new partial line
            self._partial_line_start = self.text_area.index("end-1c")
            self.text_area.insert("end", text + " ")
        else:
            # Final text: remove partial marker and insert permanently
            if hasattr(self, '_partial_line_start'):
                try:
                    self.text_area.delete(self._partial_line_start, "end-1c")
                except:
                    pass
                delattr(self, '_partial_line_start')
            self.text_area.insert("end", text + "\n")
            
            # Add to permanent storage
            self.transcript_lines.append(text)
            
            # Auto-save after adding new line
            self._autosave()
            
            # Update word count
            words = len([w for w in " ".join(self.transcript_lines).split() if w.strip()])
            self.count_label.config(text=f"{words} words")
            
            # Trim if too long
            if len(self.transcript_lines) > MAX_TRANSCRIPT_LINES:
                removed = self.transcript_lines.pop(0)
                # Remove first line from text widget
                self.text_area.delete("1.0", "2.0")
        
        self.text_area.see("end")
        self.text_area.config(state="disabled")
        self._update_scrollbar()

    def _process_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                
                if isinstance(msg, dict):
                    if msg.get("type") == "partial":
                        self._append_text(msg["text"], is_partial=True)
                    elif msg.get("type") == "final":
                        self._append_text(msg["text"], is_partial=False)
                    elif msg.get("type") == "error":
                        self.status_label.config(text=f"● {msg['text']}", fg=DANGER)
                else:
                    # Legacy string message
                    self._append_text(str(msg), is_partial=False)
                    
        except queue.Empty:
            pass
        except Exception as e:
            print(f"Queue processing error: {e}")
            
        if self.running:
            self.root.after(50, self._process_queue)

    def _audio_loop(self):
        p = pyaudio.PyAudio()
        
        # List devices for debugging
        print("Available audio devices:")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"  {i}: {info['name']}")
        
        try:
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=RATE,
                input=True,
                input_device_index=CABLE_OUTPUT_INDEX,
                frames_per_buffer=CHUNK
            )
            print(f"Opened stream on device {CABLE_OUTPUT_INDEX}")
        except Exception as e:
            print(f"Failed to open audio: {e}")
            try:
                self.msg_queue.put_nowait({"type": "error", "text": f"Audio Error: {e}"})
            except:
                pass
            return

        stt = WhisperTranscriber()
        
        buffer_samples = int(RATE * BUFFER_DURATION)
        overlap_samples = int(RATE * OVERLAP_DURATION)
        chunks_per_buffer = buffer_samples // CHUNK
        
        audio_buffer = []
        last_text = ""
        silence_count = 0

        while self.running:
            try:
                data = stream.read(CHUNK, exception_on_overflow=False)
                chunk = np.frombuffer(data, dtype=np.float32)
                audio_buffer.append(chunk)

                if len(audio_buffer) >= chunks_per_buffer:
                    # Process buffer
                    audio = np.concatenate(audio_buffer)
                    
                    # Check energy
                    energy = np.sqrt(np.mean(audio ** 2))
                    
                    if energy > NOISE_GATE_THRESH:
                        text = stt.transcribe(audio)
                        
                        if text and text != last_text:
                            # Check if this is a continuation or new sentence
                            if last_text and (text.startswith(last_text) or last_text in text):
                                # It's an extension, send as partial then final
                                try:
                                    self.msg_queue.put_nowait({"type": "partial", "text": text})
                                    self.root.after(100, lambda: self.msg_queue.put_nowait({"type": "final", "text": text}))
                                except queue.Full:
                                    pass
                            else:
                                # New text
                                try:
                                    self.msg_queue.put_nowait({"type": "final", "text": text})
                                except queue.Full:
                                    pass
                            last_text = text
                            silence_count = 0
                        elif not text:
                            silence_count += 1
                            # If silence for a while, reset last_text to allow new sentences
                            if silence_count > 3:
                                last_text = ""
                    else:
                        silence_count += 1
                        if silence_count > 2 and last_text:
                            # Gap detected, reset for new sentence
                            last_text = ""
                    
                    # Keep overlap for continuity
                    overlap_start = max(0, len(audio_buffer) - overlap_samples // CHUNK)
                    audio_buffer = audio_buffer[overlap_start:]
                    
            except OSError as e:
                continue
            except Exception as e:
                print(f"Audio loop error: {e}")
                try:
                    self.msg_queue.put_nowait({"type": "error", "text": str(e)[:50]})
                except:
                    pass
                break

        try:
            stream.stop_stream()
            stream.close()
        except:
            pass
        p.terminate()
        print("Audio loop ended")

    def _clear_transcript(self):
        self.transcript_lines.clear()
        self.text_area.config(state="normal")
        self.text_area.delete("1.0", "end")
        self.text_area.config(state="disabled")
        self.count_label.config(text="0 words")
        if hasattr(self, '_partial_line_start'):
            delattr(self, '_partial_line_start')
        
        # Start new session file
        self.session_start = datetime.now()
        self.current_file = None

    def _copy_selection(self):
        try:
            selected = self.text_area.selection_get()
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        except:
            pass

    def _save(self, e=None):
        if not self.transcript_lines:
            messagebox.showinfo("Empty", "Nothing captured yet.", parent=self.root)
            return

        # Join all lines with proper formatting
        full_text = "\n".join(self.transcript_lines)
        
        name = datetime.now().strftime("transcript_%Y%m%d_%H%M%S.txt")
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            parent=self.root
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(full_text)
            messagebox.showinfo("Saved", f"Saved {len(self.transcript_lines)} lines to:\n{path}", parent=self.root)
        except Exception as ex:
            messagebox.showerror("Save failed", str(ex), parent=self.root)

    def quit(self):
        self.running = False
        alpha = self.root.attributes('-alpha')
        if alpha > 0.1:
            self.root.attributes('-alpha', alpha - 0.1)
            self.root.after(20, self.quit)
        else:
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    app = ModernCaptionApp(root)
    root.mainloop()




# pyinstaller --onefile --noconsole --icon=app.ico --name "VoiceClient" client.py