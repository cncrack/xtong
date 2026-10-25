"""发牌工具（分发方本地执行，私钥不离手）：

    .venv/Scripts/python.exe scripts/make_lic.py --mid 对方机器码 --days 30 [--note 备注]

产物 xxt.lic → 发给对方放在 exe/项目目录旁。换签名密钥：改 core/licensing.py 的 _SIGN_KEY
（双方一致才有效；构建分发版前务必先换）。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xxt.core.licensing import _SIGN_KEY, make_license  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid", required=True, help="对方机器码（xxt machine-id 显示的 XXXX-XXXX-...）")
    ap.add_argument("--days", type=int, default=30, help="有效天数（默认 30）")
    ap.add_argument("--note", default="", help="备注（如买家名，仅签发方可见）")
    a = ap.parse_args()
    # 与构建副本同源的密钥（data/lic_sign.key）：确保签出的牌与 exe 内验签密钥一致
    key_file = ROOT / "data" / "lic_sign.key"
    if key_file.exists():
        import xxt.core.licensing as L

        L._SIGN_KEY = key_file.read_text(encoding="utf-8").strip().encode()
    lic = make_license(a.mid, a.days, a.note)
    out = ROOT / "xxt.lic"
    out.write_text(lic, encoding="utf-8")
    print(f"已生成：{out}（{a.days} 天，绑定 {a.mid}）→ 发给对方放在程序目录")


if __name__ == "__main__":
    main()
