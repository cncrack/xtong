"""学习通 cxsecret 字体反爬动态解码（P2.5 正规方案，位图 IoU 版）。

原理：作业页内嵌仅含密文字形的 TTF → 用 PIL 把密文字形与参照字体（系统黑体）的
候选字（GB2312 一级字库）分别渲染成位图 → 归一化（裁墨迹框+等比缩放）→ 比对
IoU（交并比）→ 取最高分且过阈值的候选为明文。静态经验表作兜底。
"""

from __future__ import annotations

import base64
import io
import re

# GB2312 一级字库（3755 常用字）候选集
_CANDIDATES = []
for _hi in range(0xB0, 0xD8):
    for _lo in range(0xA1, 0xFF):
        try:
            _CANDIDATES.append(bytes([_hi, _lo]).decode("gb2312"))
        except UnicodeDecodeError:
            pass

_SIZE = 72  # 渲染尺寸
_NORM = 48  # 归一化尺寸


def _render(img_font, ch: str):
    from PIL import Image, ImageDraw

    img = Image.new("L", (_SIZE, _SIZE), 0)
    d = ImageDraw.Draw(img)
    d.text((6, 6), ch, font=img_font, fill=255)
    box = img.getbbox()
    if not box:
        return None
    img = img.crop(box)
    # 保持宽高比：长边缩放到 _NORM，短边补白居中（避免拉伸形变伤害相似度）
    w, h = img.size
    scale = _NORM / max(w, h)
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))))
    pad = Image.new("L", (_NORM, _NORM), 0)
    pad.paste(img, ((_NORM - img.width) // 2, (_NORM - img.height) // 2))
    return pad


def _cos(a, b) -> float:
    """灰度余弦相似度（0~1，保留笔画细节，优于二值 IoU）。"""
    va, vb = a.tobytes(), b.tobytes()
    dot = sum(x * y for x, y in zip(va, vb))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(y * y for y in vb) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def extract_cipher_font(html: str) -> bytes | None:
    m = re.search(r"base64,([A-Za-z0-9+/=]{500,})", html)
    return base64.b64decode(m.group(1)) if m else None


def build_dynamic_map(font_bytes: bytes,
                      ref_font_path: str = r"C:\Windows\Fonts\simhei.ttf",
                      threshold: float = 0.72) -> dict[str, str]:
    """位图灰度余弦比对生成 cipher→plain 映射（平台换字体变体自动适配）。"""
    from PIL import ImageFont

    cipher_font = ImageFont.truetype(io.BytesIO(font_bytes), _SIZE - 8)
    ref_font = ImageFont.truetype(ref_font_path, _SIZE - 8)
    ref_cache: dict[str, object] = {}
    for ch in _CANDIDATES:
        img = _render(ref_font, ch)
        if img is not None:
            ref_cache[ch] = img
    mapping: dict[str, str] = {}
    for cp in _font_codepoints(font_bytes):
        cipher = chr(cp)
        ci = _render(cipher_font, cipher)
        if ci is None:
            continue
        best, best_s = None, 0.0
        for ch, img in ref_cache.items():
            s = _cos(ci, img)
            if s > best_s:
                best, best_s = ch, s
        if best is not None and best_s >= threshold:
            mapping[cipher] = best
    return mapping


def _font_codepoints(font_bytes: bytes) -> list[int]:
    from fontTools.ttLib import TTFont

    try:
        return sorted(TTFont(io.BytesIO(font_bytes)).getBestCmap().keys())
    except Exception:
        return []


def decode(text: str, dynamic_map: dict[str, str] | None = None,
           static_map: dict[str, str] | None = None) -> str:
    m: dict[str, str] = dict(static_map or {})
    m.update(dynamic_map or {})  # 动态结果优先
    return "".join(m.get(ch, ch) for ch in text)
