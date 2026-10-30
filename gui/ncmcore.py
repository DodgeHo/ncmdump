"""
ncmcore —— 网易云音乐 .ncm 解密核心

算法移植自 taurusxin/ncmdump (src/ncmcrypt.cpp, MIT License)。
原项目用 C++ + taglib 实现，这里改为纯 Python，
自带 AES-128-ECB 实现（解密量极小，无需任何第三方加密库）。

.ncm 文件结构
------------
    magic           8 bytes   0x4e455443 0x4d414446 (小端)
    gap             2 bytes
    key_len         4 bytes (uint32 LE)
    key_data        key_len bytes, 每字节 XOR 0x64
                    -> AES-128-ECB(core_key) 解密
                    -> 前 17 字节是 "neteasecloudmusic"，其余为 RC4 种子
    meta_len        4 bytes (uint32 LE)
    meta_data       meta_len bytes, 每字节 XOR 0x63
                    -> 去掉前 22 字节("163 key(Don't modify):")
                    -> base64 解码
                    -> AES-128-ECB(modify_key) 解密
                    -> 去掉前 6 字节("music:") -> JSON
    gap             5 bytes (crc32 + 版本号)
    cover_frame_len 4 bytes
    image_len       4 bytes
    image_data      image_len bytes
    gap             cover_frame_len - image_len bytes
    audio           ... 剩余部分，用 keybox 流解密
"""

import base64
import json
import struct

__all__ = ["NCMFile", "NCMError", "aes128_ecb_decrypt", "build_keybox"]

CORE_KEY = bytes(
    [0x68, 0x7A, 0x48, 0x52, 0x41, 0x6D, 0x73, 0x6F,
     0x35, 0x6B, 0x49, 0x6E, 0x62, 0x61, 0x78, 0x57]
)  # "hzHRAmso5kInbaxW"

MODIFY_KEY = bytes(
    [0x23, 0x31, 0x34, 0x6C, 0x6A, 0x6B, 0x5F, 0x21,
     0x5C, 0x5D, 0x26, 0x30, 0x55, 0x3C, 0x27, 0x28]
)  # "#14ljk_!\]&0U<'("

_MAGIC_1 = 0x4E455443
_MAGIC_2 = 0x4D414446
_CHUNK = 0x8000  # 32768，与源码一致；256 的整数倍，流密钥可整块对齐


class NCMError(Exception):
    """ncm 文件损坏或不是网易云加密文件。"""


# --------------------------------------------------------------------------
# AES-128
# --------------------------------------------------------------------------

def _build_aes_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        # 乘以生成元 3
        x = (((x << 1) ^ (0x1B if x & 0x80 else 0)) & 0xFF) ^ x
    for i in range(255, 512):
        exp[i] = exp[i - 255]

    sbox = [0] * 256
    inv_sbox = [0] * 256
    for i in range(256):
        inv = 0 if i == 0 else exp[255 - log[i]]
        s = inv
        r = inv
        for _ in range(4):
            r = ((r << 1) | (r >> 7)) & 0xFF
            s ^= r
        s ^= 0x63
        sbox[i] = s
        inv_sbox[s] = i
    return sbox, inv_sbox, exp, log


_SBOX, _INV_SBOX, _EXP, _LOG = _build_aes_tables()

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _expand_key(key):
    """AES-128 密钥扩展，返回 11 个 16 字节轮密钥。"""
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]                     # RotWord
            t = [_SBOX[b] for b in t]             # SubWord
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    rounds = []
    for r in range(11):
        rk = []
        for j in range(4):
            rk.extend(w[4 * r + j])
        rounds.append(rk)
    return rounds


def _decrypt_block(rounds, block):
    s = [block[i] ^ rounds[10][i] for i in range(16)]

    for rnd in range(9, 0, -1):
        # InvShiftRows：第 r 行循环右移 r
        t = [0] * 16
        for c in range(4):
            for r in range(1, 4):
                t[r + 4 * c] = s[r + 4 * ((c - r) % 4)]
            t[0 + 4 * c] = s[0 + 4 * c]
        # InvSubBytes
        s = [_INV_SBOX[b] for b in t]
        # AddRoundKey
        rk = rounds[rnd]
        s = [s[i] ^ rk[i] for i in range(16)]
        # InvMixColumns
        t = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
            t[4 * c + 0] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
            t[4 * c + 1] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
            t[4 * c + 2] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
            t[4 * c + 3] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)
        s = t

    # 最后一轮：InvShiftRows + InvSubBytes + AddRoundKey
    t = [0] * 16
    for c in range(4):
        for r in range(1, 4):
            t[r + 4 * c] = s[r + 4 * ((c - r) % 4)]
        t[0 + 4 * c] = s[0 + 4 * c]
    s = [_INV_SBOX[b] for b in t]
    return bytes(s[i] ^ rounds[0][i] for i in range(16))


def aes128_ecb_decrypt(key, src):
    """AES-128-ECB 解密，末尾按源码逻辑去掉 PKCS#7 填充。"""
    rounds = _expand_key(key)
    n = len(src) >> 4
    if n <= 0:
        return b""
    out = bytearray()
    for i in range(n - 1):
        out += _decrypt_block(rounds, src[i * 16:(i + 1) * 16])
    last = _decrypt_block(rounds, src[(n - 1) * 16:n * 16])
    pad = last[15]
    if pad > 16:
        pad = 0
    out += last[:16 - pad]
    return bytes(out)


# --------------------------------------------------------------------------
# RC4-like keybox
# --------------------------------------------------------------------------

def build_keybox(key):
    box = list(range(256))
    last_byte = 0
    key_offset = 0
    klen = len(key)
    for i in range(256):
        swap = box[i]
        c = (swap + last_byte + key[key_offset]) & 0xFF
        key_offset += 1
        if key_offset >= klen:
            key_offset = 0
        box[i] = box[c]
        box[c] = swap
        last_byte = c
    return box


def _stream_key(box):
    """
    源码中音频解密为：
        j = (i + 1) & 0xff
        buf[i] ^= box[(box[j] + box[(box[j] + j) & 0xff]) & 0xff]
    i 只通过 (i+1)&0xff 参与，因此流密钥周期为 256 字节，可预先展开。
    """
    tbl = bytearray(256)
    for i in range(256):
        j = (i + 1) & 0xFF
        bj = box[j]
        tbl[i] = box[(bj + box[(bj + j) & 0xFF]) & 0xFF]
    return bytes(tbl)


def _xor_bytes(data, key):
    a = int.from_bytes(data, "big")
    b = int.from_bytes(key, "big")
    return (a ^ b).to_bytes(len(data), "big")


# --------------------------------------------------------------------------
# ncm 文件
# --------------------------------------------------------------------------

class NCMFile:
    """一个 .ncm 文件的解析器。"""

    def __init__(self, path):
        self.path = path
        self.title = ""
        self.artist = ""
        self.album = ""
        self.bitrate = 0        # bps
        self.duration = 0       # ms
        self.meta_format = ""   # 元数据里声明的容器格式
        self.image_data = b""
        self.image_mime = ""
        self.source_format = ""  # 实际音频格式：mp3 / flac
        self._box = None
        self._stream = None
        self.audio_offset = 0
        self._f = None
        self._parse()

    # -- 内部 ---------------------------------------------------------------

    def _read(self, n):
        data = self._f.read(n)
        if len(data) != n:
            raise NCMError("文件意外结束（损坏的 ncm）")
        return data

    def _u32(self):
        return struct.unpack("<I", self._read(4))[0]

    def _parse(self):
        self._f = open(self.path, "rb")

        if self._u32() != _MAGIC_1 or self._u32() != _MAGIC_2:
            self._f.close()
            raise NCMError("不是网易云加密文件（缺少 CTENFDAM 文件头）")

        self._f.read(2)  # gap

        n = self._u32()
        if n <= 0:
            raise NCMError("密钥段为空，文件已损坏")
        keydata = bytes(b ^ 0x64 for b in self._read(n))
        keydata = aes128_ecb_decrypt(CORE_KEY, keydata)
        if len(keydata) <= 17:
            raise NCMError("密钥解密失败")
        self._box = build_keybox(keydata[17:])
        self._stream = _stream_key(self._box)

        n = self._u32()
        if n > 0:
            modify = bytes(b ^ 0x63 for b in self._read(n))
            try:
                blob = base64.b64decode(modify[22:])
                plain = aes128_ecb_decrypt(MODIFY_KEY, blob)
                meta = json.loads(plain[6:].decode("utf-8", "ignore"))
            except Exception:
                meta = None
            if isinstance(meta, dict):
                self._load_meta(meta)

        self._f.read(5)  # crc32 + 图片版本号

        cover_frame_len = self._u32()
        img_len = self._u32()
        if img_len > 0:
            self.image_data = self._read(img_len)
            self.image_mime = self._detect_mime(self.image_data)
            self._f.read(cover_frame_len - img_len)
        else:
            self._f.read(cover_frame_len)

        self.audio_offset = self._f.tell()

        first = self._decrypt_chunk(self._f.read(3))
        self.source_format = "mp3" if first[:3] == b"ID3" else "flac"
        self._f.seek(self.audio_offset)

        if not self.meta_format:
            self.meta_format = self.source_format

    @staticmethod
    def _detect_mime(data):
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        return "image/jpeg"

    def _load_meta(self, meta):
        self.title = str(meta.get("musicName") or "")
        self.album = str(meta.get("album") or "")
        artists = meta.get("artist")
        names = []
        if isinstance(artists, list):
            for item in artists:
                if isinstance(item, list) and item:
                    names.append(str(item[0]))
                elif isinstance(item, str):
                    names.append(item)
        self.artist = "/".join(names)
        try:
            self.bitrate = int(meta.get("bitrate") or 0)
        except (TypeError, ValueError):
            self.bitrate = 0
        try:
            self.duration = int(meta.get("duration") or 0)
        except (TypeError, ValueError):
            self.duration = 0
        self.meta_format = str(meta.get("format") or "").lower()

    def _decrypt_chunk(self, chunk):
        if not chunk:
            return b""
        reps = -(-len(chunk) // 256)  # 向上取整
        return _xor_bytes(chunk, (self._stream * reps)[:len(chunk)])

    # -- 公开 ---------------------------------------------------------------

    def dump(self, out_path, progress=None):
        """解密音频流写入 out_path。progress(count, total) 可选回调。"""
        import shutil

        total = 0
        try:
            import os
            total = os.path.getsize(self.path) - self.audio_offset
        except OSError:
            pass

        self._f.seek(self.audio_offset)
        done = 0
        with open(out_path, "wb") as out:
            while True:
                chunk = self._f.read(_CHUNK)
                if not chunk:
                    break
                out.write(self._decrypt_chunk(chunk))
                done += len(chunk)
                if progress:
                    progress(done, total)
        return out_path

    def close(self):
        if self._f:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- 便捷属性 -----------------------------------------------------------

    @property
    def duration_text(self):
        if not self.duration:
            return "-"
        sec = self.duration // 1000
        return "%d:%02d" % (sec // 60, sec % 60)

    @property
    def bitrate_kbps(self):
        return self.bitrate // 1000 if self.bitrate else 0
