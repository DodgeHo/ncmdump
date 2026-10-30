"""
build —— 打包脚本

用法：
    python build.py              # 目录版（推荐，启动快）
    python build.py --onefile    # 单 exe 版（体积约 100MB，每次启动需解包，约慢数秒）

需要一个"单文件静态版" ffmpeg.exe。脚本会依次尝试：
    1. imageio-ffmpeg 自带的静态 ffmpeg（pip install imageio-ffmpeg）
    2. 系统 PATH 中的 ffmpeg
找到后复制到 _vendor/ffmpeg.exe 并一起打包。
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def locate_ffmpeg():
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="打包成单个 exe")
    args = ap.parse_args()

    ff = locate_ffmpeg()
    if not ff:
        sys.exit("找不到 ffmpeg.exe。请先 pip install imageio-ffmpeg，"
                 "或把静态版 ffmpeg 放进 PATH。")

    vendor = os.path.join(ROOT, "_vendor")
    os.makedirs(vendor, exist_ok=True)
    dst = os.path.join(vendor, "ffmpeg.exe")
    if os.path.abspath(ff) != os.path.abspath(dst):
        shutil.copyfile(ff, dst)
    print("ffmpeg: %s (%.1f MB)" % (ff, os.path.getsize(dst) / 1048576))

    name = "ncmdump-gui"
    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--onefile" if args.onefile else "--onedir",
           "--windowed",
           "--name", name,
           "--icon", os.path.join(ROOT, "icon.ico"),
           "--add-binary", os.path.join("_vendor", "ffmpeg.exe") + ";.",
           "--distpath", os.path.join(ROOT, "dist"),
           "--workpath", os.path.join(ROOT, "build"),
           "--specpath", ROOT,
           os.path.join(ROOT, "app.py")]

    print(" ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        sys.exit(rc)

    out = os.path.join(ROOT, "dist", name)
    print("\n完成：", out + (".exe" if args.onefile else ""))


if __name__ == "__main__":
    main()
