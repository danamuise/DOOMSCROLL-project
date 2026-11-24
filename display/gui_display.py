"""Tkinter desktop display for DOOMSCROLLER.

The Tk UI runs on the main thread; the social feed, OpenAI requests, and
connectivity checks run in worker threads and communicate via a queue.
The File menu supports Pause/Resume Feed; its other commands and the Settings
and Status headings are visual placeholders for now.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime
import os
from pathlib import Path
import queue
import random
import socket
import threading
import time
import tkinter as tk

from ai_interpreter import evaluate_post
from marker_palette import emotion_to_marker
from processing.sanitizer import MessageSanitizer
from sources.combined_source import CombinedSource


BACKGROUND = "#393939"
CONSOLE_BG = "#050505"
NORMAL_TEXT = "#E5E5E5"
FEED_FONT = ("Consolas", 13, "bold")
STATUS_GREEN = "#00EA00"
STATUS_RED = "#FF5050"
MAX_POSTS = 500


def connection_label(started: float | None, now: float) -> str:
    """Status clock, with resets on disconnect."""
    if started is None:
        return "Disconnected"
    elapsed_minutes = max(0, int((now - started) // 60))
    hours, minutes = divmod(elapsed_minutes, 60)
    return f"Connected for {hours} hours, {minutes} minutes"


def display_source(source: str) -> str:
    """Source labels match the legacy console display."""
    label = source.casefold()
    if label == "4chan":
        return source + " 🍀"
    if label.startswith("mastodon"):
        return source + " 🦣"
    if label == "8kun":
        return source + " 8️⃣"
    if label == "bluesky":
        return source + " 🦋"
    return source


def selected_color(interpretation: dict) -> tuple[str, str]:
    """Use the emotion-based physical marker color in the GUI.

    Black text uses a pale background so it is visible on the black feed.
    Color selection uses emotion dimensions, not only the tone score.
    """
    marker = emotion_to_marker(interpretation)
    if marker.number == 1:
        return marker.hex_color, "#D4D4D4"
    return marker.hex_color, CONSOLE_BG


def demo_posts():
    """Local presentation preview; makes no API calls or network requests."""
    data = [
        ("Mastodon", "#community", "Neighbors helping each other after the storm.", {"valence": 0.8, "warmth": 0.95, "intensity": 0.4, "rationale": "Supportive and compassionate language."}),
        ("Bluesky", "#discussion", "I'm worried about what comes next.", {"valence": 0.15, "sadness": 0.85, "vulnerability": 0.7, "intensity": 0.7, "rationale": "Personal concern and vulnerability without hostility."}),
        ("4chan", "/int/ International", "Does anybody know what happened today?", {"valence": 0.0, "uncertainty": 0.85, "tension": 0.45, "intensity": 0.15, "rationale": "A neutral question expressing uncertainty."}),
        ("Bluesky", "#future", "We can make things better together.", {"valence": 0.65, "hope": 0.95, "warmth": 0.7, "intensity": 0.6, "rationale": "Hope and a sense of common purpose."}),
        ("8kun", "General", "You people deserve nothing good!", {"valence": -0.8, "aggression": 0.85, "intensity": 0.9, "rationale": "A hostile personal attack."}),
    ]
    while True:
        for source, subject, text, interpretation in data:
            yield {"source": source, "subject": subject, "text": text,
                   "timestamp": datetime.now().isoformat(timespec="seconds"),
                   "demo_interpretation": interpretation}


class DoomscrollGUI:
    def __init__(self, demo: bool = False):
        self.root = tk.Tk()
        self.root.title("DOOMSCROLLER")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry("1210x860")
        self.root.minsize(760, 540)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue = queue.Queue()
        self.stopped = threading.Event()
        self.paused = threading.Event()
        self.deferred_feed_events: deque[tuple] = deque()
        self.connected_since: float | None = None
        self.posts: dict[int, dict] = {}
        self.post_ids: list[int] = []
        self.item_by_identity: dict[int, int] = {}
        self.counter = 0
        self.demo = demo
        self._build()

    def _build(self):
        outer = tk.Frame(self.root, bg=BACKGROUND)
        outer.pack(fill="both", expand=True, padx=28, pady=(20, 22))

        menu = tk.Frame(outer, bg="#686868", height=29)
        menu.pack(fill="x", pady=(0, 3))
        file_button = tk.Menubutton(
            menu, text="File", font=("Courier New", 13),
            fg="#FFFFFF", bg="#686868", activeforeground="#FFFFFF",
            activebackground="#777777", relief="flat", bd=0,
            highlightthickness=0, padx=17, pady=4,
        )
        file_button.pack(side="left")
        self.file_menu = tk.Menu(
            file_button, tearoff=False, bg="#FFFFFF", fg="#111111",
            activebackground="#DDDDDD", activeforeground="#000000",
            font=("Courier New", 13), bd=1,
        )
        file_button.configure(menu=self.file_menu)
        self.file_menu.add_command(label="Pause Feed", command=self._toggle_feed_pause)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Reset Arm", state="disabled")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Restart Pi-4", state="disabled")
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Quit", state="disabled")
        for title in ("Settings", "Status"):
            tk.Label(menu, text=title, font=("Courier New", 13), fg="#FFFFFF",
                     bg="#686868", padx=17, pady=4).pack(side="left")

        project_root = Path(__file__).resolve().parent.parent
        logo_path = project_root / "DoomScrollLogo.png"
        if not logo_path.is_file():
            logo_path = project_root / "assets" / "DoomScrollLogo.png"
        if logo_path.is_file():
            image = tk.PhotoImage(file=str(logo_path))
            self.logo_image = image.subsample(2, 2)
            tk.Label(outer, image=self.logo_image, bg="#FFFFFF", anchor="w",
                     padx=10, pady=5).pack(anchor="w", pady=(0, 4))
        else:
            tk.Label(outer, text="<DOOMSCROLLER>", fg="#000000", bg="#FFFFFF",
                     font=("Courier New", 30, "bold"), padx=18, pady=15).pack(anchor="w")

        tk.Label(outer, text="By Dana Muise", bg=BACKGROUND, fg="#EEEEEE",
                 font=("Courier New", 13), anchor="w").pack(fill="x")
        self.status = tk.Label(outer, text="Status: Disconnected",
                               bg=BACKGROUND, fg=STATUS_RED,
                               font=("Courier New", 13, "bold"), anchor="w")
        self.status.pack(fill="x", pady=(2, 10))

        frame = tk.Frame(outer, bg=CONSOLE_BG)
        frame.pack(fill="both", expand=True)
        scroll = tk.Scrollbar(frame, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.feed = tk.Text(frame, bg=CONSOLE_BG, fg=NORMAL_TEXT,
                            font=FEED_FONT, wrap="word", padx=17, pady=10,
                            insertwidth=0, cursor="arrow", relief="flat", bd=0,
                            highlightthickness=0, state="disabled",
                            yscrollcommand=scroll.set)
        self.feed.pack(side="left", fill="both", expand=True)
        scroll.config(command=self.feed.yview)
        self.feed.tag_configure("meta", foreground=NORMAL_TEXT)
        self.feed.tag_configure("warning", foreground=STATUS_RED)

    def _toggle_feed_pause(self):
        """Pause/resume the incoming feed without affecting the connection clock."""
        if self.paused.is_set():
            self.paused.clear()
            self.file_menu.entryconfigure(0, label="Pause Feed")
        else:
            self.paused.set()
            self.file_menu.entryconfigure(0, label="Resume Feed")

    def _wait_for_resume(self) -> bool:
        """Worker thread waits here until resumed or the window closes."""
        while self.paused.is_set() and not self.stopped.is_set():
            self.stopped.wait(0.1)
        return not self.stopped.is_set()

    def _render_post(self, post_id: int):
        post = self.posts[post_id]
        tag = f"entry_{post_id}"
        colored_tag = f"selected_{post_id}"
        ranges = self.feed.tag_ranges(tag)

        self.feed.config(state="normal")
        at_top = self.feed.yview()[0] <= 0.003
        if not at_top:
            self.feed.mark_set("viewport_anchor", "@0,0")
            self.feed.mark_gravity("viewport_anchor", "right")

        if ranges:
            start = str(ranges[0])
            self.feed.delete(ranges[0], ranges[-1])
        else:
            start = "1.0"
        self.feed.mark_set("entry_insertion", start)
        self.feed.mark_gravity("entry_insertion", "right")

        def add(text, style="meta"):
            self.feed.insert("entry_insertion", text, (tag, style))

        add("=" * 76 + "\n")
        add(f"Source:  {display_source(post['source'])}\n")
        add(f"Feed:    {post['subject']}\n")
        add(f"Time:    {post['timestamp']}\n")
        if post["selected"]:
            interpretation = post.get("interpretation")
            error = post.get("error")
            if interpretation:
                marker = emotion_to_marker(interpretation)
                foreground, background = selected_color(interpretation)
                self.feed.tag_configure(colored_tag, foreground=foreground,
                                         background=background,
                                         font=FEED_FONT)
                add(f"Score:   {round(100 * interpretation['valence']):+d}/100  "
                    f"Intensity: {round(100 * interpretation['intensity'])}/100\n", colored_tag)
                add(f"Rationale: {interpretation.get('rationale', '')}\n", colored_tag)
                add(f"[AI INTERPRETED - MARKER {marker.number}: {marker.name} "
                    f"({marker.emotion}) - DRAW HOLD {post['hold']:g}s]\n", colored_tag)
            else:
                self.feed.tag_configure(colored_tag, foreground="#51C5F4",
                                         background=CONSOLE_BG,
                                         font=FEED_FONT)
                add((f"Score:   unavailable ({error})\n" if error else "Score:   interpreting with AI...\n"), colored_tag)
                add(f"[AI SELECTED - DRAW HOLD {post['hold']:g}s]\n", colored_tag)
            add("\n")
            add(post["safe_text"] + "\n", colored_tag)
        else:
            add("\n" + post["safe_text"] + "\n")
        add("\n")

        # Keep the transcript bounded during a long-running exhibition.
        while len(self.post_ids) > MAX_POSTS:
            oldest = self.post_ids.pop()
            tag_ranges = self.feed.tag_ranges(f"entry_{oldest}")
            if tag_ranges:
                self.feed.delete(tag_ranges[0], tag_ranges[-1])
            if oldest in self.posts:
                self.item_by_identity.pop(id(self.posts[oldest]), None)
            self.posts.pop(oldest, None)

        if at_top:
            self.feed.yview_moveto(0.0)
        else:
            self.feed.yview("viewport_anchor")
        self.feed.config(state="disabled")

    def _record(self, info: dict):
        self.counter += 1
        self.posts[self.counter] = info
        self.item_by_identity[id(info)] = self.counter
        self.post_ids.insert(0, self.counter)
        self._render_post(self.counter)
        return self.counter

    def _drain_events(self):
        try:
            for _ in range(30):
                if not self.paused.is_set() and self.deferred_feed_events:
                    event = self.deferred_feed_events.popleft()
                else:
                    event = self.events.get_nowait()
                kind, *args = event
                if self.paused.is_set() and kind in ("new_post", "interpretation_seq"):
                    # An API call may have finished just as Pause was pressed.
                    # Retain its UI update until the user resumes.
                    self.deferred_feed_events.append(event)
                    continue
                if kind == "connection":
                    is_online = args[0]
                    if is_online and self.connected_since is None:
                        self.connected_since = time.monotonic()
                    elif not is_online:
                        self.connected_since = None
                elif kind == "new_post":
                    self._record(args[0])
                elif kind == "interpretation_seq":
                    info, result, error = args
                    post_id = self.item_by_identity.get(id(info))
                    if post_id in self.posts:
                        self.posts[post_id]["interpretation"] = result
                        self.posts[post_id]["error"] = error
                        self._render_post(post_id)
                elif kind == "warning":
                    self.feed.config(state="normal")
                    self.feed.insert("1.0", f"[System] {args[0]}\n", "warning")
                    self.feed.yview_moveto(0)
                    self.feed.config(state="disabled")
        except queue.Empty:
            pass
        if not self.stopped.is_set():
            self.root.after(100, self._drain_events)

    def _refresh_status(self):
        connected = self.connected_since is not None
        self.status.config(text=f"Status: {connection_label(self.connected_since, time.monotonic())}",
                           fg=STATUS_GREEN if connected else STATUS_RED)
        if not self.stopped.is_set():
            self.root.after(1000, self._refresh_status)

    def _monitor_connection(self):
        last_state = None
        while not self.stopped.is_set():
            connected = False
            for host in ("api.openai.com", "www.google.com"):
                try:
                    with socket.create_connection((host, 443), timeout=3):
                        connected = True
                    break
                except OSError:
                    pass
            if connected != last_state:
                self.events.put(("connection", connected))
                last_state = connected
            self.stopped.wait(12)

    def _feed_worker(self):
        if self.demo:
            posts = demo_posts()
            count = 0
            while not self.stopped.is_set():
                if not self._wait_for_resume():
                    break
                m = next(posts)
                count += 1
                selected = count % 3 == 0
                info = {"source": m["source"], "subject": m["subject"],
                        "timestamp": m["timestamp"], "safe_text": m["text"],
                        "selected": selected, "hold": 10.0,
                        "interpretation": m["demo_interpretation"] if selected else None}
                self.events.put(("new_post", info))
                self.stopped.wait(3)
            return

        if not os.getenv("OPENAI_API_KEY"):
            self.events.put(("warning", "OPENAI_API_KEY is not configured. Set it before starting the live feed."))
            return
        try:
            from openai import OpenAI
            client = OpenAI()
            sanitizer = MessageSanitizer(client)
            source = CombinedSource(display_seconds=5.0)
            posts_until_selection = random.randint(4, 7)
            hold = max(0.0, float(os.getenv("CHATTERFEED_DRAW_SECONDS", "10")))
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="interpreter") as pool:
                for message in source.stream():
                    if not self._wait_for_resume():
                        break
                    posts_until_selection -= 1
                    selected = posts_until_selection == 0
                    if selected:
                        posts_until_selection = random.randint(4, 7)
                    future = pool.submit(evaluate_post, client, message["text"]) if selected else None
                    try:
                        # Never display unsanitized text in the public feed.
                        safe = sanitizer.clean(message["text"])
                    except Exception as exc:
                        self.events.put(("warning", f"Sanitizer failed; post withheld ({type(exc).__name__})."))
                        continue
                    info = {"source": message["source"], "subject": message["subject"],
                            "timestamp": message["timestamp"], "safe_text": safe,
                            "selected": selected, "hold": hold, "interpretation": None}
                    if not self._wait_for_resume():
                        break
                    self.events.put(("new_post", info))
                    # The GUI assigns IDs and matches updates via this exact
                    # info dictionary, which stays on the event queue.
                    if selected:
                        # Hold starts as soon as the post enters the GUI queue.
                        start = time.monotonic()
                        try:
                            interpretation = future.result(timeout=30)
                            self.events.put(("interpretation_seq", info, interpretation, None))
                        except FutureTimeout:
                            self.events.put(("interpretation_seq", info, None, "AI timeout"))
                        except Exception as exc:
                            self.events.put(("interpretation_seq", info, None, type(exc).__name__))
                        remaining = hold - (time.monotonic() - start)
                        if remaining > 0:
                            self.stopped.wait(remaining)
        except Exception as exc:
            self.events.put(("warning", f"Feed stopped: {type(exc).__name__}: {exc}"))

    def run(self):
        threading.Thread(target=self._monitor_connection, daemon=True).start()
        threading.Thread(target=self._feed_worker, daemon=True).start()
        self._drain_events()
        self._refresh_status()
        self.root.mainloop()

    def close(self):
        self.stopped.set()
        self.root.destroy()


def launch(demo: bool = False):
    DoomscrollGUI(demo=demo).run()
