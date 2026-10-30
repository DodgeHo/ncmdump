"""
app —— 程序入口
"""

import os
import sys

# PyInstaller 打包后，保证同目录模块可被导入
if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(sys.executable))
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # Windows 下让任务栏图标正确显示
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ncmdump.gui")
    except Exception:
        pass

    from gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
