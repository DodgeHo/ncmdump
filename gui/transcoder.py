"""
transcoder —— 转码（FLAC / MP3）与标签写入

解密出来的原始音频已经是 FLAC 或 MP3。这里负责：
  1. 目标格式 == 源格式  -> 直接复制，绝不重编码（避免无谓的音质损失）
  2. 目标格式 != 源格式  -> 调用内置的静态 ffmpeg 重编码
  3. 用 mutagen 写回标题 / 艺术家 / 专辑 / 封面 / 歌词
"""

import os
import re
import shutil
import struct
import subprocess
import tempfile

__all__ = ["Transcoder", "sanitize_filename", "find_ffmpeg"]

MP3_BITRATES = ["320k", "256k", "192k", "128k"]
FLAC_LEVELS = [str(i) for i in range(12)]  # 0-11，ffmpeg flac 压缩级别

_INVALID = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize_filename(name, maxlen=120):
    """把元数据拼成的名字清洗成合法文件名。"""
    name = _INVALID.sub("_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "untitled"
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(" .")
    return name


def _resource_dir():
    """PyInstaller 打包后的临时资源目录，或源码目录。"""
    base = getattr(__import__("sys"), "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg():
    """
    查找 ffmpeg，优先级：
      1. exe 同级目录的 ffmpeg.exe（用户可自行替换成完整版）
      2. 打包内置的 ffmpeg.exe
      3. 系统 PATH 里的 ffmpeg
    """
    import sys

    candidates = []
    here = os.path.dirname(os.path.abspath(__file__) or os.getcwd())
    candidates.append(os.path.join(here, "ffmpeg.exe"))
    candidates.append(os.path.join(os.path.dirname(here), "ffmpeg.exe"))
    try:
        exedir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exedir, "ffmpeg.exe"))
    except Exception:
        pass
    candidates.append(os.path.join(_resource_dir(), "ffmpeg.exe"))

    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffmpeg") or ""


# --------------------------------------------------------------------------
# 图片尺寸（避免为了读宽高引入 Pillow）
# --------------------------------------------------------------------------

def _image_size(data):
    if not data:
        return 0, 0, 0
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        try:
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h), 24
        except struct.error:
            return 0, 0, 24
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            try:
                seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
            except struct.error:
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                try:
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h), 24
                except struct.error:
                    break
            if seglen <= 0:
                break
            i += 2 + seglen
        return 0, 0, 24
    return 0, 0, 24


# --------------------------------------------------------------------------
# 标签写入
# --------------------------------------------------------------------------

def _write_flac_tags(path, meta, image, image_mime, lyrics):
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    tags = audio.tags
    if tags is None:
        audio.add_tags()
        tags = audio.tags

    if meta.get("title"):
        tags["TITLE"] = meta["title"]
    if meta.get("artist"):
        tags["ARTIST"] = meta["artist"]
    if meta.get("album"):
        tags["ALBUM"] = meta["album"]

    if lyrics:
        tags["LYRICS"] = lyrics
        tags["UNSYNCEDLYRICS"] = lyrics

    if image:
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = image_mime or "image/jpeg"
        pic.desc = "Cover"
        w, h, depth = _image_size(image)
        pic.width, pic.height, pic.depth = w, h, depth
        pic.data = image
        audio.clear_pictures()
        audio.add_picture(pic)

    audio.save()


def _write_mp3_tags(path, meta, image, image_mime, lyrics):
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, APIC, USLT

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    if meta.get("title"):
        tags.setall("TIT2", [TIT2(encoding=3, text=meta["title"])])
    if meta.get("artist"):
        tags.setall("TPE1", [TPE1(encoding=3, text=meta["artist"])])
    if meta.get("album"):
        tags.setall("TALB", [TALB(encoding=3, text=meta["album"])])

    if lyrics:
        tags.setall("USLT", [USLT(encoding=3, lang="chi", desc="", text=lyrics)])

    if image:
        tags.setall("APIC", [APIC(encoding=3,
                                  mime=image_mime or "image/jpeg",
                                  type=3, desc="Cover",
                                  data=image)])
    try:
        MP3(path)
    except Exception:
        pass
    tags.save(path, v2_version=3)


def write_tags(path, target_format, meta, image=b"", image_mime="", lyrics=""):
    """按目标格式写标签。失败不致命，返回错误信息字符串或 None。"""
    try:
        if target_format == "flac":
            _write_flac_tags(path, meta, image, image_mime, lyrics)
        else:
            _write_mp3_tags(path, meta, image, image_mime, lyrics)
    except Exception as exc:  # pragma: no cover
        return "%s: %s" % (type(exc).__name__, exc)
    return None


# --------------------------------------------------------------------------
# ffmpeg 调用
# --------------------------------------------------------------------------

def _creation_flags():
    if os.name == "nt":
        return 0x08000000  # CREATE_NO_WINDOW，避免弹出黑框
    return 0


class Transcoder:
    def __init__(self, ffmpeg_path=None):
        self.ffmpeg_path = ffmpeg_path or find_ffmpeg()

    def require_ffmpeg(self):
        if not self.ffmpeg_path or not os.path.isfile(self.ffmpeg_path):
            raise RuntimeError(
                "找不到 ffmpeg.exe。请把 ffmpeg.exe 放到本程序所在目录，"
                "或安装 ffmpeg 并加入 PATH 后重试。"
            )

    def build_cmd(self, src, dst, target, mp3_bitrate="320k", flac_level="5"):
        cmd = [self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
               "-i", src, "-vn", "-map_metadata", "-1"]
        if target == "flac":
            cmd += ["-c:a", "flac", "-compression_level", str(flac_level)]
        else:
            cmd += ["-c:a", "libmp3lame", "-b:a", mp3_bitrate]
        cmd += [dst]
        return cmd

    def transcode(self, src, dst, target, mp3_bitrate="320k", flac_level="5",
                  timeout=3600):
        self.require_ffmpeg()
        cmd = self.build_cmd(src, dst, target, mp3_bitrate, flac_level)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              timeout=timeout,
                              creationflags=_creation_flags())
        if proc.returncode != 0:
            tail = (proc.stdout or b"").decode("utf-8", "ignore").strip()[-500:]
            raise RuntimeError("ffmpeg 失败 (code %d)%s"
                               % (proc.returncode, "\n" + tail if tail else ""))
        return dst
