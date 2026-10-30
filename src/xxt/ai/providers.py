"""AI 厂商（provider）配置：默认列表 + 增删改查 + 每个配置项的说���。

## 为什么要这一层

`xxt/ai/llm.py` 只认三个值：`base_url` / `model` / `timeout`。
用户要的是"选一家→自动带出地址和模型→能自己加厂家→每一项有中文说明"。
所以把**厂商列表**和**配置项说明**抽成与平台无关的纯逻辑，面板只做展示与落盘。

## 为什么放 `xxt/ai/` 而不是面板里

超星面板 `panel/app.py` 里已有一份 provider 实现（内置五家），
但它是**面板私有的**、连着超星的库。第二平台要用同样的能力，
两处各写一份必然走偏（这个分支已经因为"各写一份"踩过探活的坑）。
所以收敛到 `xxt/ai/providers.py`：纯函数、无数据库、无平台细节，两边都能用。

## 关于 Key

**这一层不碰 Key。** Key 始终加密存在各自的库里（`app_config`/`meta`），
由调用方传进来；本模块只管"有哪些厂家、每家的地址和模型是什么、每一项什么意思"。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PROVIDERS",
    "FIELDS",
    "get",
    "names",
    "as_list",
    "merge",
    "upsert",
    "remove",
    "normalize",
    "validate",
]

#: 内置厂商基线（OpenAI 兼容接口）。用户可增删，改完存回各自的配置层。
#: `name` 唯一，是主键；`base_url` 结尾不带 `/`（拼 `/chat/completions` 时会再处理）。
PROVIDERS: tuple[dict[str, str], ...] = (
    {"name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-chat"},
    {"name": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "model": "glm-4-flash"},
    {"name": "通义千问",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus"},
    {"name": "Kimi",
     "base_url": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k"},
    {"name": "MiniMax",
     "base_url": "https://api.minimax.chat/v1",
     "model": "abab6.5s-chat"},
    {"name": "文心一言",
     "base_url": "https://qianfan.baidubce.com/v2",
     "model": "ernie-4.0-8k"},
    {"name": "硅基流动",
     "base_url": "https://api.siliconflow.cn/v1",
     "model": "Qwen/Qwen2.5-7B-Instruct"},
    {"name": "OpenAI",
     "base_url": "https://api.openai.com/v1",
     "model": "gpt-4o-mini"},
    {"name": "Ollama 本地",
     "base_url": "http://127.0.0.1:11434/v1",
     "model": "qwen2.5:7b"},
)

#: 每个配置项是干什么的。面板逐项渲染，用户不用猜。
FIELDS: tuple[dict[str, str], ...] = (
    {"key": "name", "cn": "厂商名", "required": "1",
     "desc": "只是给自己看的名字，可随便改；同名会覆盖。"},
    {"key": "base_url", "cn": "接口地址", "required": "1",
     "desc": "OpenAI 兼容地址，结尾不要带 /。程序会自动拼 /chat/completions。"},
    {"key": "model", "cn": "模型", "required": "1",
     "desc": "模型名，按厂商文档填。选厂商会自动带一个可用的默认值。"},
    {"key": "api_key", "cn": "API Key", "required": "0",
     "desc": "加密存在本机 data/ 目录自己的库里，不入库、不回显、不走环境变量。"},
    {"key": "timeout", "cn": "超时（秒）", "required": "0",
     "desc": "单次请求超时，默认 60。本地模型或网络差可调大。"},
)


def as_list(saved: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """内置 + 用户自定义，返回可直接用的列表。

    ⚠ 顺序与覆盖关系（测试逼出来的）：
    - 内置在前、自定义在后是"看起来整齐"的写法，但会让**内置值挡住用户的覆盖**
      （实测：用户把 DeepSeek 地址改成 https://x/v1，取出来却还是内置那个）。
    - 所以这里按名字做一次合并：**同名时以用户自定义为准**，
      再按"先内置（保持原有顺序）、后新增自定义"排好。
    """
    custom: dict[str, dict[str, str]] = {}
    extra: list[dict[str, str]] = []
    for item in saved or []:
        got = normalize(item)
        if not got:
            continue
        custom[got["name"]] = got

    builtin_names = {p["name"] for p in PROVIDERS}
    out: list[dict[str, str]] = []
    for p in PROVIDERS:
        out.append(dict(custom.get(p["name"], p)))       # 同名用用户的
        custom.pop(p["name"], None)
    for name, got in custom.items():                      # 剩下的都是用户新增的
        if name not in builtin_names:
            extra.append(got)
    out.extend(extra)
    return out


def get(name: str, saved: list[dict[str, Any]] | None = None) -> dict[str, str] | None:
    """按名字取一家；找不到返回 None（不猜、不回落第一家）。"""
    want = str(name or "").strip()
    for p in as_list(saved):
        if p["name"] == want:
            return p
    return None


def names(saved: list[dict[str, Any]] | None = None) -> list[str]:
    return [p["name"] for p in as_list(saved)]


def normalize(item: Any) -> dict[str, str] | None:
    """洗成 `{name, base_url, model}`；缺必填就返回 None（由调用方决定怎么报）。"""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    base_url = str(item.get("base_url") or "").strip().rstrip("/")
    model = str(item.get("model") or "").strip()
    if not (name and base_url and model):
        return None
    return {"name": name, "base_url": base_url, "model": model}


def validate(item: Any) -> tuple[dict[str, str] | None, str]:
    """`normalize` 的可读版：返回 `(结果, 错误说明)`，错误说明给人看。"""
    got = normalize(item)
    if got:
        return got, ""
    if not isinstance(item, dict):
        return None, "厂商信息格式不对"
    missing = [f["cn"] for f in FIELDS
               if f["required"] == "1" and not str(item.get(f["key"]) or "").strip()]
    return None, "缺必填项：" + "、".join(missing) if missing else "厂商信息不完整"


def upsert(saved: list[dict[str, Any]] | None, item: Any) -> tuple[list[dict[str, str]], str]:
    """新增或覆盖一家（按 name 判重）。返回 `(新列表, 错误说明)`。

    只动**用户自定义**那部分：内置那几家不写进 saved，
    否则用户改了内置项后，升级内置基线时会被旧值盖住。
    """
    got, err = validate(item)
    if not got:
        return list(saved or []), err
    out = [dict(x) for x in (saved or []) if isinstance(x, dict)]
    out = [normalize(x) for x in out]
    out = [x for x in out if x]
    for i, x in enumerate(out):
        if x["name"] == got["name"]:
            out[i] = got
            return out, ""
    out.append(got)
    return out, ""


def remove(saved: list[dict[str, Any]] | None, name: str) -> list[dict[str, str]]:
    """删一家。只能删用户自己加的（内置的不在 saved 里，删了也没用）。"""
    want = str(name or "").strip()
    out = [normalize(x) for x in (saved or []) if isinstance(x, dict)]
    return [x for x in out if x and x["name"] != want]


def merge(current: dict[str, Any] | None,
          saved: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把"当前选中的厂商"补成完整三件套（base_url/model 缺失时按名字补）。

    为什么需要：配置里可能只存了 `provider: DeepSeek` 而没存地址。
    """
    cur = dict(current or {})
    name = str(cur.get("provider") or cur.get("provider_name") or cur.get("name") or "").strip()
    if name and not (str(cur.get("base_url") or "").strip() and str(cur.get("model") or "").strip()):
        hit = get(name, saved)
        if hit:
            cur.setdefault("base_url", hit["base_url"])
            cur.setdefault("model", hit["model"])
    return cur
