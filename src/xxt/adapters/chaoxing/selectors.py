"""学习通页面选择器集中配置。

✅ 已实况核对（2026-10-24，方法：scripts/recon_space.py 转储真实页面/接口）：
  - 课程列表：不走 DOM，走页面自身 XHR `mooc2-ans visit/courselistdata`（HTML 片段），
    片段内选择器：卡片 `div.course[id^=c_]`、`input.courseId/.clazzId/.curPersonId`、
    课程名 `.course-name`、入口链接 `a[href]` —— 见 courses.py
  - 登录成功信号：`uid` / `_uid` / `vc3` 任一带值 cookie（passport2 登录后写入）

⚠️ 未实况核对（需要时再验证）：
  - 章节树 / 任务点 / 视频元素（嵌套 iframe 内）
  - 视频内弹题容器（P2 的事）

平台改版时：先重跑 scripts/recon_space.py 核对，再更新本文件与 courses.py。
"""

# P2 弹题 DOM 选择器（⚠️ 全部待实况核对——遇到含弹题的课程时校准）
# 结构假设源自 xuexitongScript 经验与平台惯例；核对方法：弹题出现时转储弹层 HTML
QUIZ_SELECTORS = {
    "popup": "[class*=exam], [class*=paper], [class*=tiiku], .ans-videoQuiz",
    "stem": ".mark_name, .timu, .question-title",
    "option": ".mark_answer li, .answerList li, .optionList li",
    "option_input": "input[type=radio], input[type=checkbox]",
    "fill_input": "input[type=text], textarea",
    "submit": "[class*=submit], .btnSubmit",
}
