"""发牌管理工作台（GUI，tkinter 零依赖）：

    .venv/Scripts/python.exe scripts/release_gui.py

功能：本机机器码/密钥状态 · 一键构建（单文件/目录版，含源码变更提醒）·
      发牌（机器码+天数→xxt.lic）· 发牌记录（data/lic_records.json 本机管理）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xxt.core.licensing import make_license, machine_id  # noqa: E402

RECORDS = ROOT / "data" / "lic_records.json"
KEY_FILE = ROOT / "data" / "lic_sign.key"
EXE = ROOT / "dist" / "xxt.exe"


def load_records() -> list[dict]:
    if RECORDS.exists():
        return json.loads(RECORDS.read_text(encoding="utf-8"))
    return []


def save_records(rs: list[dict]) -> None:
    RECORDS.parent.mkdir(parents=True, exist_ok=True)
    RECORDS.write_text(json.dumps(rs, ensure_ascii=False, indent=1), encoding="utf-8")


def src_newer_than_exe() -> bool:
    """源码是否比上次构建新（提示需重建分发版）。"""
    if not EXE.exists():
        return False
    exe_m = EXE.stat().st_mtime
    for p in (ROOT / "src").rglob("*.py"):
        if p.stat().st_mtime > exe_m:
            return True
    return False


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("XXT 分发管理工作台")
        self.geometry("780x560")
        self._build_ui()
        self._refresh_status()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top = ttk.LabelFrame(self, text="状态")
        top.pack(fill="x", **pad)
        self.lbl_mid = ttk.Label(top, text="")
        self.lbl_mid.pack(anchor="w", padx=8, pady=4)
        self.lbl_key = ttk.Label(top, text="", foreground="#555")
        self.lbl_key.pack(anchor="w", padx=8)
        self.lbl_exe = ttk.Label(top, text="")
        self.lbl_exe.pack(anchor="w", padx=8, pady=(0, 4))

        build = ttk.LabelFrame(self, text="构建分发版（自动注入密钥；原源码不动）")
        build.pack(fill="x", **pad)
        ttk.Button(build, text="📦 构建单文件 exe", command=lambda: self._build(True)).grid(row=0, column=0, padx=8, pady=6)
        ttk.Button(build, text="📂 构建目录版", command=lambda: self._build(False)).grid(row=0, column=1)
        ttk.Button(build, text="🔄 刷新状态", command=self._refresh_status).grid(row=0, column=2)
        self.txt_out = scrolledtext.ScrolledText(build, height=6, font=("Consolas", 9))
        self.txt_out.grid(row=1, column=0, columnspan=3, sticky="we", padx=8, pady=(0, 6))
        build.columnconfigure(0, weight=1)

        issue = ttk.LabelFrame(self, text="发牌（发给对方 xxt.lic）")
        issue.pack(fill="x", **pad)
        row = ttk.Frame(issue); row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="对方机器码:").pack(side="left")
        self.ent_mid = ttk.Entry(row, width=24); self.ent_mid.pack(side="left", padx=4)
        ttk.Label(row, text="天数:").pack(side="left")
        self.ent_days = ttk.Entry(row, width=6); self.ent_days.insert(0, "30"); self.ent_days.pack(side="left", padx=4)
        ttk.Label(row, text="备注:").pack(side="left")
        self.ent_note = ttk.Entry(row, width=18); self.ent_note.pack(side="left", padx=4)
        ttk.Button(row, text="🎫 生成 lic", command=self._issue).pack(side="left", padx=8)
        ttk.Button(row, text="📁 导出便携发牌器", command=self._export_issuer).pack(side="left")
        ttk.Label(issue, text="生成于项目根 xxt.lic——通过微信/网盘发给对方，放到 exe 同目录即可",
                  foreground="#555").pack(anchor="w", padx=8, pady=(0, 6))

        rec = ttk.LabelFrame(self, text="发牌记录（仅本机管理用）")
        rec.pack(fill="both", expand=True, **pad)
        cols = ("time", "mid_tail", "days", "expire", "note")
        self.tree = ttk.Treeview(rec, columns=cols, show="headings", height=7)
        for c, w, t in zip(cols, (150, 90, 50, 90, 200),
                           ("签发时间", "机器码尾4", "天数", "到期", "备注")):
            self.tree.heading(c, text=t); self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=8, pady=6)
        ttk.Button(rec, text="🗑 删除选中记录", command=self._del_rec).pack(anchor="e", padx=8, pady=(0, 6))

    # ---------- 逻辑 ----------
    def _refresh_status(self) -> None:
        newer = src_newer_than_exe()
        self.lbl_mid.config(text=f"本机机器码：{machine_id()}（自用无需 lic；发给发牌方=你）")
        self.lbl_key.config(
            text=f"签名密钥：{'已生成 ✓（务必备份 data/lic_sign.key）' if KEY_FILE.exists() else '未生成（首次构建时自动生成）'}")
        if EXE.exists():
            ago = time.strftime("%Y-%m-%d %H:%M", time.localtime(EXE.stat().st_mtime))
            tip = "⚠ 源码有新改动——分发前请重新构建" if newer else "✓ 与源码同步"
            self.lbl_exe.config(text=f"单文件 exe：dist/xxt.exe（构建于 {ago}）{tip}",
                                foreground="#b45309" if newer else "#166534")
        else:
            self.lbl_exe.config(text="单文件 exe：尚未构建", foreground="#555")
        self._load_records()

    def _log(self, s: str) -> None:
        self.txt_out.insert("end", s + "\n")
        self.txt_out.see("end")
        self.update_idletasks()

    def _build(self, onefile: bool) -> None:
        cmd = [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(Path(__file__).parent / "build_exe.py")]
        if onefile:
            cmd.append("--onefile")

        def work() -> None:
            self._log(f">> 开始构建（{'单文件' if onefile else '目录版'}）…")
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines()[-6:]:
                self._log("  " + line)
            if r.returncode == 0:
                self._log(">> 完成 ✓")
            else:
                self._log(">> 失败 ✗\n" + (r.stderr or "")[-400:])
            self.after(0, self._refresh_status)

        threading.Thread(target=work, daemon=True).start()

    _ISSUER_TMPL = '''"""便携发牌器（自包含：内嵌签名密钥，任何装有 Python 的电脑可用）：

    python portable_issuer.py --mid 对方机器码 --days 30 [--note 备注]
    → 在当前目录生成 xxt.lic，发给对方放在 exe 同目录。

⚠ 内含私钥，只保存在你掌控的介质（U盘/私人网盘），不要外发/入库。
"""
import argparse, base64, hashlib, hmac, json, time

_SIGN_KEY = {key!r}.encode()


def make_license(mid, days, note=""):
    expire = time.strftime("%Y-%m-%d", time.localtime(time.time() + days * 86400))
    payload = json.dumps({{"mid": mid, "expire": expire, "note": note}}, ensure_ascii=False)
    b = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return b + "." + hmac.new(_SIGN_KEY, b.encode(), hashlib.sha1).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mid", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    lic = make_license(a.mid.upper(), a.days, a.note)
    open("xxt.lic", "w", encoding="utf-8").write(lic)
    print("xxt.lic 已生成（" + str(a.days) + " 天，绑定 " + a.mid.upper() + "）→ 发给对方放 exe 同目录")
'''

    def _export_issuer(self) -> None:
        if not KEY_FILE.exists():
            messagebox.showerror("无法导出", "签名密钥不存在（先完成一次构建）")
            return
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        out = ROOT / "data" / "portable_issuer.py"
        out.write_text(self._ISSUER_TMPL.format(key=key), encoding="utf-8")
        messagebox.showinfo(
            "已导出便携发牌器",
            f"{out}\n\n拷这一个文件到任何装有 Python 的电脑即可发牌：\n"
            f"python portable_issuer.py --mid 对方机器码 --days 30\n\n"
            f"⚠ 文件内含私钥，只存你掌控的介质，不要外发。")

    def _issue(self) -> None:
        mid = self.ent_mid.get().strip().upper()
        note = self.ent_note.get().strip()
        try:
            days = int(self.ent_days.get().strip())
        except ValueError:
            messagebox.showerror("输入错误", "天数须为整数")
            return
        if not mid:
            messagebox.showerror("输入错误", "请填对方机器码")
            return
        # 与构建同源密钥
        if KEY_FILE.exists():
            import xxt.core.licensing as L
            L._SIGN_KEY = KEY_FILE.read_text(encoding="utf-8").strip().encode()
        lic = make_license(mid, days, note)
        out = ROOT / "xxt.lic"
        out.write_text(lic, encoding="utf-8")
        rs = load_records()
        rs.append({"time": time.strftime("%Y-%m-%d %H:%M"), "mid_tail": mid[-4:],
                   "days": days, "expire": time.strftime(
                       "%Y-%m-%d", time.localtime(time.time() + days * 86400)),
                   "note": note})
        save_records(rs)
        self._load_records()
        messagebox.showinfo("已发牌",
                            f"xxt.lic 已生成于项目根（{days} 天，绑定 {mid}）。\n请发给对方放在 exe 同目录。")

    def _load_records(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for r in load_records():
            self.tree.insert("", "end", values=(r["time"], r["mid_tail"], r["days"], r["expire"], r["note"]))

    def _del_rec(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        rs = load_records()
        vals = self.tree.item(sel[0], "values")
        rs = [r for r in rs if not (r["time"] == vals[0] and r["mid_tail"] == vals[1])]
        save_records(rs)
        self._load_records()


if __name__ == "__main__":
    App().mainloop()
