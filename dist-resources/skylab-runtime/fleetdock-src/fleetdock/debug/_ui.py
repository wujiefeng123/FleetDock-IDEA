"""调试图形界面共用的中文文本和 Windows 窗口适配。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
from typing import Any, Sequence


logger = logging.getLogger(__name__)

_TextItem = tuple[str, int, int, tuple[int, int, int], float]


class _DebugTextRenderer:
    """使用 Pillow 和系统 CJK 字体向 OpenCV BGR 画布绘制 Unicode 文本。"""

    def __init__(self, numpy_module: Any) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError('中文调试界面需要 Pillow，请使用 "fleetdock[vision]" 安装。') from exc
        self._np = numpy_module
        self._image = Image
        self._draw = ImageDraw
        self._font_module = ImageFont
        self._font_path = self._find_chinese_font()
        self._fonts: dict[int, Any] = {}

    def render(self, canvas: Any, items: Sequence[_TextItem]) -> None:
        """把文本项一次性绘制到 ``canvas``，减少每帧图像格式转换。"""
        image = self._image.fromarray(canvas)
        draw = self._draw.Draw(image)
        for text, x, y, color, scale in items:
            draw.text((x, y), text, font=self.font(scale), fill=color, anchor="ls")
        canvas[:] = self._np.asarray(image)

    def font(self, scale: float) -> Any:
        """按 OpenCV 风格缩放值返回缓存的系统中文字体。"""
        size = max(12, int(round(scale * 28)))
        font = self._fonts.get(size)
        if font is None:
            font = self._font_module.truetype(str(self._font_path), size=size)
            self._fonts[size] = font
        return font

    def text_bbox(self, text: str, scale: float) -> tuple[int, int, int, int]:
        """返回以左侧基线为锚点的文字边界。"""
        left, top, right, bottom = self.font(scale).getbbox(text, anchor="ls")
        return int(left), int(top), int(right), int(bottom)

    @staticmethod
    def _find_chinese_font() -> Path:
        candidates = (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RuntimeError("未找到中文字体，请安装微软雅黑、苹方或 Noto Sans CJK。")


def _use_physical_pixels_on_windows() -> Any:
    """让当前 GUI 线程使用物理像素，避免高 DPI 缩放破坏窗口尺寸和鼠标坐标。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        setter = ctypes.windll.user32.SetThreadDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        return setter(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        logger.debug("当前 Windows 版本不支持线程级高 DPI 上下文", exc_info=True)
        return None


def _restore_dpi_context_on_windows(previous_context: Any) -> None:
    """恢复打开调试窗口前的 Windows 线程 DPI 上下文。"""
    if sys.platform != "win32" or previous_context is None:
        return
    try:
        import ctypes

        setter = ctypes.windll.user32.SetThreadDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        setter(ctypes.c_void_p(previous_context))
    except (AttributeError, OSError, TypeError):
        logger.debug("恢复 Windows 线程高 DPI 上下文失败", exc_info=True)


def _set_unicode_window_title_on_windows(title: str) -> None:
    """修正 OpenCV HighGUI 在 Windows 下对中文窗口标题的错误编码。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def update_title(window: int, parameter: int) -> bool:
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
            if process_id.value != os.getpid():
                return True
            class_name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(window, class_name, len(class_name))
            if "HighGUI" not in class_name.value:
                return True
            user32.SetWindowTextW(window, title)
            return False

        user32.EnumWindows(callback_type(update_title), 0)
    except (AttributeError, OSError, TypeError):
        logger.debug("设置 Windows 中文窗口标题失败", exc_info=True)
