"""
converter —— 单文件转换流程编排

解密 -> （按需）重编码 -> 写标签 -> 落盘
把 ncmcore 和 transcoder 串起来，供 GUI 的线程池调用。
"""

import os
import shutil
import tempfile
import traceback

from ncmcore import NCMFile, NCMError
from transcoder import Transcoder, write_tags, sanitize_filename, find_ffmpeg

__all__ = ["convert_one", "resolve_output_path", "ConversionResult"]

LYRIC_EXT = ".lrc"


class ConversionResult:
    __slots__ = ("src", "dst", "source_format", "target_format",
                 "ok", "message", "skipped")

    def __init__(self, src, dst="", source_format="", target_format="",
                 ok=False, message="", skipped=False):
        self.src = src
        self.dst = dst
        self.source_format = source_format
        self.target_format = target_format
        self.ok = ok
        self.message = message
        self.skipped = skipped


def _read_lyrics(ncm_path, embed_lyrics):
    if not embed_lyrics:
        return ""
    base = os.path.splitext(ncm_path)[0] + LYRIC_EXT
    if not os.path.isfile(base):
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            with open(base, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            break
    return ""


def resolve_output_path(ncm_path, out_dir, target_format, naming, meta):
    stem = os.path.splitext(os.path.basename(ncm_path))[0]
    if naming == "meta" and (meta.get("title") or meta.get("artist")):
        title = meta.get("title") or stem
        artist = meta.get("artist") or ""
        stem = sanitize_filename("%s - %s" % (title, artist) if artist else title)
    else:
        stem = sanitize_filename(stem)
    return os.path.join(out_dir, stem + "." + target_format)


def convert_one(ncm_path, out_dir, target, mp3_bitrate="320k",
                flac_level="5", naming="original", embed_lyrics=True,
                overwrite=False, transcoder=None, progress=None):
    """
    转换单个 .ncm。

    target: "auto" | "flac" | "mp3"
    返回 ConversionResult。
    """
    transcoder = transcoder or Transcoder(find_ffmpeg())
    result = ConversionResult(ncm_path)

    tmp_dir = None
    try:
        ncm = NCMFile(ncm_path)
    except NCMError as exc:
        result.message = str(exc)
        return result
    except OSError as exc:
        result.message = "无法读取文件：%s" % exc
        return result
    except Exception as exc:  # pragma: no cover
        result.message = "解析失败（%s）" % type(exc).__name__
        return result

    try:
        result.source_format = ncm.source_format
        real_target = target if target in ("flac", "mp3") else ncm.source_format
        result.target_format = real_target

        meta = {"title": ncm.title, "artist": ncm.artist, "album": ncm.album}

        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            result.message = "无法创建输出目录：%s" % exc
            return result

        dst = resolve_output_path(ncm_path, out_dir, real_target, naming, meta)
        result.dst = dst

        if os.path.exists(dst) and not overwrite:
            result.skipped = True
            result.ok = True
            result.message = "已存在，跳过"
            return result

        tmp_dir = tempfile.mkdtemp(prefix="ncmdump_")
        raw = os.path.join(tmp_dir, "raw." + ncm.source_format)
        ncm.dump(raw, progress=progress)

        lyrics = _read_lyrics(ncm_path, embed_lyrics)

        if real_target == ncm.source_format:
            # 源格式与目标格式一致：直接落盘，不做任何重编码
            tmp_out = os.path.join(tmp_dir, "out." + real_target)
            shutil.copyfile(raw, tmp_out)
            note = "直接提取（未重编码）"
        else:
            tmp_out = os.path.join(tmp_dir, "out." + real_target)
            transcoder.transcode(raw, tmp_out, real_target,
                                 mp3_bitrate, flac_level)
            note = "已重编码"

        tag_err = write_tags(tmp_out, real_target, meta,
                             ncm.image_data, ncm.image_mime, lyrics)

        shutil.move(tmp_out, dst)
        result.ok = True
        result.message = note + ("（标签写入失败：%s）" % tag_err if tag_err else "")
        return result

    except Exception as exc:
        result.message = "%s：%s" % (type(exc).__name__, exc)
        return result
    finally:
        ncm.close()
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
