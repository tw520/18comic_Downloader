# 禁漫天堂下載器

從禁漫天堂下載漫畫。每一話存成一個資料夾，名稱是「話數_章節名稱」。

## 需要準備

- Windows
- Python 3.10 或以上
- 可以連上 [18comic.vip](https://18comic.vip/)

## 安裝

在這個資料夾開啟終端機，執行：

```bat
pip install -r requirements.txt
```

## 視窗介面

雙擊 `下載.bat`，或執行：

```bat
pythonw -X utf8 download_gui.py
```

1. 貼上漫畫網址。整部用 `/album/編號`，只想下某一話可以貼 `/photo/編號`。
2. 按「讀取目錄」。畫面會顯示漫畫名稱、作者和章節。貼某一話的網址時，只會勾選那一話。
3. 「儲存位置」預設是程式旁邊的 `downloads`。按「瀏覽」可以改到別的資料夾。選定後會記在本機的 `settings.json`。
4. 勾選要下載的章節，按「開始下載」。
5. 已下載的圖片再執行一次會跳過。按「停止」會在目前這一話完成後停下。
6. 「開啟資料夾」會打開儲存位置。

## 命令列

```bat
python download.py
python download.py "https://18comic.vip/album/編號/"
python download.py "https://18comic.vip/album/編號/" --start 1 --end 3
python download.py "網址" --output D:\comics
```

- 不帶網址時，程式會請你貼上。
- 只填數字時，會當成漫畫編號。
- `--start` 是從第幾話開始，由 1 起算。
- `--end` 是下載到第幾話，包含這一話。省略時會下到最後一話。
- `--output` 是下載根目錄，預設是程式旁邊的 `downloads`。

進度會寫到程式旁邊的 `download.log`。沒有安裝 `rich` 時仍可下載，只是不顯示進度條。

## 儲存方式

```text
下載根目錄 / 漫畫名稱 / 001_章節名稱 / 圖片
```

章節沒有獨立標題，或標題就等於漫畫名時，資料夾會命名為「第N話」。
