"""macOS 原生 Toast 浮窗（基于 PyObjC）

不依赖系统通知权限，直接在屏幕右上角显示一个半透明圆角浮窗，自动淡入淡出。

使用方法：
    from toast import show_toast
    show_toast("✅ 标注完成", "已标注 3 个号码")

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
            rect, 10, 10
        )
        path.fill()


class _ToastController(NSObject):
    """管理 Toast 窗口的显示和定时关闭"""

    def showToast_title_message_color_duration_(self, _, title, message, color_name, duration):
        """在主线程调用：创建并显示 toast"""
        color = TOAST_COLORS.get(color_name, TOAST_COLORS["info"])

        # 计算窗口尺寸
        width = 300
        height = 70 if message else 46

        # 获取屏幕尺寸（避开状态栏）
        screen = NSScreen.mainScreen()
        screen_frame = screen.frame()
        x = screen_frame.size.width - width - 16
        # macOS 状态栏高度约 22pt，再留 6pt 间距
        y = screen_frame.size.height - height - 28

        # 创建窗口（无边框、透明背景）
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

        # 标题
        title_y = height - 30 if message else height // 2 - 10
        title_field = NSTextField.alloc().initWithFrame_(
            NSRect(NSPoint(16, title_y), NSSize(width - 32, 22))
        )
        title_field.setStringValue_(title)
        title_field.setTextColor_(NSColor.whiteColor())
        title_field.setFont_(NSFont.boldSystemFontOfSize_(13))
        title_field.setBezeled_(False)
        title_field.setDrawsBackground_(False)
        title_field.setEditable_(False)
        title_field.setSelectable_(False)
        view.addSubview_(title_field)

        # 消息（可选）
        if message:
            msg_field = NSTextField.alloc().initWithFrame_(
                NSRect(NSPoint(16, 12), NSSize(width - 32, 18))
            )
            msg_field.setStringValue_(message)
            msg_field.setTextColor_(
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1, 1, 1, 0.85)
            )
            msg_field.setFont_(NSFont.systemFontOfSize_(11))
            msg_field.setBezeled_(False)
            msg_field.setDrawsBackground_(False)
            msg_field.setEditable_(False)
            msg_field.setSelectable_(False)
            view.addSubview_(msg_field)

        # 淡入动画（0.25 秒）
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.25)
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
        NSAnimationContext.currentContext().setDuration_(0.3)
        window.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()


# 单例控制器
_controller = None


def _get_controller():
    """获取单例控制器（必须用 alloc().init() 创建 NSObject 子类）"""
    global _controller
    if _controller is None:
        _controller = _ToastController.alloc().init()
    return _controller


def show_toast(title, message="", duration=2.5, type="info"):
    """显示一个 toast 浮窗

    Args:
        title: 标题（必填）
        message: 副标题（可选）
        duration: 显示时长（秒），默认 2.5 秒
        type: 类型，决定颜色（success/warning/error/info）

    注意：必须在主线程调用（rumps 回调中是主线程）。
    """
    controller = _get_controller()
    # 使用 performSelector 在主线程异步执行
    from Foundation import NSDictionary
    # 简化：直接调用（rumps 回调已经在主线程）
    controller.showToast_title_message_color_duration_(
        None, title, message, type, duration
    )
