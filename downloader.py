import os
import requests
import yt_dlp

MAX_TELEGRAM_UPLOAD = 49 * 1024 * 1024  # stay safely under Telegram's 50MB bot upload limit


def try_ytdlp_download(url: str, tmp_dir: str):
    """Try downloading with yt-dlp (works for YouTube, Instagram, TikTok, Twitter, etc.)."""
    ydl_opts = {
        "outtmpl": os.path.join(tmp_dir, "%(title).80s.%(ext)s"),
        "format": "best[filesize<50M]/best",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return filename


def try_direct_download(url: str, tmp_dir: str):
    """Fallback: treat the link as a direct file URL."""
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()

    content_length = int(resp.headers.get("content-length", 0))
    if content_length and content_length > MAX_TELEGRAM_UPLOAD:
        raise ValueError("File is too large for Telegram (over 50MB).")

    filename = url.split("/")[-1].split("?")[0] or "downloaded_file"
    filepath = os.path.join(tmp_dir, filename)

    size = 0
    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            size += len(chunk)
            if size > MAX_TELEGRAM_UPLOAD:
                raise ValueError("File is too large for Telegram (over 50MB).")
            f.write(chunk)

    return filepath


def download_media(url: str, tmp_dir: str):
    """Try yt-dlp first, fall back to direct download. Returns the local file path."""
    try:
        return try_ytdlp_download(url, tmp_dir)
    except Exception:
        return try_direct_download(url, tmp_dir)
