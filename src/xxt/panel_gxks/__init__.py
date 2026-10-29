"""第二平台独立面板包（入口：`xxt gxks-panel`，默认 127.0.0.1:8301）。

与超星面板 `xxt.panel` 完全分开：不同包、不同端口、不同数据表。
两个平台各自演进，一个改版不会把另一个的面板拖下水。
"""

from .app import create_app, serve

__all__ = ["create_app", "serve"]
