"""高校考试网页面事实（**纯数据，不含逻辑**）。

来源：`docs/gxks/gaoxiaokaoshi.md` §2.7 / §2.8 / §2.10（2026-10-29 实测，A 级）。
规矩（PLAN-GXKS §11.2）：平台 URL / 选择器 / 接口名只允许出现在适配层；
站点改版时**只改本文件**，不改逻辑代码。

⚠ 本文件里的每一项都标了证据等级：
  - 【实测】= 2026-10-29 真机采样确认；
  - 【未证实】= 历史传闻或仅从他人脚本推测，**不要当成事实用来做判定**。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 站点与路由

BASE = "http://www.gaoxiaokaoshi.com"          # 【实测】仅 HTTP，HTTPS 不可达
LOGIN_PAGE = f"{BASE}/Login.aspx"              # 【实测】1920 宽也落这里，分流规则未证实
# 【实测 2026-10-29 12:49】会话失效时整页被踢到 **Loginb.aspx**（带 b 的另一套登录页）。
# 【实测 2026-10-30】未登录访问学习入口会被 302 到 **Default.aspx?ReturnUrl=…**，
# 而 `Login.aspx` 自己也会 302 到 `Default.aspx`（200 / 3717 字节，就是登录页本体）。
# ⇒ 三个都要认。只认前两个时，`at_login_page()` 会把“停在 Default.aspx 的未登录态”
#   误判成“已登录” —— P2 的「检查会话」首跑就撞上了这个假阳性。
DEFAULT_PAGE = f"{BASE}/Default.aspx"         # 【实测】登录页实际落点（带 ReturnUrl）
LOGIN_PAGES = (LOGIN_PAGE, f"{BASE}/Loginb.aspx", DEFAULT_PAGE)
LOGIN_POST = f"{BASE}/HidLogin.aspx"           # 【实测】表单 POST，明文

# 【实测 §7.2/§7.4】登录表单结构：`<form id="frmLogin">`，字段名 = `name`/`pw`/`verifyCode`，
# `screenType` 是服务端渲染的 hidden（值随视口档位变）→ **不得硬编码、不得改它**。
LOGIN_FORM_ID = "frmLogin"
LOGIN_FIELD_NAME = "name"
LOGIN_FIELD_PW = "pw"
LOGIN_FIELD_VERIFY = "verifyCode"
LOGIN_VERIFY_ROW = "trVerify"                  # 默认 `display:none`，仅 needVerify=1 时显示
NEED_VERIFY_HINT = "needVerify=1"             # 出现即**转人工**，绝不试错验证码
LOGIN_SUBMIT_SELECTOR = "input[name='btnSubmit'], #btnSubmit, input[type='submit']"

# 【实测 2026-10-29 13:58】真正的凭据票 = **`.ASPXAUTH`**（ASP.NET Forms 认证票）。
# 证据：① 12:49 被踢到登录页时导出的 10 条 cookie 里 **没有它**；
#       ② 12:52 重登后它出现，随后连播 47 分钟不断；
#       ③ 它是**会话级**（`expires=-1`，关浏览器即失）。
# ⇒ “掉登录”的直接成因不是 cookie 被清空，而是**这张票没了**；
#   而它是否还在可以**本地零请求读到** → 每节开播前自检，不必等到页面掉登录。
AUTH_COOKIE = ".ASPXAUTH"
MAIN_PAGE = f"{BASE}/Homew/MainPage.aspx?menu=3&subMenu=4"   # 【实测】学习入口

LIST_PATH = "/Study/LibraryStudyList.aspx"     # 【实测】课程/节列表（在 iframe 里）
STUDY_PATH = "/Study/LibraryStudy.aspx"        # 【实测】播放页 ?Id=<视频Id>&PlanId=<计划Id>
UPDATE_TIME_PATH = "/Study/updateTime.ashx"    # 【实测】唯一进度上报通道，每 60 秒 GET 一次

# ---------------------------------------------------------------- 帧结构

# 【实测】列表承载在 iframe[name="main"]；播放页的 iframe **名字会被 showframe() 改写**
# （初始 ExamView → 实测变 t），所以播放页只能按 URL 特征找，不能按 name 找。
LIST_FRAME_NAME = "main"
STUDY_FRAME_URL_HINT = "LibraryStudy.aspx"

# ---------------------------------------------------------------- 列表页

LIST_TABLE = "table.table"                     # 【实测】7 列
LIST_COLUMNS = {                               # 【实测】表头顺序，改版先核对这里
    0: "课程名称",
    1: "所属分类",
    2: "学时要求",      # 形如 "45分钟"：是学分口径，**不等于视频时长**
    3: "已完成学时",    # 形如 "21分钟"：服务端累计值，**完成判定只认这一列**
    4: "状态",          # 【实测】见过：未学习 / 学习中 / 已完成（2026-10-29 1.1 达标后）
    5: "开始学习",
    6: "课后练习",
}

# 【已证实 2026-10-29】1.1 学时 46 ≥ 要求 45 时，状态列变为「已完成」。
# 仍保留关键词族（改版换词时不至于全线不通），但「已完成」是实测值。
# ⚠ 不要用“已完成学时 >= 学时要求”单独判断完成——实测出现过 42/42 仍为「学习中」（服务端有滞时）。
STATE_DONE_HINTS = ("已完成", "已学完", "已学习")
STATE_LEARNING = "学习中"
STATE_NOT_STARTED = "未学习"

# 【实测】2026-10-29 抓包出现的全部域名（平台档案 §2.10 #13）：
#   g.alicdn.com                  → Aliplayer 2.9.11 主库与插件（HTTPS 官方 CDN）
#   vod.gaoxiaokaoshi.com        → 阿里云 VOD 加速域（m3u8/ts 分片）
#   videocloud.cn-hangzhou.log.aliyuncs.com → Aliplayer 自带埋点
# 新增域名必须先在 `gaoxiaokaoshi.md` 登记，再改这里——**不得为了让告警消失而直接放行**。
ALLOWED_HOSTS = {"g.alicdn.com"}
ALLOWED_HOST_SUFFIXES = (".gaoxiaokaoshi.com", ".aliyuncs.com")

# 【实测 2026-10-29 19:58】课程下拉：`<select name="ddlClass" id="ddlClass" onchange="">`。
# 当前账号**只有 1 项**（`value=32 军事理论`），且 `onchange` 为空 ⇒ 切换方式**未证实**；
# 见到第二门课时必须重新侦察再改 `driver.select_course()`，不得凭猜。
COURSE_SELECT_ID = "ddlClass"

# 【实测】分页是 ASP.NET 回发（__doPostBack），不能用 URL 参数翻页
PAGER_LABEL = ".page .fright li"               # 形如 "1/4"
PAGER_PREV = "#PageSplit1_BtnPrevious"
PAGER_NEXT = "#PageSplit1_BtnNext"
PAGER_SELECT_NAME = "PageSplit1$ddlPage"       # 实测为 name 属性（ASP.NET 命名容器写法）

# 【实测】节级门禁：未解锁时「参加练习」的 onclick 是 alert(...)，**读它即可判断，
# 不必点击试探**——这条让"不能硬闯风控"和"要知道能不能做题"同时成立。
# 达标后同一位置变成 `showExam(15263)`，括号里就是**练习/考试 ID**（课后题入口）。
PRACTICE_LOCK_HINT = "请先完成学习！"
EXAM_ONCLICK_PREFIX = "showExam("

# ---------------------------------------------------------------- 播放页

# 【实测】播放器是阿里云 Aliplayer 2.9.11（vid + playauth），autoplay:false
PLAYER_SELECTOR = "video"
PLAYER_LIB_HINT = "aliplayer"

# 【实测】隐藏字段（页面逻辑靠它们取身份与基数）
HIDDEN_FIELDS = {
    "video_id": "hidNewId",
    "section_id": "hidRefId",
    "student_id": "hidStudentId",
    "study_minutes": "hidStudyTime",   # ⚠ 实测**恒为 0**（Q11），**不是**服务端已学分钟，不可当进度读
    "pass_line": "hidPassLine",        # ⚠ 实测 = 1（不是“学时要求 45”），上报参数即 `Mins=1`
    "session_id": "hidSessionID",
}

# 【实测】内部计数器 id：显示"此次学习 X 分 Y 秒, 累计 Z 分"
STUDY_CLOCK_LABEL = "spTitle"

# 【实测】计时与上报节奏：墙钟每秒 +1，满 60 秒上报一次；倍速对墙钟无效
UPLOAD_EVERY_SECONDS = 60

# ---------------------------------------------------------------- 练习页（在线作答）

# 【实测 2026-10-29】直接 GET 这条即等价于点「参加练习」，但**不经过 confirm**
# （`showExam()` 里才有 `confirm('确定要参加这场练习吗?')`）——这是脚本能自己开卷的前提。
EXAM_START_URL = f"{BASE}/ExamList/chkExam.aspx?ExamID={{exercise_id}}&SiteType=2&IsContinue=0"
# 【实测】建卷是两跳：chkExam.aspx → ExamCreate.ashx（服务端建卷）→ 302 落地 frameset。
# 卡在中间态就去抓会拿到空页，必须等 URL 稳定。
EXAM_TRANSIENT_HINTS = ("chkExam.aspx", "ExamCreate.ashx")

# 【实测】答题帧里有 `var tm_arr = []`。帧名换过（ExamDo → ExamTmStepDo），
# 所以按“哪个帧有非空 tm_arr”定位，**不认帧名**。
EXAM_TM_ARR_JS = "() => (window.tm_arr || [])"
# tm_arr 每行（2026-10-30 从 ExamDo.html 原样读出）：
#   [0]题ID [1]题型代码 [2]题干 [3]分值 [4]选项数 [5]选项(分号分隔) [8]知识点ID
EXAM_TM_ARR_FIELDS = {"qid": 0, "type_code": 1, "stem": 2, "max_score": 3,
                      "option_count": 4, "options": 5, "knowledge_id": 8}

# 【实测】题型表也由页面自带（`tx_arr`，13 种，含题库里尚未实测的简答/论述等），
# 题型名称不必我们维护。每项：[0]题型代码 [2]名称。
EXAM_TYPE_ARR_JS = "() => (window.tx_arr || [])"

# 【实测】选项是 `<input type="radio" name="tmda_<题号>" value="a|b|c|d">`，
# value 就是选项 key，与 tm_arr[5] 分号拆分后的顺序一一对应（a,b,c,d…）。
# `onclick="check_index(N)"` 会刷新答题卡，因此**必须真点击**，不能只置 checked。
EXAM_RADIO_NAME = "tmda_{ordinal}"
EXAM_RADIO_ID = "tm_{ordinal}_{idx}"   # radio 的 id 规则，仅调试用

# 【实测】交卷走 `frmExam.submit()`（不是链接）。默认不调用；
# `gxks.auto_submit` 打开后才由后台进程自己点。
EXAM_SUBMIT_JS = "frmExam.submit()"

# ---------------------------------------------------------------- 服务端以外的流量

# 【实测】视频流走阿里云点播（自有 PlayDomain），不占平台带宽；播放器自带质量埋点
VIDEO_HOST_HINT = "aliyuncs.com"
