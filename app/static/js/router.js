"use strict";
/* ================= 路由 ================= */
const RENDERERS = {
  assignment: renderAssignment, exam: renderExam,
  scan: renderScan, review: renderReview, analysis: renderAnalysis,
  lesson: renderLesson, practice: renderLesson, paper: renderLesson,
  students: renderStudents,
};

/* 页面输出缓存：切换页面保留之前界面（含滚动位置）。
   全局班级切换等数据范围变化时清空；各页面内的「↻ 刷新」按钮可随时重拉最新数据。 */
const pageCache = {};
function invalidatePageCache() { for (const k in pageCache) delete pageCache[k]; }

/* 缓存恢复后需要重新绑定的非内联事件（内联 onclick 随 innerHTML 一起恢复）；
   列表/数据页顺便静默重拉最新数据（快、无 AI 调用，不影响「保留输出」的体验） */
const REBINDERS = {
  scan: bindScanEvents,
  assignment: bindCheckingEvents,
  exam: bindCheckingEvents,
  review: () => { syncSegHighlights(); loadReview(); },
  practice: () => { syncSegHighlights(); loadPractices(); },
  paper: () => { syncSegHighlights(); loadPapers(); },
  lesson: () => renderLessonTab(),
  analysis: () => { syncSegHighlights(); renderAnaTab(); },
};

/* 恢复页面快照时，把各页 seg 标签高亮同步到当前 state（快照是渲染时的旧高亮） */
function syncSegHighlights() {
  $$("#revSeg button").forEach(x => x.classList.toggle("active", x.dataset.f === state.reviewFilter));
  $$("#pracSeg button[data-f]").forEach(x => x.classList.toggle("active", x.dataset.f === state.practiceFilter));
  $$("#paperSeg button[data-f]").forEach(x => x.classList.toggle("active", x.dataset.f === state.paperFilter));
  $$("#anaSeg button").forEach(x => x.classList.toggle("active", x.dataset.t === state.analysisTab));
  $$("#trendSeg button").forEach(x => x.classList.toggle("active", x.dataset.by === state.trendsBy));
}

async function render(opts) {
  const main = $("#main");
  const useCache = opts && opts.useCache;
  if (useCache && pageCache[state.page]) {
    main.innerHTML = pageCache[state.page].html;
    const rb = REBINDERS[state.page];
    if (rb) rb();
    window.scrollTo({top: pageCache[state.page].scrollY || 0});
    return;
  }
  main.innerHTML = `<div class="loading-block">加载中…</div>`;
  const fn = RENDERERS[state.page] || renderScan;
  try { await fn(main); }
  catch (e) { main.innerHTML = `<div class="card"><div class="empty">页面加载失败，请刷新重试（如仍失败，截图联系技术人员）</div></div>`; }
  pageCache[state.page] = {html: main.innerHTML, scrollY: 0};
  window.scrollTo({top: 0});
}

(async function boot() {
  try {
    const ont = await api("/api/ontology");
    state.ontology = ont;
    ont.categories.forEach(c => { state.catMap[c.id] = c; });
  } catch (e) { toast("错因类别加载失败，请刷新页面重试", false); }
  state.page = routeFromHash();   // 旧地址 → 新页（含页内分段定位）
  renderTabs();
  let theme = null;
  try { theme = localStorage.getItem("ea-theme"); } catch (e) { /* 忽略 */ }
  applyTheme(theme || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("#themeBtn").onclick = () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  await initClasses();
  // 长文本点击展开/收起（全局委托，含动态渲染的卡片）
  document.addEventListener("click", ev => {
    const el = ev.target.closest(".clamp");
    if (el) el.classList.toggle("open");
  });
  // 离开当前页时记住滚动位置（切回时恢复，保留之前浏览现场）
  window.addEventListener("scroll", () => {
    const c = pageCache[state.page];
    if (c) c.scrollY = window.scrollY;
  });
  // 一级：点阶段 → 进入该阶段首个页面（已在该阶段则不重复跳转）
  $("#navTabs").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-group]");
    if (!b) return;
    if (b.dataset.group === groupOf(state.page)) return;
    const pages = pagesOf(b.dataset.group);
    if (pages.length) go(pages[0][0]);
  });
  // 二级：点具体页面
  $("#navSub").addEventListener("click", ev => {
    const b = ev.target.closest("button[data-page]");
    if (b) go(b.dataset.page);
  });
  window.addEventListener("hashchange", () => {
    const prevA = state.analysisTab, prevL = state.lessonTab;
    const p = routeFromHash();
    if (p !== state.page || state.analysisTab !== prevA || state.lessonTab !== prevL) {
      state.page = p; renderTabs(); render({useCache: true});
    }
  });
  await render();
})();
