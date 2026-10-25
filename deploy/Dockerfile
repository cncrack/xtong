# XXT 学习助手 — Docker 镜像（PLAN §4.1 双形态之服务器形态）
# 基础镜像：Playwright 官方 Python（自带 Chromium/Firefox/WebKit，免手动装内核）
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

# 时区（学习窗口按本地时间计算）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# 依赖先行（利用层缓存）
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# 运行数据全走 /app/data（compose 挂载卷持久化：db/会话/日志/密钥）
# 配置：挂载 ./config 到 /app/config（settings.toml 在宿主机改）
# LLM Key：环境变量 XXT_LLM_API_KEY（或首启后面板填，加密落 data/）
ENV XXT_HEADLESS=1
VOLUME ["/app/data"]
EXPOSE 8300

# 面板常驻；学习会话由面板/auto 守护管理（容器内全部 headless）
CMD ["python", "-m", "xxt.cli", "panel"]
