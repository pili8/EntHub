"""macOS 原生 Toast 浮窗（基于 PyObjC）

不依赖系统通知权限，在屏幕正中显示一个紧凑的半透明圆角浮窗。

使用方法：
    from toast import show_toast
    show_toast("已标注 3 个号码", type="success")

注意：必须在 macOS 主线程调用（rumps 回调中是主线程，可以直接用）。
"""
import objc
from AppKit import (
    NSWindow, NSView, NSTextField, NSColor, NSFont,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSScreen, NSAnimationContext,
    NSBezierPath,
)
from Foundation import NSObject, NSRect, NSPoint, NSSize, NSTimer


# Toast 类型对应的颜色
TOAST_COLORS = {
    "success": (0.09, 0.64, 0.29),   # #16A34A 绿
    "warning": (0.71, 0.33, 0.04),   # #B45309 橙黄
    "error":   (0.76, 0.25, 0.05),   # #C2410C 红橙
    "info":    (0.85, 0.47, 0.34),   # #D97757 赤陶
}


def _calc_size(text):
    """根据文本长度计算窗口尺寸"""
    # 粗略估算：每个字约 7.5px（13px 字体）
    text_width = max(len(text) * 8, 80)
    width = min(text_width + 32, 400)  # 加 padding，最大 400
    height = 36
    return width, height


class _ToastView(NSView):
    """自定义视图：画圆角半透明背景"""

    def initWithFrame_color_(self, frame, color):
        self = objc.super(_ToastView, self).initWithFrame_(frame)
        if self:
            self._bg_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(
                color[0], color[1], color[2], 0.95
            )
        return self

    def drawRect_(self, rect):
        self._bg_color.set()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, 8, 8
        )
        path.fill()


class _ToastController(NSObject):
    """管理 Toast 窗口的显示和定时关闭"""

    def showToast_text_color_duration_(self, _, text, color_name, duration):
        """在主线程调用：创建并显示 toast（单行，居中）"""
        color = TOAST_COLORS.get(color_name, TOAST_COLORS["info"])

        # 计算窗口尺寸
        width, height = _calc_size(text)

        # 屏幕顶部居中（避开状态栏，留 12pt 间距）
        screen = NSScreen.mainScreen()
        screen_frame = screen.frame()
        x = (screen_frame.size.width - width) / 2
        y = screen_frame.size.height - height - 28  # 状态栏高度约 22pt

        # 创建窗口
        frame = NSRect(NSPoint(x, y), NSSize(width, height))
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        window.setLevel_(NSFloatingWindowLevel)        # 浮在所有窗口之上
        window.setOpaque_(False)
        window.setBackgroundColor_(NSColor.clearColor())
        window.setAlphaValue_(0.0)
        window.setIgnoresMouseEvents_(True)            # 鼠标穿透
        window.setHasShadow_(True)
        # 加入所有空格（多桌面都能看到）
        window.setCollectionBehavior_(1 << 4)          # NSWindowCollectionBehaviorCanJoinAllSpaces
        window.orderFrontRegardless()

        # 自定义内容视图（圆角背景）
        view = _ToastView.alloc().initWithFrame_color_(
            NSRect(NSPoint(0, 0), NSSize(width, height)), color
        )
        window.setContentView_(view)

        # 单行文本（居中显示）
        text_field = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(8, (height - 16) / 2), NSSize(width - 16, 16))
        )
        text_field.setStringValue_(text)
        text_field.setTextColor_(NSColor.whiteColor())
        text_field.setFont_(NSFont.boldSystemFontOfSize_(13))
        text_field.setAlignment_(2)  # NSCenterTextAlignment
        text_field.setBezeled_(False)
        text_field.setDrawsBackground_(False)
        text_field.setEditable_(False)
        text_field.setSelectable_(False)
        view.addSubview_(text_field)

        # 淡入动画（0.2 秒）
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.2)
        window.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

        # 定时淡出关闭
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            duration, self, "fadeOut:", window, False
        )

    def fadeOut_(self, timer):
        """淡出动画后关闭窗口"""
        window = timer.userInfo()
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setCompletionHandler_(
            lambda: window.orderOut_(None)
        )
        NSAnimationContext.currentContext().setDuration_(0.25)
        window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()


# 单例控制器
_controller = None


def _get_controller():
    """获取单例控制器"""
    global _controller
    if _controller is None:
        _controller = _ToastController.alloc().init()
    return _controller


def show_toast(text, duration=1.8, type="info"):
    """显示一个紧凑的 toast 浮窗（屏幕居中，单行）

    Args:
        text: 显示的文本（必填）
        duration: 显示时长（秒），默认 1.8 秒
        type: 类型，决定颜色（success/warning/error/info）

    注意：必须在主线程调用（rumps 回调中是主线程）。
    """
    controller = _get_controller()
    controller.showToast_text_color_duration_(
        None, text, type, duration
    )
