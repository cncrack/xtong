/**
 * 页内视频 watcher（PLAN §5.3：页内只执行不决策）。
 * 注入到真正含 <video> 的 frame；外层经 exposeBinding 的 __xxtEvent 接收事件。
 * 职责：静音播放、倍速设定、暂停/停滞恢复（保活）、进度上报。
 * 红线：绝不修改 currentTime（平台可检测跳秒）。
 */
(() => {
  if (window.__xxtWatcher) return 'already';
  const v = document.querySelector('video');
  if (!v) return 'no-video';
  window.__xxtWatcher = true;

  const send = (type, extra) => {
    const payload = Object.assign(
      { type, t: Date.now(), cur: v.currentTime, dur: v.duration || 0,
        rate: v.playbackRate, paused: v.paused, muted: v.muted },
      extra || {}
    );
    try { window.__xxtEvent(JSON.stringify(payload)); } catch (e) { /* 外层未绑定 */ }
  };

  v.muted = true;
  try { v.playbackRate = __XXT_RATE__; } catch (e) {}
  ['playing', 'pause', 'ended', 'stalled', 'waiting', 'error'].forEach(ev =>
    v.addEventListener(ev, () => send(ev))
  );

  // 保活：被暂停即恢复；进度连续 ~9s 不前进即恢复（§5.8 实战经验）
  let lastT = v.currentTime, stall = 0;
  setInterval(() => {
    try {
      if (v.paused && !v.ended) { v.play().catch(() => {}); send('resume'); }
      if (Math.abs(v.currentTime - lastT) < 0.1) {
        if (++stall >= 3 && !v.ended) { v.play().catch(() => {}); stall = 0; send('stall-resume'); }
      } else { stall = 0; lastT = v.currentTime; }
    } catch (e) { send('watcher-error', { msg: String(e) }); }
    send('tick');
  }, 3000);

  // 自动播放策略兜底：失败则降级静音重试
  v.play().catch(() => { v.muted = true; v.play().catch(() => send('play-blocked')); });

  // ---- P2 弹题检测（选择器由外层注入 __XXT_QUIZ__，待实况核对）----
  try {
    const Q = __XXT_QUIZ__;
    let lastQuiz = '';
    const scanQuiz = () => {
      const pop = document.querySelector(Q.popup);
      if (!pop || !pop.offsetParent) return;
      const stemEl = pop.querySelector(Q.stem);
      const stem = stemEl ? stemEl.innerText.trim() : '';
      if (!stem || stem === lastQuiz) return;
      lastQuiz = stem;
      const options = Array.from(pop.querySelectorAll(Q.option))
        .map(li => li.innerText.trim()).filter(t => t && t.length < 200);
      const hasInput = pop.querySelectorAll(Q.option_input).length > 0;
      const multi = pop.querySelectorAll(Q.option_input + '[type=checkbox]').length > 0;
      const qtype = options.length ? (multi ? 'multi' : 'single')
        : (pop.querySelector(Q.fill_input) ? 'fill' : 'judge');
      send('quiz-popup', { stem, options, qtype, hasInput });
    };
    setInterval(scanQuiz, 3000);
    const mo = new MutationObserver(() => scanQuiz());
    mo.observe(document.body, { childList: true, subtree: true });
    // 外层决策后回填：fill(answer[], submit) —— 选择器同源，核对后即可用
    window.__xxtQuizFill = (answers, submit) => {
      const pop = document.querySelector(Q.popup);
      if (!pop) return 'no-popup';
      let ok = 0;
      for (const li of pop.querySelectorAll(Q.option)) {
        const inp = li.querySelector(Q.option_input);
        const hit = answers.some(a => li.innerText.includes(a) || a.includes(li.innerText.trim()));
        if (inp && hit) { inp.checked = true; inp.click(); ok++; }
      }
      if (!ok) {  // 填空：按顺序写入
        const inputs = pop.querySelectorAll(Q.fill_input);
        answers.slice(0, inputs.length).forEach((a, i) => { inputs[i].value = a; ok++; });
      }
      if (submit && ok) {
        const btn = pop.querySelector(Q.submit);
        if (btn) { btn.click(); return 'submitted'; }
      }
      return ok ? 'filled' : 'no-match';
    };
  } catch (e) { /* 弹题检测异常不干扰播放 */ }

  send('watcher-ready', { dur: v.duration || 0 });
  return 'ok';
})();
