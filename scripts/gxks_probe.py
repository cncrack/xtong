"""在线练习流程探针：跟踪每一步的真实状态，出问题能直接定位环节。

用法（面板/脚本出问题时）：
    .venv\\Scripts\\python.exe scripts/gxks_probe.py --port 9222
    .venv\\Scripts\\python.exe scripts/gxks_probe.py --port 9222 --watch

`--watch` 持续轮询（每 3 秒），适合"我操作、你看日志"的场景。

它只**读**，不点任何按钮、不改页面，安全。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ⚠ 必须在 import playwright 相关模块**之前**设好临时目录：
# 沙箱下 Playwright 建 mkdtemp 会 EPERM，指到项目内目录即可绕过。
_TMPDIR = Path(__file__).resolve().parent.parent / "data" / "tmp" / "pw-artifacts"
_TMPDIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TMPDIR)
os.environ["TEMP"] = str(_TMPDIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

STAGE_HINTS = {
    "MainPage.aspx": "学习入口（有课程列表帧才算就绪）",
    "LibraryStudyList": "课程/节列表帧",
    "ExamTmStepMain": "答题页（frameset 外层）",
    "ExamTmStepDo": "答题帧（tm_arr 在这里）",
    "ExamTmStepHeader": "答题头帧（SubmitExam 在这里）",
    "ExamMessage": "交卷成功页（带 Tk_Cl_Id / Clerk_Kscj_Id）",
    "ExamSave": "交卷结果页（失败时里面是 alert）",
    "viewExam": "判分/答案解析页（标准答案在这里）",
    "Loginb": "登录页",
}


def stage_of(url: str) -> str:
    for k, v in STAGE_HINTS.items():
        if k.lower() in (url or "").lower():
            return v
    return "未知页面"


def snapshot(port: int) -> dict:
    """读一次当前状态（只用 HTTP，不连 CDP）。"""
    from xxt.adapters.gxks import chrome

    out = {
        "at": time.strftime("%H:%M:%S"),
        "alive": chrome.port_alive(port),
        "pages": [],
    }
    if not out["alive"]:
        return out
    for u in chrome.page_urls(port):
        out["pages"].append({"url": u, "stage": stage_of(u)})
    return out


def snapshot_deep(port: int) -> dict:
    """连 CDP 读更深的状态：帧、答案、判分页解析结果。"""
    from xxt.adapters.gxks import browser, selectors
    
    try:
        from xxt.config import Settings, find_project_root
        s = Settings(root=find_project_root())
        sess = browser.open_session(s, label="", port=port, require_list=False)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    page = sess.page
    info: dict = {"url": page.url, "stage": stage_of(page.url), "frames": []}
    try:
        for f in page.frames:
            u = f.url or ""
            if not u or u == "about:blank":
                continue
            item = {"url": u[:120], "stage": stage_of(u)}
            try:
                caps = f.evaluate("""() => ({
                    tm_arr: (typeof tm_arr !== 'undefined' && tm_arr.length) || 0,
                    SubmitExam: typeof SubmitExam,
                    PackAnswer: typeof PackAnswer,
                    frmExam: typeof frmExam,
                    check_index: typeof check_index,
                })""")
                item.update(caps)
            except Exception:
                pass
            info["frames"].append(item)

        # 若在答题帧：读答案
        try:
            frame = None
            for f in page.frames:
                try:
                    if f.evaluate(selectors.EXAM_TM_ARR_JS):
                        frame = f
                        break
                except Exception:
                    pass
            if frame is not None:
                checked = frame.evaluate("""() => {
                  const o = {};
                  document.querySelectorAll('input[name^="tmda_"]:checked').forEach(el => {
                    o[el.name] = el.value; });
                  return o; }""")
                info["checked"] = checked
                # 题型分布 + 每题输入控件类型（判断多选是 radio 还是 checkbox）
                types = frame.evaluate("""() => {
                  const out = [];
                  const tm = (typeof tm_arr !== 'undefined') ? tm_arr : [];
                  // 每题由 tm_arr 给题型码；再按题号查它的 input 类型
                  for (let i = 0; i < tm.length; i++) {
                    const no = i + 1;
                    const ins = document.querySelectorAll(
                      'input[name="tmda_' + no + '"]');
                    const kinds = {};
                    ins.forEach(el => { kinds[el.type] = (kinds[el.type] || 0) + 1; });
                    out.push({
                      ordinal: no,
                      type_code: String(tm[i][1]),
                      stem: String(tm[i][2] || '').slice(0, 30),
                      input_types: kinds,
                      option_count: Number(tm[i][4]) || 0,
                    });
                  }
                  return out;
                }""")
                info["questions"] = types
        except Exception:
            pass
    finally:
        sess.close()
    return info


def record_types(deep: dict) -> None:
    """把探测到的题型累积到 `data/debug/type_stats.json`（只增不改，供分析用）。

    目的：不再靠"碰到才试"。每次你正常做练习，探针顺手记下这节用到的题型码、
    输入控件类型（radio/checkbox）、选项数 —— 攒几次就知道平台实际用了哪几种，
    再决定给 `TYPE_SPECS` 加哪些。
    """
    qs = deep.get("questions") or []
    if not qs:
        return
    path = (Path(__file__).resolve().parent.parent / "data" / "debug"
            / "type_stats.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        stats = {}
    changed = False
    for q in qs:
        code = str(q.get("type_code") or "")
        if not code:
            continue
        entry = stats.setdefault(code, {
            "type_code": code,
            "seen": 0,
            "input_types": {},
            "option_counts": [],
            "sample_stems": [],
        })
        entry["seen"] = int(entry.get("seen", 0)) + 1
        for k, v in (q.get("input_types") or {}).items():
            entry["input_types"][k] = int(entry["input_types"].get(k, 0)) + int(v)
        oc = q.get("option_count")
        if oc and oc not in entry["option_counts"]:
            entry["option_counts"].append(oc)
        stem = str(q.get("stem") or "")
        if stem and stem not in entry["sample_stems"] and len(entry["sample_stems"]) < 3:
            entry["sample_stems"].append(stem)
        changed = True
    if changed:
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(json.dumps({"recorded": True, "file": str(path),
                          "types": sorted(stats)}, ensure_ascii=False),
              file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--watch", action="store_true", help="持续监控（每 3 秒一次）")
    ap.add_argument("--deep", action="store_true", help="连 CDP 读帧与答案（更详细）")
    ap.add_argument("--record", action="store_true",
                    help="把题型累积到 data/debug/type_stats.json（配合 --deep）")
    args = ap.parse_args()

    def once():
        snap = snapshot(args.port)
        if args.deep and snap["alive"]:
            snap["deep"] = snapshot_deep(args.port)
            if args.record:
                record_types(snap["deep"])
        print(json.dumps(snap, ensure_ascii=False, indent=2))

    if not args.watch:
        once()
        return 0

    last = None
    print(f"[探针] 监控端口 {args.port} 每 3 秒（Ctrl+C 退出）", file=sys.stderr)
    try:
        while True:
            snap = snapshot(args.port)
            sig = json.dumps(snap.get("pages"), ensure_ascii=False)
            if sig != last:                    # 只在变化时打印，避免刷屏
                print(json.dumps(snap, ensure_ascii=False))
                last = sig
            time.sleep(3)
    except KeyboardInterrupt:
        print("[探针] 已停止", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
