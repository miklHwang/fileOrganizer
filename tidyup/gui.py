"""The tidyup window (tkinter). A thin view: all work is done by app.run_cli in a worker thread."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tidyup import __version__, app, config, winutil
from tidyup.errors import TidyupError

_ACTION_KEYS = ("scan", "plan", "report", "trial", "undo", "apply", "upkeep", "migrate", "runs")
_STATUS_IDLE = "Ready."


def _config_path() -> Path:
    if config.is_frozen():
        return config.find_default_config()  # type: ignore[return-value]
    path = Path.cwd() / "config.yaml"
    if not path.exists():
        try:
            with open(winutil.long_path(path), "x", encoding="utf-8", newline="") as f:
                f.write(config.default_config_text())
        except FileExistsError:
            pass
    return path


class Window:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.q: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.worker: threading.Thread | None = None
        self.after_job = None
        self.root = tk.Tk()
        self.root.title(f"tidyup {__version__}")
        self.root.geometry("1024x700")
        self.root.minsize(800, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tasks = app.build_tasks(config_path, migrate_to=Path("migrate-target"))
        self.buttons: dict[str, ttk.Button] = {}
        self.open_buttons: dict[str, ttk.Button] = {}
        self._build()
        self.refresh()

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        r = self.root
        r.columnconfigure(1, weight=1)
        r.rowconfigure(1, weight=1)
        head = ttk.Label(r, text=f"Settings file: {self.config_path}\n"
                                 "Nothing is moved without asking you first.",
                         justify="left", padding=8)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        side = ttk.Frame(r, padding=8)
        side.grid(row=1, column=0, sticky="ns")
        for key in _ACTION_KEYS:
            t = self.tasks[key]
            b = ttk.Button(side, text=t.label, width=42, command=lambda k=key: self.start(k))
            b.pack(fill="x", pady=3)
            self.buttons[key] = b
        ttk.Separator(side).pack(fill="x", pady=8)
        for key, label, fn in (
                ("config", "Settings (config.yaml)", lambda: self.open_item("config")),
                ("manifest", "Open the plan in Excel", lambda: self.open_item("manifest")),
                ("migrate_manifest", "Open the move list",
                 lambda: self.open_item("migrate_manifest")),
                ("work_dir", "Open tidyup's data folder", lambda: self.open_item("work_dir"))):
            b = ttk.Button(side, text=label, width=42, command=fn)
            b.pack(fill="x", pady=3)
            self.open_buttons[key] = b
        body = ttk.Frame(r, padding=(0, 8, 8, 8))
        body.grid(row=1, column=1, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.text = tk.Text(body, wrap="word", state="disabled", font=("Consolas", 10))
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.tag_configure("error", foreground="#c00000")
        self.text.tag_configure("warn", foreground="#b36b00")
        self.text.tag_configure("success", foreground="#1a7f1a")
        self.text.tag_configure("heading", font=("Consolas", 10, "bold"))
        self.status = tk.StringVar(value=_STATUS_IDLE)
        ttk.Label(r, textvariable=self.status, padding=(8, 2)).grid(
            row=2, column=0, columnspan=2, sticky="ew")

    # ---------------------------------------------------------------- helpers
    def paths(self) -> dict[str, Path] | None:
        try:
            return app.work_paths(config.load_config(self.config_path))
        except TidyupError:
            return None

    def refresh(self) -> None:
        state = "disabled" if self.busy else "normal"
        for b in self.buttons.values():
            b.configure(state=state)
        p = self.paths()
        for key, b in self.open_buttons.items():
            if key in ("manifest", "migrate_manifest"):
                ok = p is not None and p[key].exists()
            else:
                ok = True
            b.configure(state="normal" if ok else "disabled")

    def write(self, kind: str, text: str) -> None:
        self.text.configure(state="normal")
        tag = kind if kind in ("error", "warn", "success", "heading") else ""
        self.text.insert("end", text + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def open_item(self, key: str) -> None:
        p = self.paths()
        path = self.config_path if key == "config" else (p[key] if p else None)
        try:
            if path is None:
                raise TidyupError("Cannot read the settings file. Open it and fix it first.")
            winutil.open_path(path)
        except TidyupError as e:
            messagebox.showerror("tidyup", str(e))

    # ---------------------------------------------------------------- jobs
    def start(self, key: str) -> None:
        if self.busy:
            return
        task = self.tasks[key]
        if key == "migrate":
            folder = filedialog.askdirectory(title="Choose the folder to move your files to")
            if not folder:
                return
            task = app.build_tasks(self.config_path, migrate_to=Path(folder))["migrate"]
        if key == "plan":
            p = self.paths()
            if p is not None and p["manifest"].exists() and not messagebox.askyesno(
                    "tidyup", "This replaces your current plan, including any Y/N changes. "
                              "Continue?", default="no"):
                return
        if key in ("trial", "apply"):
            messagebox.showinfo("tidyup", "Save and close manifest.csv in Excel first.")
        self.write("heading", task.label)
        if task.preview_argv is not None:
            self.run_job(task.preview_argv, lambda code: self.after_preview(task, code))
        else:
            self.run_job(task.argv, lambda code: self.finish(task, code))

    def run_job(self, argv: list[str], done) -> None:
        self.busy = True
        self.status.set("Working...")
        self.refresh()
        sink = lambda kind, text: self.q.put((kind, text))  # noqa: E731

        def work() -> None:
            code = app.run_cli(argv, sink)
            self.q.put(("done", code))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.poll(done)

    def poll(self, done) -> None:
        finished: int | None = None
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "done":
                    finished = int(payload)  # type: ignore[arg-type]
                elif kind == "progress":
                    self.status.set(str(payload))
                else:
                    self.write(kind, str(payload))
        except queue.Empty:
            pass
        if finished is None:
            self.after_job = self.root.after(100, lambda: self.poll(done))
            return
        self.after_job = None
        self.busy = False
        self.status.set(_STATUS_IDLE)
        self.refresh()
        done(finished)

    def after_preview(self, task: app.Task, code: int) -> None:
        if code != 0:
            self.problem(task, code)
            return
        if messagebox.askyesno("tidyup", task.confirm or "Continue?", default="no"):
            self.run_job(task.argv, lambda c: self.finish(task, c))
        else:
            self.write("info", "Cancelled. Nothing was changed.")

    def finish(self, task: app.Task, code: int) -> None:
        if code != 0:
            self.problem(task, code)
        else:
            self.write("success", "Done.")

    def problem(self, task: app.Task, code: int) -> None:
        self.write("error", f"Stopped: '{task.label}' did not finish (code {code}). "
                            "Nothing more was done.")
        messagebox.showerror("tidyup", "That did not work. See the red text for details.")

    def on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
                "tidyup", "A job is still running. Closing now interrupts it. An interrupted "
                          "run can be undone or resumed. Close anyway?", default="no"):
            return
        self.root.destroy()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else []
    path = _config_path()
    win = Window(path)
    try:
        if "--selftest" in args:
            win.root.update()
            missing = [k for k in _ACTION_KEYS if k not in win.buttons]
            if missing:
                return 1
            return 0
        win.root.mainloop()
        return 0
    finally:
        try:
            win.root.destroy()
        except tk.TclError:
            pass
