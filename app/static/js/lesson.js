"use strict";
/* ================= 讲评备课（讲评方案 / 配套练习 / 智能组卷：顶栏二级标签，三页共用渲染器） ================= */
const LESSON_TABS = {decision: "讲评方案", practice: "配套练习", paper: "智能组卷"};
const LESSON_SUBS = {
  decision: "自动算出这节课讲什么、不讲什么、找谁辅导（可打印带进课堂）",
  practice: "AI 按错因生成练习，您逐题审校后附入讲评方案",
  paper: "按错题分布出综合卷，审校通过后可导出学生卷 / 教师版",
};
async function renderLesson(main) {
  const tab = state.lessonTab;
  main.innerHTML = `
  <div class="page-title">讲评备课 · ${LESSON_TABS[tab]}</div>
  <div class="page-sub">${LESSON_SUBS[tab]}</div>
  <div id="lessonBody"><div class="loading-block">加载中…</div></div>`;
  await renderLessonTab();
}
async function renderLessonTab() {
  const box = $("#lessonBody");
  if (!box) return;
  if (state.lessonTab === "practice") await renderPracticeBody(box);
  else if (state.lessonTab === "paper") await renderPaperBody(box);
  else await renderSheetBody(box);
}
/* 讲评方案 → 配套练习：跳到配套练习标签并预选错因类别 */
function gotoPracticeTab(cat) {
  state.practiceCats = cat ? [cat] : [];
  if (state.page === "practice") { renderTabs(); render(); }
  else go("practice");
}

/* ---- 讲评方案段 ---- */
/* 数据范围：全部历史（默认）/ 按批次（某次或某几次作业、考试）/ 按阶段（日期范围）。
   picks 存已选批次 [{batch_no, exam_type}]，提交时取 batch_no 去重后逗号拼接 */
function sheetRangeQuery() {
  const r = state.sheetRange;
  const q = {};
  if (r.mode === "batch") {
    const nos = [...new Set(r.picks.map(p => p.batch_no))];
    if (nos.length) q.batch_nos = nos.join(",");
  } else if (r.mode === "range") {
    if (r.date_from) q.date_from = r.date_from;
    if (r.date_to) q.date_to = r.date_to;
  }
  return q;
}
/* 当前方案是否就是所选范围（班级 + 批次/阶段都一致）——决定进页要不要重算 */
function sheetMatchesRange() {
  const s = state.sheet, r = state.sheetRange;
  if (!s || (s.class_name || "") !== (state.className || "")) return false;
  const q = sheetRangeQuery();
  const same = (a, b) => (a || "") === (b || "");
  if (!same(s.batch_nos && s.batch_nos.join(","), q.batch_nos)) return false;
  if (!same(s.date_from, q.date_from) || !same(s.date_to, q.date_to)) return false;
  return true;
}
async function renderSheetBody(box) {
  box.innerHTML = `
  <div class="card tint-blue no-print" id="sheetRangeCard">
    <h3>🎯 数据范围 <span class="hint">默认全部历史——也可只针对某次作业/考试，或某个阶段备课</span></h3>
    <div class="btn-row" style="flex-wrap:wrap">
      <div class="seg" id="sheetRangeSeg">
        <button data-m="all" class="${state.sheetRange.mode === "all" ? "active" : ""}" onclick="setSheetRangeMode('all', this)">全部历史</button>
        <button data-m="batch" class="${state.sheetRange.mode === "batch" ? "active" : ""}" onclick="setSheetRangeMode('batch', this)">按批次</button>
        <button data-m="range" class="${state.sheetRange.mode === "range" ? "active" : ""}" onclick="setSheetRangeMode('range', this)">按阶段</button>
      </div>
      <span style="flex:1"></span>
      <button class="btn primary" id="genSheetBtn" onclick="genSheet()">⟳ 生成讲评方案</button>
      ${state.sheet ? `<button class="btn secondary" id="aiStratBtn" onclick="genSheetAI()" title="AI 根据本页数据，建议这节课怎么讲：总体思路、45 分钟时间分配、每类错因的切入话术与讲解步骤">✦ AI 讲评策略</button>
      <button class="btn secondary" onclick="exportSheet('html')" title="打开干净的 A4 打印页，浏览器打印或另存 PDF">⇩ 打印版</button>
      <button class="btn secondary" onclick="exportSheet('docx')" title="下载 Word 版，可继续编辑">⇩ Word 版</button>` : ""}
    </div>
    <div id="sheetRangeBox"></div>
    <div class="policy-strip" style="margin-top:10px">每次生成自动保存，可回看；范围只影响本页与导出（打印版 / Word / AI 策略同步该范围）。</div>
  </div>
  <div id="sheetBox"></div>`;
  await loadSheetBatches();
  renderSheetRangeBox();
  // 已有方案且班级/范围未变：直接展示（切换页面保留之前输出，含已生成的 AI 策略）；
  // 点「⟳ 生成讲评方案」再按最新确认数据重算
  if (sheetMatchesRange()) {
    renderSheetBox();
    renderAIAdvice();
    return;
  }
  await genSheet();   // 首次进入/范围已变重新生成，保证反映最新确认数据
}

/* 批次列表（错题库实际存在的批次，随右上角班级过滤） */
async function loadSheetBatches() {
  const r = await api("/api/errors/batches" + qstr({class_name: state.className})).catch(() => null);
  state.sheetBatches = (r && r.batches) || [];
}
function renderSheetRangeBox() {
  const box = $("#sheetRangeBox");
  if (!box) return;
  const r = state.sheetRange;
  if (r.mode === "batch") {
    const picked = new Set(r.picks.map(p => p.batch_no + "|" + p.exam_type));
    box.innerHTML = state.sheetBatches.length ? `
      <div class="btn-row" style="flex-wrap:wrap;margin-top:10px">
        ${state.sheetBatches.map((b, i) => {
          const on = picked.has(b.batch_no + "|" + b.exam_type);
          const date = (b.first_at || "").slice(0, 10);
          return `<button class="btn sm ${on ? "success" : "secondary"}" style="cursor:pointer"
            onclick="toggleSheetBatch(${i})" title="${esc(b.confirmed)} 条已确认 / 共 ${b.count} 条">
            ${on ? "✓ " : ""}${esc(b.exam_type || "未分类型")} · ${esc(b.batch_no)}${date ? ` · ${date}` : ""}（${b.count} 条）</button>`;
        }).join("")}
      </div>
      <div style="font-size:12.5px;color:var(--text2);margin-top:8px">${r.picks.length ? `已选 ${r.picks.length} 个批次——适合备某次作业/考试或某几次合并的讲评课` : "点击选择一个或多个批次（再次点击取消）"}</div>`
      : `<div style="font-size:12.5px;color:var(--text3);margin-top:10px">错题库里还没有带唯一标识的批次——先用批改页批改作业/试卷，或录入时填写唯一标识</div>`;
  } else if (r.mode === "range") {
    box.innerHTML = `
      <div class="btn-row" style="flex-wrap:wrap;margin-top:10px;align-items:center">
        <input type="date" id="sheetDateFrom" value="${esc(r.date_from)}" title="阶段起点（舍）" onchange="setSheetDate('date_from', this.value)">
        <span style="color:var(--text3)">—</span>
        <input type="date" id="sheetDateTo" value="${esc(r.date_to)}" title="阶段止点（含当天）" onchange="setSheetDate('date_to', this.value)">
        <button class="btn secondary sm" onclick="sheetDateQuick(1)">近一月</button>
        <button class="btn secondary sm" onclick="sheetDateQuick(3)">近三月</button>
        <button class="btn secondary sm" onclick="sheetDateQuick(12)">近一年</button>
        <span style="font-size:12.5px;color:var(--text2)">${r.date_from || r.date_to ? `阶段：${r.date_from || "起"} ~ ${r.date_to || "今"}（按错题录入时间）` : "选起止日期或用快捷按钮——适合备某个阶段（如本学期）的讲评课"}</span>
      </div>`;
  } else {
    box.innerHTML = "";
  }
}
function setSheetRangeMode(m, btn) {
  state.sheetRange.mode = m;
  $$("#sheetRangeSeg button").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  renderSheetRangeBox();
  if (sheetMatchesRange()) { renderSheetBox(); renderAIAdvice(); }   // 范围没变（如同为空选）不重算
}
function toggleSheetBatch(i) {
  const b = state.sheetBatches[i];
  if (!b) return;
  const key = b.batch_no + "|" + b.exam_type;
  const picks = state.sheetRange.picks;
  const at = picks.findIndex(p => p.batch_no + "|" + p.exam_type === key);
  if (at >= 0) picks.splice(at, 1); else picks.push({batch_no: b.batch_no, exam_type: b.exam_type});
  renderSheetRangeBox();
}
function setSheetDate(k, v) {
  state.sheetRange[k] = v || "";
  renderSheetRangeBox();
}
function sheetDateQuick(months) {
  const to = new Date(), from = new Date();
  from.setMonth(from.getMonth() - months);
  state.sheetRange.date_from = from.toISOString().slice(0, 10);
  state.sheetRange.date_to = to.toISOString().slice(0, 10);
  renderSheetRangeBox();
}

/* 讲评方案导出：打印友好 A4 页 / Word（与页面同源同范围实时重算） */
function exportSheet(fmt) {
  location.href = "/api/decision-sheet/export" +
    qstr(Object.assign({class_name: state.className || "", fmt}, sheetRangeQuery()));
}

async function genSheet() {
  const btn = $("#genSheetBtn");
  if (!btn) return;
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> 生成中…';
  try {
    state.sheet = await api("/api/decision-sheet" +
      qstr(Object.assign({class_name: state.className}, sheetRangeQuery())));
    state.aiStrategy = null;   // 讲评方案已重算，旧 AI 策略随之失效（恢复生成提示语）
    renderSheetBox();
  } catch (e) { toast(e.message, false); }
  if (btn) { btn.disabled = false; btn.textContent = "⟳ 生成讲评方案"; }
}

function renderSheetBox() {
  const s = state.sheet;
  const box = $("#sheetBox");
  if (!box || !s) return;
  box.innerHTML = `
  <div class="card">
    <div class="sheet-head">
      <div>
        <h3 style="font-size:21px;margin-bottom:2px">英语讲评课方案</h3>
        <div class="sheet-meta">
          <span>数据范围：<b>${esc(s.scope_label || (s.class_name || "全部历史数据"))}</b></span>
          <span>班级人数：<b>${s.class_size}</b></span>
          <span>确认错题：<b>${s.total_confirmed}</b></span>
          <span>记录编号：<b>${s.sheet_id || "—"}</b></span>
        </div>
      </div>
      <div class="btn-row no-print" style="justify-content:flex-end">
        <button class="btn secondary sm" onclick="exportSheet('html')" title="打开干净的 A4 打印页，浏览器打印或另存 PDF">⇩ 打印版</button>
        <button class="btn secondary sm" onclick="exportSheet('docx')" title="下载 Word 版，可继续编辑">⇩ Word 版</button>
      </div>
    </div>
    <h3 style="margin-top:20px">① 本课重点讲（错得最多的 ${s.focus.length} 类，共 ${s.focus.reduce((a, f) => a + f.count, 0)} 人次）</h3>
    ${s.focus.length ? s.focus.map(f => `
      <div class="card focus-card" style="margin:10px 0;padding:16px 18px">
        <div class="fc-head"><span class="badge g${f.category_id[0]}">${groupName(f.category_id[0])}</span>
          <b style="font-size:16px">${esc(f.name)}</b>
          <span class="badge gX">${f.count} 人次 · ${Math.min(100, Math.round(f.rate * 100))}%</span>
          ${f.approved_practice_count ? `<span class="badge gE">${f.approved_practice_count} 套已审校练习</span>` : `<a class="ai-tag" style="cursor:pointer" onclick="gotoPracticeTab('${f.category_id}')">尚无练习 · 去生成→</a>`}</div>
        <div style="font-size:13.5px"><b>教学要点：</b>${esc(f.teaching_point)}</div>
        <div style="font-size:13.5px;margin-top:4px"><b>练习：</b>${esc(f.practice_hint)}</div>
        ${f.examples.map(ex => `<div class="example">
          ${ex.qtype ? `<div style="margin-bottom:3px">${qtypeChip(ex.qtype)}</div>` : ""}
          <div class="ex-q clamp" title="点击展开/收起">${esc(ex.question)}</div>
          ${passageBox(ex.passage)}
          ${ex.answer ? `<div class="ex-a">错例：${esc(ex.answer)}</div>` : ""}
          <div class="ex-s">${ex.student_code ? esc(ex.student_code) + " · " : ""}${esc(ex.evidence || "")}</div>
        </div>`).join("")}
      </div>`).join("") : `<div class="empty">没有错得足够多的错因（需 ≥3 人次）</div>`}
    <h3 style="margin-top:20px">② 本课不讲清单（错误率 < 10%，个别反馈解决）</h3>
    ${s.skip.length ? `<div class="list">${s.skip.map(k => `
      <div class="list-row">${catBadge(k.category_id)}
        <div class="grow"><div class="title">${k.count} 人次 · ${Math.min(100, Math.round(k.rate * 100))}%</div>
        <div class="desc">建议：${esc(k.advice)}</div></div>
      </div>`).join("")}</div>` : `<div style="color:var(--text3);font-size:13.5px">无</div>`}
    <h3 style="margin-top:20px">③ 个别辅导名单（同类错因 ≥4 次）</h3>
    ${s.tutors.length ? `<div class="list">${s.tutors.map(t => `
      <div class="list-row tutor-row"><span style="font-weight:700">${esc(t.student_code)}</span>
        ${catBadge(t.category_id)}
        <div class="grow"><div class="title">同类错误 ${t.count} 次</div>
        <div class="desc">建议：${esc(t.advice)}</div></div>
      </div>`).join("")}</div>` : `<div style="color:var(--text3);font-size:13.5px">无</div>`}
    <div id="aiAdviceBox">${state.aiStrategy ? "" : `
      <div class="notice-strip no-print" style="margin-top:16px"><b>④ AI 讲评策略（可选，点上方「✦ AI 讲评策略」生成）：</b>AI 将根据本页数据建议这节课怎么讲——总体思路、45 分钟时间分配、每类重点错因的切入话术与讲解步骤。不生成也不影响 ①–③ 的使用。</div>`}</div>
    <div class="policy-strip" style="margin-top:16px">${esc(s.notice || "本名单为教学提示，不构成学生评价；不得向学生或家长公布排名、不得作为评价依据。")}</div>
  </div>`;
}

/* POST body 版范围参数（batch_nos 为数组，与 AIStrategyRequest 对齐） */
function sheetRangeBody() {
  const q = sheetRangeQuery();
  const body = {};
  if (q.batch_nos) body.batch_nos = q.batch_nos.split(",");
  if (q.date_from) body.date_from = q.date_from;
  if (q.date_to) body.date_to = q.date_to;
  return body;
}

async function genSheetAI() {
  const btn = $("#aiStratBtn");
  const box = $("#aiAdviceBox");
  if (box) box.innerHTML = `<div class="loading-block">AI 生成讲评策略中（约 10–30 秒）…</div>`;
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> AI 生成中…';
  try {
    state.aiStrategy = await api("/api/decision-sheet/ai", {method: "POST",
      body: Object.assign({class_name: state.className || null}, sheetRangeBody())});
    renderAIAdvice();
    const adv = $("#aiAdviceBox");
    if (adv) adv.scrollIntoView({behavior: "smooth", block: "start"});
    toast("AI 讲评策略已生成（见下方第 ④ 部分），请审阅后采用");
  } catch (e) {
    if (box) box.innerHTML = "";
    toast(e.message, false);
  }
  btn.disabled = false; btn.textContent = "✦ AI 讲评策略";
}

function renderAIAdvice() {
  const box = $("#aiAdviceBox");
  if (!box) return;
  const r = state.aiStrategy;
  if (!r) { box.innerHTML = ""; return; }
  const st = r.strategy || {};
  const fa = Array.isArray(st.focus_advice) ? st.focus_advice : [];
  box.innerHTML = `
    <h3 style="margin-top:20px">④ AI 讲评执行策略 <span class="ai-tag">${esc(r.ai_tag || "AI 生成 · 请您把关")}</span></h3>
    <div class="card focus-card" style="padding:16px 18px">
      <div style="font-size:14px;line-height:1.8">${esc(st.overview || "")}</div>
      ${st.timing ? `<div style="margin-top:10px;font-size:13.5px"><b>⏱ 时间分配：</b>${esc(st.timing)}</div>` : ""}
      ${fa.length ? `<div style="margin-top:14px">${fa.map(a => `
        <div class="ai-advice">
          ${catBadge(a.category_id)}
          <div style="flex:1">
            ${a.hook ? `<div style="font-size:13.5px"><b>切入：</b>${esc(a.hook)}</div>` : ""}
            ${a.steps ? `<div style="font-size:13.5px;margin-top:4px"><b>步骤：</b>${esc(a.steps)}</div>` : ""}
            ${a.board ? `<div style="font-size:13.5px;margin-top:4px;color:var(--text2)"><b>板书：</b>${esc(a.board)}</div>` : ""}
          </div>
        </div>`).join("")}</div>` : ""}
      ${st.skip_advice ? `<div style="margin-top:12px;font-size:13.5px"><b>不讲清单处理：</b>${esc(st.skip_advice)}</div>` : ""}
      ${st.tutor_advice ? `<div style="margin-top:6px;font-size:13.5px"><b>个别辅导组织：</b>${esc(st.tutor_advice)}</div>` : ""}
      ${st.risk ? `<div style="margin-top:6px;font-size:13.5px;color:#B91C1C"><b>⚠ 风险提醒：</b>${esc(st.risk)}</div>` : ""}
      <div class="policy-strip" style="margin-top:12px">策略由 AI 根据上方真实数据生成，数字不会编造；具体怎么讲由您最终把关。</div>
    </div>`;
}
/* ---- 配套练习段（AI 生成 → 您审校 → 附入讲评单；可单类或多类一次生成） ---- */
async function renderPracticeBody(box) {
  const preCats = state.practiceCats || [];
  state.practiceCats = [];
  state.practicePage = 1;   // 整页重渲染（首次/生成后/班级切换）从第 1 页开始
  box.innerHTML = `
  ${state.className ? "" : `<div class="card no-print" style="border-left:4px solid #B91C1C;padding:12px 18px;font-size:13.5px"><b style="color:#B91C1C">未选班级</b><span style="color:var(--text2)">——练习将不归属任何班级，建议右上角先选定</span></div>`}
  <div class="card tint-purple no-print">
    <h3>生成练习 <span class="hint">基于 36 类常见错因 · 可选多类一起生成（最多 6 类，每类各成一套）</span></h3>
    <div class="btn-row">
      <select id="pracCat" style="width:auto;min-width:230px" title="错因类别：选一个后点「＋ 添加」">${ontologyOptions()}</select>
      <button class="btn secondary" onclick="addPracticeCat()">＋ 添加类别</button>
      <input type="number" id="pracN" min="1" max="100" value="${state.practiceN || 5}" style="width:72px" title="每套题数（1–100）"> 题/套
      <button class="btn primary" id="genPracBtn" onclick="genPractice()">✦ 生成（AI）</button>
    </div>
    <div class="btn-row" id="pracCats" style="margin-top:10px;flex-wrap:wrap;align-items:center"></div>
    <div id="pracRangeBox"></div>
    <div class="policy-strip" style="margin-top:12px">AI 生成内容一律为草稿，须经您逐题审校把关后方可用于教学（《教师生成式AI应用指引》要求）。</div>
  </div>
  <div class="seg" id="pracSeg">
    <button data-f="draft" class="${state.practiceFilter === "draft" ? "active" : ""}" onclick="setPracticeFilter('draft', this)">待审校</button>
    <button data-f="approved" class="${state.practiceFilter === "approved" ? "active" : ""}" onclick="setPracticeFilter('approved', this)">已审校</button>
    <button data-f="all" class="${state.practiceFilter === "all" ? "active" : ""}" onclick="setPracticeFilter('all', this)">全部</button>
    <button class="btn secondary sm" style="margin-left:8px" onclick="loadPractices()">↻ 刷新</button>
  </div>
  <div id="pracList"><div class="loading-block">加载中…</div></div>
  <div style="height:76px"></div>
  <div class="check-dock" id="pracDock" style="display:none">
    <span id="pracDockInfo" style="font-size:12.5px;color:var(--text2)"></span>
    <span style="flex:1"></span>
    <button class="btn secondary" onclick="exportPracticeList(1)" title="当前列表全部练习合并导出为可打印网页（含答案与解析）">⇩ 导出列表·教师版</button>
    <button class="btn secondary" onclick="exportPracticeList(0)" title="当前列表全部已审校练习合并导出为可打印学生卷">⇩ 导出列表·学生卷</button>
  </div>`;
  drawPracticeCats(preCats);
  renderPracticeRangeRow();
  await loadPractices();
}

/* 筛选切换（内联 onclick：页面缓存恢复后仍可用） */
function setPracticeFilter(f, btn) {
  state.practiceFilter = f;
  state.practicePage = 1;   // 切筛选从第 1 页开始
  $$("#pracSeg button[data-f]").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadPractices();
}

/* 错例时间范围：设阶段后 AI 参考该阶段班级真实错例出题（留空 = 全部历史）；
   只重绘本行，不整页重渲染 */
function renderPracticeRangeRow() {
  const box = $("#pracRangeBox");
  if (!box) return;
  const r = state.practiceRange || (state.practiceRange = {from: "", to: ""});
  box.innerHTML = `
  <div class="btn-row" style="margin-top:10px;flex-wrap:wrap;align-items:center">
    <span style="font-size:12.5px;color:var(--text2)">错例时间范围：</span>
    <input type="date" id="pracDateFrom" value="${esc(r.from)}" title="阶段起点（舍）" onchange="setPracticeRange('from', this.value)">
    <span style="color:var(--text3)">—</span>
    <input type="date" id="pracDateTo" value="${esc(r.to)}" title="阶段止点（含当天）" onchange="setPracticeRange('to', this.value)">
    <button class="btn secondary sm" onclick="practiceDateQuick(1)">近一月</button>
    <button class="btn secondary sm" onclick="practiceDateQuick(3)">近三月</button>
    <button class="btn secondary sm" onclick="practiceDateQuick(12)">近一年</button>
    <button class="btn secondary sm" onclick="setPracticeRange('all')">全部历史</button>
    <span style="font-size:12.5px;color:var(--text3)">${r.from || r.to
      ? `按 ${r.from || "起"} ~ ${r.to || "今"} 的真实错例生成（按错题录入时间）`
      : "留空用全部历史——设范围后 AI 参考该阶段真实错例出题，更贴合班级实际"}</span>
  </div>`;
}
function setPracticeRange(k, v) {
  const r = state.practiceRange || (state.practiceRange = {from: "", to: ""});
  if (k === "all") { r.from = ""; r.to = ""; }
  else r[k] = v || "";
  renderPracticeRangeRow();
}
function practiceDateQuick(months) {
  const to = new Date(), from = new Date();
  from.setMonth(from.getMonth() - months);
  state.practiceRange = {from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10)};
  renderPracticeRangeRow();
}

/* 已选类别区：badge 可逐个移除；gotoPracticeTab 跳转预选也走这里 */
function drawPracticeCats(init) {
  if (init) state.practiceCats = init.slice(0, 6);
  const box = $("#pracCats");
  if (!box) return;
  const cats = state.practiceCats || [];
  box.innerHTML = cats.length
    ? cats.map((cid, i) => `<span style="display:inline-flex;align-items:center;gap:2px">${catBadge(cid)}
        <a onclick="removePracticeCat(${i})" title="移除该类别" style="cursor:pointer;opacity:.5;text-decoration:none">✕</a></span>`).join("")
      + `<span style="font-size:12.5px;color:var(--text2)">共 ${cats.length} 类 · 每类一套 · 合计约 ${cats.length * (state.practiceN || 5)} 题</span>`
    : `<span style="font-size:12.5px;color:var(--text2)">未选类别——下拉选择后点「＋ 添加类别」，可连续添加多个（一次最多 6 个）</span>`;
}
function addPracticeCat() {
  const cid = ($("#pracCat") || {}).value;
  if (!cid) { toast("请先在下拉框选择错因类别", false); return; }
  const cats = state.practiceCats || (state.practiceCats = []);
  if (cats.includes(cid)) { toast("该类别已在列表中", false); return; }
  if (cats.length >= 6) { toast("一次最多 6 个类别——类别多时请分次生成", false); return; }
  cats.push(cid);
  drawPracticeCats();
}
function removePracticeCat(i) {
  (state.practiceCats || []).splice(i, 1);
  drawPracticeCats();
}

async function genPractice() {
  const btn = $("#genPracBtn");
  const n = parseInt($("#pracN").value);
  const cats = state.practiceCats || [];
  state.practiceN = n;   // 生成后重渲染时保留题数输入
  if (!cats.length) { toast("请先添加至少一个错因类别", false); return; }
  btn.disabled = true; btn.innerHTML = cats.length > 1
    ? '<span class="spin"></span> AI 逐类生成中…多套请稍候'
    : '<span class="spin"></span> AI 生成中…';
  try {
    const r = await api("/api/practice/generate", {
      method: "POST",
      body: Object.assign({category_ids: cats, n, class_name: state.className || ""},
        state.practiceRange && (state.practiceRange.from || state.practiceRange.to)
          ? {date_from: state.practiceRange.from || null, date_to: state.practiceRange.to || null}
          : {}),
    });
    state.practiceFilter = "draft";
    toast(`已生成 ${r.count} 套共 ${r.total_items} 题，待审校`);
    render();
  } catch (e) {
    toast(e.message, false);
    btn.disabled = false; btn.textContent = "✦ 生成（AI）";
  }
}

async function loadPractices() {
  const box = $("#pracList");
  if (!box) return;
  let rows = await api("/api/practices").catch(() => []);
  if (state.className) rows = rows.filter(p => (p.class_name || "") === state.className);
  if (state.practiceFilter !== "all") rows = rows.filter(p => p.status === state.practiceFilter);
  state.practiceRows = rows;   // 列表导出用（含过滤后全部，与分页无关）
  const dock = $("#pracDock"), info = $("#pracDockInfo");
  if (dock) {
    dock.style.display = rows.length ? "" : "none";
    if (info) info.textContent = `当前列表共 ${rows.length} 套 · ${rows.reduce((a, p) => a + (p.items || []).length, 0)} 题`;
  }
  if (!rows.length) {
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">📝</div>
      ${state.practiceFilter === "draft" ? "暂无待审校练习——生成后在此逐题审校" : "暂无练习"}</div></div>`;
    return;
  }
  // 分页渲染（替换式）：每页 10 套（套内默认还折叠只显 3 题），套数再多也不卡；
  // 审校通过后刷新时留在当前页（套数变少则回退到最后一页）
  const pages = Math.ceil(rows.length / PRACTICE_PAGE);
  if (typeof state.practicePage !== "number") state.practicePage = 1;
  else if (state.practicePage > pages) state.practicePage = pages;
  drawPracticePage(state.practicePage);
}

/* 分页渲染：每页 10 套，滑块/按钮翻页 */
const PRACTICE_PAGE = 10;
function drawPracticePage(page) {
  state.practicePage = page;
  const rows = state.practiceRows || [];
  const pages = Math.max(1, Math.ceil(rows.length / PRACTICE_PAGE));
  const slice = rows.slice((page - 1) * PRACTICE_PAGE, page * PRACTICE_PAGE);
  $("#pracList").innerHTML = slice.map(p => practiceCardHTML(p)).join("") +
    pagerHTML("practice", page, pages, rows.length, "套");
}
PAGERS.practice = {go: (p, viaPager) => {
  drawPracticePage(p);
  if (viaPager) scrollToSel("#pracSeg", 60);
}};

function practiceCardHTML(p) {
  return `
  <div class="card">
    <div class="result-head">
      ${catBadge(p.category_id)}
      <span class="ai-tag">AI 生成 · ${p.status === "approved" ? "已审校" : "待审校"}</span>
      ${p.class_name ? `<span class="badge gX">${esc(p.class_name)}</span>` : ""}
      ${p.scope ? `<span class="badge gX" title="错例时间范围（按录入时间）">⏱ ${esc(p.scope)}</span>` : ""}
      <span style="margin-left:auto;font-size:12px;color:var(--text2)">练习 #${p.id} · ${(p.items || []).length} 题 · ${esc((p.created_at || "").slice(0, 10))}</span>
    </div>
    ${foldItemsHTML(p, "prac")}
    ${p.status === "draft"
      ? `<div class="btn-row" style="margin-top:6px">
          <button class="btn success sm" onclick="approvePractice(${p.id})">✓ 审校通过，可附入讲评方案</button>
          <button class="btn sm secondary" disabled title="审校通过后可导出学生卷">⇩ 学生卷·打印</button>
          <button class="btn sm secondary" disabled title="审校通过后可导出学生卷">⇩ 学生卷·Word</button>
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','html',1)" title="含答案与解析，可打印核对">⇩ 教师版·打印</button>
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','docx',1)">⇩ 教师版·Word</button>
          <span style="font-size:12.5px;color:var(--text2)">请逐题核对后点「✓ 审校通过」，通过后才能导出学生卷</span></div>`
      : `<div class="btn-row" style="margin-top:6px">
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','html',0)" title="可打印网页，也可另存 PDF">⇩ 学生卷·打印</button>
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','docx',0)">⇩ 学生卷·Word</button>
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','html',1)" title="含答案与解析">⇩ 教师版·打印</button>
          <button class="btn sm secondary" onclick="exportPractices('${p.id}','docx',1)">⇩ 教师版·Word</button>
        </div>
        <div class="policy-strip">已于 ${esc((p.approved_at || "").slice(0, 10))} 审校通过，可打印附入讲评方案</div>`}
  </div>`;
}

/* 题目折叠（练习/试卷卡）：默认只显示前 3 题，点击「展开剩余」再渲染其余——
   题多时页面不会拉得老长，审校/导出按钮就在眼前 */
function foldItemsHTML(p, prefix) {
  const items = p.items || [];
  const rest = items.slice(3);
  return items.slice(0, 3).map((it, i) => quizItem(it, i)).join("") +
    (rest.length ? `<div id="${prefix}-fold-${p.id}" class="fold-zone"></div>
      <button class="btn secondary sm" id="${prefix}-fold-btn-${p.id}" style="width:100%" onclick="toggleFold('${prefix}',${p.id})">▾ 展开剩余 ${rest.length} 题</button>` : "");
}
function toggleFold(prefix, id) {
  const box = $("#" + prefix + "-fold-" + id), btn = $("#" + prefix + "-fold-btn-" + id);
  if (!box || !btn) return;
  const p = (prefix === "prac" ? state.practiceRows : state.paperRows || []).find(x => x.id === id);
  if (!p) return;
  if (box.dataset.open) {
    box.innerHTML = ""; delete box.dataset.open;
    btn.textContent = `▾ 展开剩余 ${(p.items || []).length - 3} 题`;
    return;
  }
  box.innerHTML = (p.items || []).slice(3).map((it, i) => quizItem(it, i + 3)).join("");
  box.dataset.open = "1";
  btn.textContent = "△ 收起剩余题目";
}

/* 导出：html=新标签页打开可打印；docx=Word 下载；answers=1 教师版 / 0 学生卷 */
function exportPractices(ids, fmt, answers) {
  window.open(`/api/practices/export?ids=${ids}&fmt=${fmt}&answers=${answers}`, "_blank");
}
function exportPracticeList(answers) {
  const rows = state.practiceRows || [];
  if (!rows.length) { toast("当前列表没有练习可导出", false); return; }
  if (!answers && rows.some(p => p.status !== "approved")) {
    toast("列表里有未审校的练习——学生卷只能导出已审校的（可切到「已审校」再导）", false); return;
  }
  exportPractices(rows.map(p => p.id).join(","), "html", answers);
}

/* 选项文本剥自带字母前缀（与后端 options.strip_option_prefix 同规则）：
   练习/试卷列表的字母徽章 + 选项自带前缀会重复显示成 "A. A. xxx" */
function optText(o) {
  const s = String(o == null ? "" : o);
  const t = s.replace(/^\s*(?:\(\s*)?[A-Ha-h]\s*[.、．:：\)）]\s*/, "").trim();
  return t || s;
}

function quizItem(it, i) {
  const opts = (it.options || []).map((o, j) =>
    `<li><span class="opt-letter">${"ABCDE"[j] || "·"}</span><span>${esc(optText(o))}</span></li>`).join("");
  return `
  <div class="quiz">
    <div class="q-head"><span class="q-type">${esc(it.type || "练习")}</span>
      <span style="font-size:12px;color:var(--text3)">第 ${i + 1} 题</span></div>
    ${it.passage && String(it.passage).trim() ? passageBox(it.passage, "短文原文（点击展开）") : ""}
    <div class="q-body">${esc(it.q)}</div>
    ${opts ? `<ul class="opts">${opts}</ul>` : ""}
    <div class="q-ans"><b style="color:var(--green)">答案：${esc(it.answer)}</b></div>
    ${it.explanation ? `<div class="q-exp">解析：${esc(it.explanation)}</div>` : ""}
  </div>`;
}

async function approvePractice(id) {
  try {
    await api(`/api/practice/${id}/approve`, {method: "POST"});
    toast("已审校通过，练习可附入讲评方案");
    loadPractices();
  } catch (e) { toast(e.message, false); }
}

/* ---- 智能组卷段（错题分布 → AI 综合卷 → 您审校 → 导出学生卷/教师版） ---- */
async function renderPaperBody(box) {
  state.paperPage = 1;   // 整页重渲染（首次/生成后/班级切换）从第 1 页开始
  box.innerHTML = `
  ${state.className ? "" : `<div class="card no-print" style="border-left:4px solid #B91C1C;padding:12px 18px;font-size:13.5px"><b style="color:#B91C1C">未选班级</b><span style="color:var(--text2)">——试卷将不归属任何班级，建议右上角先选定</span></div>`}
  <div class="card tint-purple no-print">
    <h3>智能组卷 <span class="hint">按${state.className ? "「" + esc(state.className) + "」" : "全部范围"}已确认错题的分布出一份综合卷</span></h3>
    <div class="btn-row">
      <input type="text" id="paperTitle" placeholder="卷名（可留空，自动命名）" style="width:190px" title="试卷标题">
      <select id="paperExamType" style="width:auto" title="错题来源范围（按任务类型筛选）">
        <option value="">全部任务</option>
        ${EXAM_TYPES.map(t => `<option value="${t}">${t}</option>`).join("")}</select>
      <select id="paperDiff" style="width:auto" title="试卷难度">
        <option value="">难度：自动适配</option>
        <option value="easy">难度：基础巩固</option>
        <option value="mid">难度：标准</option>
        <option value="hard">难度：适度挑战</option></select>
      <select id="paperQtype" style="width:auto" title="题型构成">
        <option value="">题型：自动混合</option>
        <option value="mc">题型：单选为主</option>
        <option value="read">题型：完形+阅读为主</option>
        <option value="write">题型：改错+书面表达为主</option></select>
      <input type="number" id="paperN" min="1" max="50" value="${state.paperN || 10}" style="width:64px" title="题数（1–50）"> 题
      <button class="btn primary" id="genPaperBtn" onclick="genPaper()">✦ AI 组卷</button>
    </div>
    <div id="paperRangeBox"></div>
    <div class="policy-strip" style="margin-top:12px">按选定范围内错题的高频错因加权出题（人次多的错因多出题）；AI 生成内容一律为草稿，须经您逐题审校把关后方可导出使用。</div>
  </div>
  <div class="seg" id="paperSeg">
    <button data-f="draft" class="${state.paperFilter === "draft" ? "active" : ""}" onclick="setPaperFilter('draft', this)">待审校</button>
    <button data-f="approved" class="${state.paperFilter === "approved" ? "active" : ""}" onclick="setPaperFilter('approved', this)">已审校</button>
    <button data-f="all" class="${state.paperFilter === "all" ? "active" : ""}" onclick="setPaperFilter('all', this)">全部</button>
    <button class="btn secondary sm" style="margin-left:8px" onclick="loadPapers()">↻ 刷新</button>
  </div>
  <div id="paperList"><div class="loading-block">加载中…</div></div>`;
  renderPaperRangeRow();
  await loadPapers();
}

/* 错题时间范围：只统计该阶段内确认的错题来组卷（留空 = 全部历史）；
   与任务类型筛选叠加生效；只重绘本行，不整页重渲染 */
function renderPaperRangeRow() {
  const box = $("#paperRangeBox");
  if (!box) return;
  const r = state.paperRange || (state.paperRange = {from: "", to: ""});
  box.innerHTML = `
  <div class="btn-row" style="margin-top:10px;flex-wrap:wrap;align-items:center">
    <span style="font-size:12.5px;color:var(--text2)">错题时间范围：</span>
    <input type="date" id="paperDateFrom" value="${esc(r.from)}" title="阶段起点（舍）" onchange="setPaperRange('from', this.value)">
    <span style="color:var(--text3)">—</span>
    <input type="date" id="paperDateTo" value="${esc(r.to)}" title="阶段止点（含当天）" onchange="setPaperRange('to', this.value)">
    <button class="btn secondary sm" onclick="paperDateQuick(1)">近一月</button>
    <button class="btn secondary sm" onclick="paperDateQuick(3)">近三月</button>
    <button class="btn secondary sm" onclick="paperDateQuick(12)">近一年</button>
    <button class="btn secondary sm" onclick="setPaperRange('all')">全部历史</button>
    <span style="font-size:12.5px;color:var(--text3)">${r.from || r.to
      ? `只统计 ${r.from || "起"} ~ ${r.to || "今"} 确认的错题（按录入时间）`
      : "留空用全部历史——设范围后按该阶段错题分布组卷"}</span>
  </div>`;
}
function setPaperRange(k, v) {
  const r = state.paperRange || (state.paperRange = {from: "", to: ""});
  if (k === "all") { r.from = ""; r.to = ""; }
  else r[k] = v || "";
  renderPaperRangeRow();
}
function paperDateQuick(months) {
  const to = new Date(), from = new Date();
  from.setMonth(from.getMonth() - months);
  state.paperRange = {from: from.toISOString().slice(0, 10), to: to.toISOString().slice(0, 10)};
  renderPaperRangeRow();
}

/* 筛选切换（内联 onclick：页面缓存恢复后仍可用） */
function setPaperFilter(f, btn) {
  state.paperFilter = f;
  state.paperPage = 1;   // 切筛选从第 1 页开始
  $$("#paperSeg button[data-f]").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadPapers();
}

async function genPaper() {
  const btn = $("#genPaperBtn");
  const n = parseInt($("#paperN").value);
  const title = (($("#paperTitle") || {}).value || "").trim();
  const examType = (($("#paperExamType") || {}).value) || "";
  const difficulty = (($("#paperDiff") || {}).value) || "";
  const qtype_pref = (($("#paperQtype") || {}).value) || "";
  state.paperN = n;   // 生成后重渲染时保留题数输入
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span> AI 组卷中…';
  try {
    const r = await api("/api/paper/generate", {
      method: "POST",
      body: Object.assign({n, title, exam_type: examType || null, difficulty: difficulty || null,
             qtype_pref: qtype_pref || null, class_name: state.className || ""},
        state.paperRange && (state.paperRange.from || state.paperRange.to)
          ? {date_from: state.paperRange.from || null, date_to: state.paperRange.to || null}
          : {}),
    });
    state.paperFilter = "draft";
    toast(`已生成《${r.title}》共 ${r.items.length} 题，待审校`);
    render();
  } catch (e) {
    toast(e.message, false);
    btn.disabled = false; btn.textContent = "✦ AI 组卷";
  }
}

async function loadPapers() {
  const box = $("#paperList");
  if (!box) return;
  let rows = await api("/api/papers").catch(() => []);
  if (state.className) rows = rows.filter(p => (p.class_name || "") === state.className);
  if (state.paperFilter !== "all") rows = rows.filter(p => p.status === state.paperFilter);
  state.paperRows = rows;
  if (!rows.length) {
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">📄</div>
      ${state.paperFilter === "draft" ? "暂无待审考试卷——点上方「AI 组卷」生成" : "暂无试卷"}</div></div>`;
    return;
  }
  // 分页渲染（替换式）：每页 10 套（套内默认还折叠只显 3 题），套数再多也不卡；
  // 审校通过后刷新时留在当前页（套数变少则回退到最后一页）
  const pages = Math.ceil(rows.length / PAPER_PAGE);
  if (typeof state.paperPage !== "number") state.paperPage = 1;
  else if (state.paperPage > pages) state.paperPage = pages;
  drawPaperPage(state.paperPage);
}

/* 分页渲染：每页 10 套，滑块/按钮翻页 */
const PAPER_PAGE = 10;
function drawPaperPage(page) {
  state.paperPage = page;
  const rows = state.paperRows || [];
  const pages = Math.max(1, Math.ceil(rows.length / PAPER_PAGE));
  const slice = rows.slice((page - 1) * PAPER_PAGE, page * PAPER_PAGE);
  $("#paperList").innerHTML = slice.map(p => paperCardHTML(p)).join("") +
    pagerHTML("paper", page, pages, rows.length, "套");
}
PAGERS.paper = {go: (p, viaPager) => {
  drawPaperPage(p);
  if (viaPager) scrollToSel("#paperSeg", 60);
}};

function paperCardHTML(p) {
  return `
  <div class="card">
    <div class="result-head">
      <b style="font-size:15px">${esc(p.title || "综合测试卷")}</b>
      <span class="ai-tag">AI 生成 · ${p.status === "approved" ? "已审校" : "待审校"}</span>
      ${p.scope ? `<span class="badge gX" title="组卷范围">${esc(p.scope)}</span>` : ""}
      <span style="margin-left:auto;font-size:12px;color:var(--text2)">试卷 #${p.id} · ${(p.items || []).length} 题 · ${esc((p.created_at || "").slice(0, 10))}</span>
    </div>
    ${foldItemsHTML(p, "paper")}
    ${p.status === "draft"
      ? `<div class="btn-row" style="margin-top:6px">
          <button class="btn success sm" onclick="approvePaper(${p.id})">✓ 审校通过</button>
          <button class="btn sm secondary" disabled title="审校通过后可导出学生卷">⇩ 学生卷·打印</button>
          <button class="btn sm secondary" disabled title="审校通过后可导出学生卷">⇩ 学生卷·Word</button>
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'html',1)" title="含答案与解析，可打印核对">⇩ 教师版·打印</button>
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'docx',1)">⇩ 教师版·Word</button>
          <span style="font-size:12.5px;color:var(--text2)">请逐题核对后点「✓ 审校通过」，通过后才能导出学生卷</span></div>`
      : `<div class="btn-row" style="margin-top:6px">
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'html',0)" title="可打印网页，也可另存 PDF">⇩ 学生卷·打印</button>
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'docx',0)">⇩ 学生卷·Word</button>
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'html',1)" title="含答案与解析">⇩ 教师版·打印</button>
          <button class="btn sm secondary" onclick="exportPaper(${p.id},'docx',1)">⇩ 教师版·Word</button>
        </div>
        <div class="policy-strip">已于 ${esc((p.approved_at || "").slice(0, 10))} 审校通过，可导出使用</div>`}
  </div>`;
}

async function approvePaper(id) {
  try {
    await api(`/api/paper/${id}/approve`, {method: "POST"});
    toast("已审校通过，试卷可导出使用");
    loadPapers();
  } catch (e) { toast(e.message, false); }
}

function exportPaper(id, fmt, answers) {
  window.open(`/api/papers/${id}/export?fmt=${fmt}&answers=${answers}`, "_blank");
}
