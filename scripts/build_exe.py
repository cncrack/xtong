"""P3 构建脚本：PyArmor 混淆 core/ai → PyInstaller 打包 exe。

用法：.venv/Scripts/python.exe scripts/build_exe.py [--onefile]
  默认 onedir（dist/xxt/ 目录，启动快）；--onefile 单文件（dist/xxt.exe，便携分发）。
浏览器内核不打包——目标机需 `playwright install chromium` 或拷贝 ms-playwright（见 docs/BUILD.md）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
APP = BUILD / "app"
VENVPY = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run(cmd: list[str], **kw) -> None:
    print(">>", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT), **kw)


def main() -> None:
    # ① 组装干净源码树
    if BUILD.exists():
        shutil.rmtree(BUILD)
    APP.mkdir(parents=True)
    shutil.copytree(ROOT / "src" / "xxt", APP / "xxt",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "config" / "settings.toml", BUILD / "settings.toml.template")

    # ② PyArmor 混淆 core/ai（核心策略与 AI 层不可读；适配层壳保留）
    res = subprocess.run([str(VENVPY.parent / ("pyarmor.exe" if sys.platform == "win32" else "pyarmor")),
                          "gen", "-O", str(APP / "xxt"), "-r",
                          str(ROOT / "src/xxt/core"), str(ROOT / "src/xxt/ai")],
                         cwd=str(ROOT), capture_output=True, text=True)
    if res.returncode != 0:
        print("!! PyArmor 混淆失败（trial 限制？）——回退为仅 PyInstaller 打包\n", res.stdout, res.stderr)
    else:
        print(">> PyArmor 混淆完成（core/ai）")

    # ③ 入口
    (APP / "run.py").write_text(
        "from xxt.cli import main\nimport sys\nsys.exit(main())\n", encoding="utf-8")

    # ④ PyInstaller（playwright 全量收集；onedir=目录启动快 / onefile=单文件便携）
    onefile = "--onefile" in sys.argv
    mode_args = ["--onefile"] if onefile else []
    run([str(VENVPY.parent / ("pyinstaller.exe" if sys.platform == "win32" else "pyinstaller")),
         "--noconfirm", "--clean", "--name", "xxt", *mode_args,
         "--paths", str(APP), "--collect-all", "playwright",
         "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "pyi"),
         "--specpath", str(BUILD), str(APP / "run.py")])

    # ⑤ 附带运行说明与配置模板
    if onefile:
        dist = ROOT / "dist"
        shutil.copy(BUILD / "settings.toml.template", dist / "settings.toml.template")
        print(f"\n构建完成（单文件）：{dist / 'xxt.exe'}")
    else:
        dist = ROOT / "dist" / "xxt"
        shutil.copy(BUILD / "settings.toml.template", dist / "settings.toml.template")
        print(f"\n构建完成（目录）：{dist / 'xxt.exe'}")


if __name__ == "__main__":
    main()
