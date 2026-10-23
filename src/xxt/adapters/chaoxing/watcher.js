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
  send('watcher-ready', { dur: v.duration || 0 });
  return 'ok';
})();
