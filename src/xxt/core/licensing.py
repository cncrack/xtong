"""轻量授权（自研，配合 PyArmor 混淆使用）。

机制：xxt.lic = base64(payload) + "." + HMAC-SHA1(密钥, payload)。
payload = {mid: 机器码, expire: YYYY-MM-DD, note}。
校验失败（伪造/过期/机器不符）→ 拒绝运行。
自用形态无 lic 文件 → 无感直通（不破坏现有使用）。
密钥常量藏于混淆后的 core 内（配合 PyArmor 提取成本高）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path

# 分发方签名密钥（构建时请自行更换为新随机串——保持私密）
_SIGN_KEY = b"xxt-default-lic-key-CHANGE-ME-2026"


def _raw_machine_id() -> str:
    """跨平台硬件指纹原始串（优先系统级稳定标识，回退 MAC）。"""
    try:  # Windows：注册表 MachineGUID（重装软件不变）
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\\Microsoft\\Cryptography",
        ) as k:
            return winreg.QueryValueEx(k, "MachineGUID")[0]
    except Exception:
        pass
    try:  # Linux：/etc/machine-id
        p = Path("/etc/machine-id")
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    try:  # 兜底：首个非环回 MAC
        out = subprocess.run(
            ["ip", "link"], capture_output=True, text=True, timeout=5).stdout
        import re

        m = re.search(r"link/ether ([0-9a-f:]{17})", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "unknown-machine"


def machine_id() -> str:
    """对外展示的机器码（XXXX-XXXX-XXXX-XXXX）。"""
    h = hashlib.sha1(_raw_machine_id().encode()).hexdigest()[:16].upper()
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def make_license(mid: str, days: int, note: str = "") -> str:
    """生成 lic 内容（分发方本地执行）。"""
    expire = time.strftime(
        "%Y-%m-%d", time.localtime(time.time() + days * 86400))
    payload = json.dumps({"mid": mid, "expire": expire, "note": note},
                         ensure_ascii=False)
    b = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_SIGN_KEY, b.encode(), hashlib.sha1).hexdigest()
    return f"{b}.{sig}"


def check_license(lic_path: Path, require: bool = False) -> tuple[bool, str]:
    """启动校验。lic 文件不存在：require=False（自用）放行；require=True 拒绝。"""
    if not lic_path.exists():
        return (True, "") if not require else (False, "缺少授权文件 xxt.lic")
    try:
        b64, sig = lic_path.read_text(encoding="utf-8").strip().split(".")
        expect = hmac.new(_SIGN_KEY, b64.encode(), hashlib.sha1).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return False, "授权文件签名无效（可能被篡改）"
        payload = json.loads(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
        if payload.get("mid") != machine_id():
            return False, "授权与本机不匹配（机器码不同）"
        if payload.get("expire", "") < time.strftime("%Y-%m-%d"):
            return False, f"授权已于 {payload['expire']} 到期，请联系发牌方续期"
        return True, f"授权有效（至 {payload['expire']}）"
    except Exception:
        return False, "授权文件格式错误"
