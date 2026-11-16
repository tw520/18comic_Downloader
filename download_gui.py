"""禁漫天堂下載器的視窗介面。"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import jmcomic
from PIL import Image, ImageTk
from jmcomic import (
    JmcomicException,
    MissingAlbumPhotoException,
    PartialDownloadFailedException,
    RequestRetryAllFailException,
    fix_windir_name,
)

from download import SCRIPT_DIR, build_option, parse_target


SETTINGS_PATH = SCRIPT_DIR / "settings.json"
LOG_PATH = SCRIPT_DIR / "download.log"
LOGO_PATH = SCRIPT_DIR / "new_logo.png"
SITE_URL = "https://18comic.vip/"


def load_logo(height: int) -> ImageTk.PhotoImage | None:
    if not LOGO_PATH.is_file():
        return None
    image = Image.open(LOGO_PATH).convert("RGBA")
    width = max(1, round(image.width * height / image.height))
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image)


def enable_dpi() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        return


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_catalog(url: str):
    """讀取漫畫名稱與章節。章節網址會回到所屬漫畫，並標出該話。"""
    kind, target_id = parse_target(url)
    client = build_option(SCRIPT_DIR / "downloads").new_jm_client()
    focus_id = None
    if kind == "photo":
        photo = client.get_photo_detail(target_id)
        target_id = str(photo.album_id)
        focus_id = str(photo.photo_id)
    try:
        album = client.get_album_detail(target_id)
    except MissingAlbumPhotoException:
        raise ValueError(f"找不到編號 {target_id} 的漫畫") from None

    chapters = []
    for photo in album:
        if getattr(photo, "from_album", None) is None:
            photo.from_album = album
        chapters.append(
            {
                "id": str(photo.photo_id),
                "label": fix_windir_name(photo.folder_name),
                "checked": focus_id is None or str(photo.photo_id) == focus_id,
            }
        )
    if not chapters:
        raise ValueError("這部漫畫沒有可下載的章節")
    return {
        "name": str(album.name),
        "author": str(album.author or ""),
        "chapters": chapters,
    }


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("禁漫天堂下載器")
        self.root.minsize(680, 620)
        self.root.geometry("820x760")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        settings = load_settings()
        default_dir = settings.get("output_dir") or str(SCRIPT_DIR / "downloads")

        self.url_var = tk.StringVar()
        self.name_var = tk.StringVar(value="尚未讀取")
        self.author_var = tk.StringVar(value="")
        self.save_var = tk.StringVar(value=default_dir)
        self.preview_var = tk.StringVar(value="讀取目錄後會顯示漫畫資料夾路徑")
        self.selection_var = tk.StringVar(value="尚未載入章節")
        self.status_var = tk.StringVar(value="貼上漫畫網址，按「讀取目錄」。")
        self.save_var.trace_add("write", lambda *_: self.update_preview())

        self.catalog: dict | None = None
        self.chapter_rows: list[dict] = []
        self._batch = False
        self.fetching = False
        self.downloading = False
        self.stop_requested = False
        self.events: queue.Queue = queue.Queue()

        self._build()
        self.update_preview()
        self.url_entry.focus_set()
        self.root.after(100, self._drain_events)

    def open_site(self, _event=None) -> None:
        webbrowser.open(SITE_URL)

    def post(self, callback) -> None:
        self.events.put(callback)

    def _drain_events(self) -> None:
        try:
            while True:
                callback = self.events.get_nowait()
                callback()
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(100, self._drain_events)

    def _build(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 18, "bold"))
        style.configure("Name.TLabel", font=("Microsoft JhengHei UI", 12, "bold"))
        style.configure("TLabel", font=("Microsoft JhengHei UI", 10))
        style.configure("TButton", font=("Microsoft JhengHei UI", 10), padding=6)
        style.configure("Primary.TButton", font=("Microsoft JhengHei UI", 10, "bold"), padding=8)

        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))
        self.logo_image = load_logo(84)
        self.icon_image = load_logo(48)
        if self.icon_image is not None:
            root.iconphoto(True, self.icon_image)
        if self.logo_image is not None:
            self.logo_label = ttk.Label(header, image=self.logo_image, cursor="hand2")
            self.logo_label.pack(side="left", padx=(0, 12))
            self.logo_label.bind("<Button-1>", self.open_site)
        ttk.Label(header, text="禁漫天堂下載器", style="Title.TLabel").pack(side="left")

        url_row = ttk.Frame(root)
        url_row.grid(row=1, column=0, sticky="ew", padx=16)
        url_row.columnconfigure(1, weight=1)
        ttk.Label(url_row, text="網址").grid(row=0, column=0, padx=(0, 8))
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew")
        self.url_entry.bind("<Return>", lambda _event: self.on_fetch())
        self._attach_entry_menu(self.url_entry)
        self.fetch_button = ttk.Button(url_row, text="讀取目錄", command=self.on_fetch)
        self.fetch_button.grid(row=0, column=2, padx=(8, 0))

        ttk.Label(
            root,
            text="例如 https://18comic.vip/album/1044827/ ，也可貼上某一話的網址",
            foreground="#666666",
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(4, 8))

        info = ttk.Frame(root)
        info.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        info.columnconfigure(1, weight=1)
        ttk.Label(info, text="漫畫名稱").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(info, textvariable=self.name_var, style="Name.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(info, text="作者").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        ttk.Label(info, textvariable=self.author_var).grid(row=1, column=1, sticky="w", pady=(4, 0))

        tools = ttk.Frame(root)
        tools.grid(row=4, column=0, sticky="nsew", padx=16)
        tools.columnconfigure(0, weight=1)
        tools.rowconfigure(1, weight=1)
        tool_bar = ttk.Frame(tools)
        tool_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(tool_bar, text="章節").pack(side="left")
        ttk.Button(tool_bar, text="全選", command=lambda: self.set_all(True)).pack(side="left", padx=(12, 4))
        ttk.Button(tool_bar, text="全不選", command=lambda: self.set_all(False)).pack(side="left")
        ttk.Label(tool_bar, textvariable=self.selection_var).pack(side="right")

        list_wrap = ttk.Frame(tools)
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            list_wrap,
            height=320,
            highlightthickness=1,
            highlightbackground="#c5c5c5",
            bg="white",
            borderwidth=0,
        )
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.chapter_frame = tk.Frame(self.canvas, bg="white")
        self.canvas_window = self.canvas.create_window((0, 0), window=self.chapter_frame, anchor="nw")
        self.chapter_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._stretch_chapter_frame)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))
        self.empty_label = tk.Label(
            self.chapter_frame,
            text="讀取目錄後，可勾選要下載的章節。",
            bg="white",
            fg="#777777",
            font=("Microsoft JhengHei UI", 10),
            anchor="w",
        )
        self.empty_label.pack(fill="x", padx=10, pady=10)

        save_row = ttk.Frame(root)
        save_row.grid(row=5, column=0, sticky="ew", padx=16, pady=(10, 0))
        save_row.columnconfigure(1, weight=1)
        ttk.Label(save_row, text="儲存位置").grid(row=0, column=0, padx=(0, 8))
        self.save_entry = ttk.Entry(save_row, textvariable=self.save_var)
        self.save_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(save_row, text="瀏覽…", command=self.choose_directory)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))

        self.preview_label = ttk.Label(root, textvariable=self.preview_var, wraplength=760)
        self.preview_label.grid(row=6, column=0, sticky="ew", padx=16, pady=(6, 0))

        actions = ttk.Frame(root)
        actions.grid(row=7, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.download_button = ttk.Button(
            actions, text="開始下載", style="Primary.TButton", command=self.on_download
        )
        self.download_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止", command=self.on_stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="開啟資料夾", command=self.open_directory).pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.grid(row=8, column=0, sticky="ew", padx=16, pady=(10, 0))
        ttk.Label(root, textvariable=self.status_var).grid(
            row=9, column=0, sticky="w", padx=16, pady=(6, 14)
        )

    def _attach_entry_menu(self, entry: ttk.Entry) -> None:
        menu = tk.Menu(entry, tearoff=0)
        menu.add_command(label="剪下", command=lambda: self._entry_cut(entry))
        menu.add_command(label="複製", command=lambda: self._entry_copy(entry))
        menu.add_command(label="貼上", command=lambda: self._entry_paste(entry))
        menu.add_separator()
        menu.add_command(label="全選", command=lambda: self._entry_select_all(entry))

        def show_menu(event):
            if str(entry.cget("state")) == "disabled":
                return "break"
            entry.focus_set()
            entry.icursor(f"@{event.x}")
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        entry.bind("<Button-3>", show_menu)

    def _entry_cut(self, entry: ttk.Entry) -> None:
        if not entry.selection_present():
            return
        self._entry_copy(entry)
        entry.delete("sel.first", "sel.last")

    def _entry_copy(self, entry: ttk.Entry) -> None:
        if not entry.selection_present():
            return
        text = entry.selection_get()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _entry_paste(self, entry: ttk.Entry) -> None:
        if str(entry.cget("state")) == "disabled":
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        text = text.replace("\r\n", "\n").split("\n", 1)[0].strip()
        if not text:
            return
        if entry.selection_present():
            entry.delete("sel.first", "sel.last")
        entry.insert("insert", text)

    def _entry_select_all(self, entry: ttk.Entry) -> None:
        entry.select_range(0, "end")
        entry.icursor("end")

    def _stretch_chapter_frame(self, event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def update_preview(self) -> None:
        raw = self.save_var.get().strip()
        name = "" if self.catalog is None else self.catalog["name"]
        if not raw or not name:
            self.preview_var.set("會在儲存位置下建立「漫畫名稱／每一話」資料夾")
            return
        album_dir = Path(raw) / fix_windir_name(name)
        self.preview_var.set(f"漫畫資料夾：{album_dir}")

    def choose_directory(self) -> None:
        current = self.save_var.get().strip()
        selected = filedialog.askdirectory(initialdir=current or str(SCRIPT_DIR))
        if selected:
            self.save_var.set(selected)
            self._remember_directory(selected)

    def open_directory(self) -> None:
        path = self._album_dir() if self.catalog else Path(self.save_var.get().strip() or SCRIPT_DIR)
        try:
            path.mkdir(parents=True, exist_ok=True)
            import os

            os.startfile(path)  # noqa: S606  Windows 檔案總管
        except OSError as exc:
            messagebox.showerror("無法開啟資料夾", str(exc))

    def _remember_directory(self, path: str) -> None:
        data = load_settings()
        data["output_dir"] = path
        try:
            save_settings(data)
        except OSError:
            return

    def _album_dir(self) -> Path:
        base = Path(self.save_var.get().strip())
        if self.catalog is None:
            return base
        return base / fix_windir_name(self.catalog["name"])

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.fetch_button.configure(state=state)
        self.download_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.url_entry.configure(state=state)
        self.save_entry.configure(state=state)

    def on_fetch(self) -> None:
        if self.fetching or self.downloading:
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo("尚未輸入", "請先貼上漫畫網址。")
            return
        self.fetching = True
        self.set_busy(True)
        self.status_var.set("正在讀取漫畫名稱與章節…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url: str) -> None:
        try:
            catalog = fetch_catalog(url)
        except ValueError as exc:
            self.post(lambda message=str(exc): self._fetch_failed(message))
        except RequestRetryAllFailException:
            self.post(lambda: self._fetch_failed("連線失敗，站點沒有回應。請稍後再試。"))
        except JmcomicException as exc:
            self.post(lambda message=str(exc): self._fetch_failed(f"讀取失敗：{message}"))
        except Exception as exc:
            self.post(lambda message=str(exc): self._fetch_failed(f"讀取失敗：{message}"))
        else:
            self.post(lambda data=catalog: self._fetch_done(data))

    def _fetch_failed(self, message: str) -> None:
        self.fetching = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.set_busy(False)
        self.status_var.set(message)
        messagebox.showerror("讀取失敗", message)

    def _fetch_done(self, catalog: dict) -> None:
        self.fetching = False
        self.catalog = catalog
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0, maximum=max(len(catalog["chapters"]), 1))
        self.name_var.set(catalog["name"])
        self.author_var.set(catalog["author"] or "未提供")
        self._render_chapters(catalog["chapters"])
        self.update_preview()
        self.set_busy(False)
        selected = sum(1 for chapter in catalog["chapters"] if chapter["checked"])
        self.status_var.set(f"已讀取 {len(catalog['chapters'])} 話。勾選後按「開始下載」。")
        self.selection_var.set(f"已選 {selected} / {len(catalog['chapters'])} 話")

    def _render_chapters(self, chapters: list[dict]) -> None:
        for child in self.chapter_frame.winfo_children():
            child.destroy()
        self.chapter_rows = []
        for chapter in chapters:
            var = tk.BooleanVar(value=chapter["checked"])
            row = tk.Checkbutton(
                self.chapter_frame,
                text=chapter["label"],
                variable=var,
                anchor="w",
                bg="white",
                activebackground="#f3f3f3",
                selectcolor="white",
                font=("Microsoft JhengHei UI", 10),
                command=self.update_selection_count,
            )
            row.pack(fill="x", padx=8, pady=1)
            self.chapter_rows.append(
                {"id": chapter["id"], "label": chapter["label"], "var": var, "widget": row}
            )
        self.canvas.yview_moveto(0)
        self.update_selection_count()

    def set_all(self, checked: bool) -> None:
        self._batch = True
        for row in self.chapter_rows:
            row["var"].set(checked)
        self._batch = False
        self.update_selection_count()

    def update_selection_count(self) -> None:
        if self._batch:
            return
        total = len(self.chapter_rows)
        selected = len(self.selected_rows())
        if total == 0:
            self.selection_var.set("尚未載入章節")
        else:
            self.selection_var.set(f"已選 {selected} / {total} 話")

    def selected_rows(self) -> list[dict]:
        return [row for row in self.chapter_rows if row["var"].get()]

    def on_download(self) -> None:
        if self.fetching or self.downloading:
            return
        if self.catalog is None:
            messagebox.showinfo("尚未讀取", "請先讀取目錄。")
            return
        selected = self.selected_rows()
        if not selected:
            messagebox.showinfo("尚未勾選", "請至少勾選一話。")
            return
        save_dir = self.save_var.get().strip()
        if not save_dir:
            messagebox.showinfo("尚未選擇位置", "請選擇漫畫儲存位置。")
            return
        try:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("無法建立資料夾", str(exc))
            return
        self._remember_directory(save_dir)
        self.downloading = True
        self.stop_requested = False
        self.set_busy(True)
        self.stop_button.configure(state="normal")
        for row in self.chapter_rows:
            row["widget"].configure(state="disabled")
        self.progress.configure(mode="determinate", maximum=len(selected), value=0)
        self.status_var.set(f"準備下載 {len(selected)} 話…")
        threading.Thread(
            target=self._download_worker,
            args=(save_dir, [(row["id"], row["label"]) for row in selected]),
            daemon=True,
        ).start()

    def _download_worker(self, save_dir: str, chapters: list[tuple[str, str]]) -> None:
        option = build_option(Path(save_dir))
        failed: list[str] = []
        finished = 0
        stopped = False
        for index, (photo_id, label) in enumerate(chapters, start=1):
            if self.stop_requested:
                stopped = True
                break
            self.post(lambda i=index, n=len(chapters), name=label: self._mark_downloading(i, n, name))
            try:
                jmcomic.download_photo(photo_id, option)
            except PartialDownloadFailedException:
                failed.append(label)
            except RequestRetryAllFailException:
                failed.append(label)
                stopped = True
                self.post(lambda name=label: self.status_var.set(f"{name} 連線失敗，已停止後續章節。"))
                self.post(lambda i=index: self.progress.configure(value=i))
                break
            except JmcomicException:
                failed.append(label)
            else:
                finished += 1
                self.post(lambda photo=photo_id: self._mark_chapter_done(photo))
            self.post(lambda i=index: self.progress.configure(value=i))
        self.post(lambda: self._download_finished(finished, failed, stopped))

    def _mark_downloading(self, index: int, total: int, label: str) -> None:
        self.status_var.set(f"正在下載 {index}/{total}：{label}")

    def _mark_chapter_done(self, photo_id: str) -> None:
        for row in self.chapter_rows:
            if row["id"] == photo_id:
                row["widget"].configure(text=f"{row['label']}    已完成")
                break

    def _download_finished(self, finished: int, failed: list[str], stopped: bool) -> None:
        self.downloading = False
        self.stop_button.configure(state="disabled")
        self.set_busy(False)
        for row in self.chapter_rows:
            row["widget"].configure(state="normal")
        album_dir = self._album_dir()
        if stopped and finished == 0 and not failed:
            self.status_var.set("已停止。")
        elif failed:
            self.status_var.set(f"完成 {finished} 話，失敗 {len(failed)} 話。可再下載一次，已完成的圖片會跳過。")
            messagebox.showwarning(
                "部分章節失敗",
                "這些章節沒有完整下載：\n" + "\n".join(failed[:12]),
            )
        elif stopped:
            self.status_var.set(f"已停止，完成 {finished} 話。檔案在：{album_dir}")
        else:
            self.status_var.set(f"下載完成，共 {finished} 話。檔案在：{album_dir}")
            messagebox.showinfo("下載完成", f"共 {finished} 話\n{album_dir}")

    def on_stop(self) -> None:
        if self.downloading:
            self.stop_requested = True
            self.status_var.set("目前這一話下完後會停止。")
            self.stop_button.configure(state="disabled")

    def on_close(self) -> None:
        if self.downloading and not messagebox.askokcancel("關閉", "下載還在進行。關閉後，目前這一話仍可能繼續寫入檔案。"):
            return
        self.stop_requested = True
        self.root.destroy()


def main() -> None:
    enable_dpi()
    try:
        jmcomic.disable_jm_log()
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
