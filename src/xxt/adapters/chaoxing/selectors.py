"""学习通页面选择器集中配置。

⚠️ 以下选择器为初始猜测，尚未实况核对——计划在登录可用后用浏览器实况会话
逐一验证并修正（届时更新本文件与 devlog）。平台改版时优先只改此文件，
不要把选择器散落到业务代码里。
"""

SELECTORS = {
    # 学习空间课程列表
    "course_card": ".course-info",       # 课程卡片容器（待核对）
    "course_name": ".course-name",       # 课程名（待核对）
    # 章节树 / 任务点（M1 核对）
    "chapter_tree": ".chapter_course",
    # 视频内弹题容器（M3 核对）
    "quiz_popup": "[id^='pop']",
}
