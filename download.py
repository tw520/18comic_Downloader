"""禁漫天堂漫畫下載器。

用法：
  python download.py
  python download.py "https://18comic.vip/album/*******/..."
  python download.py "網址" --start 1 --end 3
  python download.py "網址" --output D:\\comics
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import jmcomic
from jmcomic import (
    JmAlbumDetail,
    JmPhotoDetail,
    JmcomicException,
    MissingAlbumPhotoException,
    PartialDownloadFailedException,
    RequestRetryAllFailException,
    fix_windir_name,
)
from jmcomic.jm_plugin import DownloadProgressPlugin


ALBUM_RE = re.compile(r"/album/(\d+)", re.IGNORECASE)
PHOTO_RE = re.compile(r"/photo/(\d+)", re.IGNORECASE)
SCRIPT_DIR = Path(__file__).resolve().parent


def _keep_website_chapter_order(episode_list: list) -> list:
    """保留網站上的章節順序。

    套件原本會把 sort 相同的章節當成重複而刪掉。禁漫允許不同章節共用同一個 sort，
    網頁仍把它們分成獨立的一話，刪掉之後目錄就會少話，後面的編號也會往前擠。
    """
    seen: set[str] = set()
    kept = []
    for episode in episode_list:
        photo_id = str(episode[0])
        if photo_id in seen:
            continue
        seen.add(photo_id)
        kept.append(episode)
    return kept


JmAlbumDetail.distinct_episode = staticmethod(_keep_website_chapter_order)


def _chapter_index_and_title(photo) -> tuple[int, str]:
    """編號用網站章節順序，標題用章節名稱，不用章節頁自己的長標題。"""
    album = getattr(photo, "from_album", None)
    photo_id = str(getattr(photo, "photo_id", ""))
    episodes = getattr(album, "episode_list", None) if album is not None else None
    if episodes:
        for index, episode in enumerate(episodes, start=1):
            if str(episode[0]) == photo_id:
                return index, str(episode[2] or "").strip()
    return int(photo.album_index), (getattr(photo, "name", "") or "").strip()


def _photo_folder_name(self) -> str:
    """001_章節名稱。沒有獨立標題、或標題就等於漫畫名時，改為「第N話」。"""
    index, title = _chapter_index_and_title(self)
    album = getattr(self, "from_album", None)
    album_title = (getattr(album, "name", "") or "").strip() if album is not None else ""
    if not title or title == album_title:
        title = f"第{index}話"
    return f"{index:03d}_{title}"


JmPhotoDetail.folder_name = property(_photo_folder_name)


def setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def parse_target(text: str) -> tuple[str, str]:
    raw = text.strip().strip('"').strip("'")
    if not raw:
        raise ValueError("請輸入漫畫網址或編號")

    album = ALBUM_RE.search(raw)
    if album:
        return "album", album.group(1)

    photo = PHOTO_RE.search(raw)
    if photo:
        return "photo", photo.group(1)

    if raw.isdigit():
        return "album", raw

    raise ValueError("認不出這段網址。請貼上 /album/編號 或 /photo/編號 的頁面網址")


def build_option(base_dir: Path) -> jmcomic.JmOption:
    return jmcomic.JmOption.construct(
        {
            "dir_rule": {
                "base_dir": str(base_dir),
                "rule": "Bd / {Atitle} / {Pfolder_name}",
            },
            "download": {
                "cache": True,
                "image": {
                    "decode": True,
                    "suffix": None,
                },
                "threading": {
                    "image": 10,
                    "photo": 3,
                },
            },
            "client": {
                "impl": "api",
                "retry_times": 5,
                "postman": {
                    "type": "curl_cffi",
                    "meta_data": {
                        "impersonate": "chrome",
                        "verify": False,
                    },
                },
            },
        }
    )


def enable_progress(option: jmcomic.JmOption, log_file: Path) -> None:
    try:
        import rich  # noqa: F401
    except ImportError:
        print("未安裝 rich，將不顯示進度條。可執行：pip install rich")
        return
    DownloadProgressPlugin.build(option).invoke(log_file=str(log_file))


def chapter_slice(photos: list, start: int, end: int | None) -> list:
    total = len(photos)
    if start < 1 or start > total:
        raise ValueError(f"起始話數必須介於 1 到 {total}")
    last = total if end is None else end
    if last < start or last > total:
        raise ValueError(f"結束話數必須介於 {start} 到 {total}")
    return photos[start - 1 : last]


def print_plan(album, photos: list, base_dir: Path) -> Path:
    album_dir = base_dir / fix_windir_name(str(album.name))
    print()
    print(f"漫畫：{album.name}")
    print(f"作者：{album.author}")
    print(f"話數：{len(list(album))}")
    print(f"這次下載：{len(photos)} 話")
    print(f"儲存位置：{album_dir}")
    if photos:
        print(f"第一個資料夾：{fix_windir_name(photos[0].folder_name)}")
        print(f"最後一個資料夾：{fix_windir_name(photos[-1].folder_name)}")
    print()
    return album_dir


def report_partial(exc: PartialDownloadFailedException) -> int:
    downloader = exc.downloader
    failed_photos = list(getattr(downloader, "download_failed_photo", []) or [])
    failed_images = list(getattr(downloader, "download_failed_image", []) or [])
    print("有部分內容下載失敗。已完成的圖片會保留，再執行一次會從缺的地方補。")
    if failed_photos:
        print(f"失敗章節數：{len(failed_photos)}")
    if failed_images:
        print(f"失敗圖片數：{len(failed_images)}")
    return 1


def download_album_range(album_id: str, option, start: int, end: int | None) -> int:
    client = option.new_jm_client()
    try:
        album = client.get_album_detail(album_id)
    except MissingAlbumPhotoException:
        print(f"找不到編號 {album_id} 的漫畫。")
        return 1

    photos = chapter_slice(list(album), start, end)
    album_dir = print_plan(album, photos, Path(option.dir_rule.base_dir))
    whole = start == 1 and (end is None or end == len(list(album)))

    try:
        if whole:
            jmcomic.download_album(album_id, option)
        else:
            for offset, photo in enumerate(photos, start):
                label = photo.folder_name
                print(f"[{offset}/{len(list(album))}] {label}")
                jmcomic.download_photo(photo.photo_id, option)
    except PartialDownloadFailedException as exc:
        return report_partial(exc)
    except RequestRetryAllFailException:
        print("連線失敗，站點沒有回應。請稍後再試，已下載的圖片會自動跳過。")
        return 1
    except JmcomicException as exc:
        print(f"下載中斷：{exc}")
        return 1

    print(f"下載完成：{album_dir}")
    return 0


def download_one_photo(photo_id: str, option) -> int:
    print(f"下載單一章節：{photo_id}")
    try:
        result = jmcomic.download_photo(photo_id, option)
    except MissingAlbumPhotoException:
        print(f"找不到編號 {photo_id} 的章節。")
        return 1
    except PartialDownloadFailedException as exc:
        return report_partial(exc)
    except RequestRetryAllFailException:
        print("連線失敗，站點沒有回應。請稍後再試。")
        return 1
    except JmcomicException as exc:
        print(f"下載中斷：{exc}")
        return 1

    photo = result.detail
    print(f"下載完成：{photo.save_path}")
    return 0


def ask_url() -> str:
    print("禁漫天堂下載器")
    print("貼上漫畫網址後按 Enter。例如：")
    print("https://18comic.vip/album/*******/")
    return input("網址：")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下載禁漫天堂漫畫，每話一個資料夾。")
    parser.add_argument("url", nargs="?", help="漫畫或章節網址，也可直接填本子編號")
    parser.add_argument(
        "-o",
        "--output",
        default=str(SCRIPT_DIR / "downloads"),
        help="下載根目錄，預設是程式旁邊的 downloads",
    )
    parser.add_argument("--start", type=int, default=1, help="從第幾話開始，由 1 起算")
    parser.add_argument("--end", type=int, default=None, help="下載到第幾話（含）")
    return parser.parse_args()


def main() -> int:
    setup_stdio()
    args = parse_args()
    raw = args.url if args.url else ask_url()

    try:
        kind, target_id = parse_target(raw)
    except ValueError as exc:
        print(exc)
        return 1

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    option = build_option(output_dir)
    enable_progress(option, SCRIPT_DIR / "download.log")

    try:
        if kind == "photo":
            if args.start != 1 or args.end is not None:
                print("單一章節網址會下載該話，--start / --end 不適用。")
            return download_one_photo(target_id, option)
        return download_album_range(target_id, option, args.start, args.end)
    except KeyboardInterrupt:
        print("\n已停止。再次執行會跳過已下載的圖片。")
        return 130
    except RequestRetryAllFailException:
        print("連線失敗，站點沒有回應。請稍後再試。")
        return 1
    except ValueError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
