"use strict";
/* ================= 错因分析页 · 条形图配色 ================= */
const BAR_COLORS = ["#4F46E5","#2563EB","#7C3AED","#0D9488","#D97706","#16A34A","#DC2626","#DB2777","#8E8E96","#00B5AD"];

/* ================= 错因分析页（合并原「错因总览 + 错因趋势」：页内分段） ================= */
async function renderAnalysis(main) {
  main.innerHTML = `
  <div class="page-title">错因分析</div>
  <div class="page-sub">只统计您确认过的错题（以您的改判为准）——「总览」看现状与预警，「趋势」看讲评效果与题型变化</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
    <div class="seg" id="anaSeg">
      <button data-t="overview" class="${state.analysisTab === "overview" ? "active" : ""}" onclick="setAnalysisTab('overview', this)">总览</button>
      <button data-t="trends" class="${state.analysisTab === "trends" ? "active" : ""}" onclick="setAnalysisTab('trends', this)">趋势</button>
    </div>
    <div class="seg" title="只看某次作业 / 某次考试的错题（默认全部批次；总览与趋势同步生效）">
      <select id="anaBatch" style="border:none;background:transparent;font-size:13px;color:inherit;padding:6px 10px;cursor:pointer" onchange="setAnaBatch(this.value)"></select>
    </div>
  </div>
  <div id="anaBody"><div class="loading-block">加载中…</div></div>`;
  await fillBatchSelect("anaBatch", state.anaBatch, state.className);
  await renderAnaTab();
}

/* 批次筛选切换（总览与趋势共用；空 = 全部批次） */
function setAnaBatch(v) {
  state.anaBatch = v || "";
  renderAnaTab();
}

/* 页内分段切换（内联 onclick：页面缓存恢复后仍可用） */
function setAnalysisTab(t, btn) {
  state.analysisTab = t;
  $$("#anaSeg button").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  renderAnaTab();
}
async function renderAnaTab() {
  const box = $("#anaBody");
  if (!box) return;
  if (state.analysisTab === "trends") await renderTrendBody(box);
  else await renderDashBody(box);
}

/* ---- 总览段 ---- */
async function renderDashBody(box) {
  const sel = `<select id="dashExamType" style="width:auto;font-size:13px">
    <option value="">全部任务</option>
    ${EXAM_TYPES.map(t => `<option value="${t}">${t}</option>`).join("")}</select>`;
  box.innerHTML = `
  <div class="card no-print" style="display:flex;gap:12px;align-items:center;padding:12px 18px">
    <span style="font-size:13px;color:var(--text2)">任务类型</span>${sel}</div>
  <div id="dashBody"><div class="loading-block">加载中…</div></div>`;
  const s = $("#dashExamType");
  if (s) s.onchange = loadDashboard;
  await loadDashboard();
}

async function loadDashboard() {
  const examType = ($("#dashExamType") || {}).value || null;
  const box = $("#dashBody");
  try { var d = await api("/api/dashboard" + qstr({exam_type: examType,
    class_name: state.className, batch_no: state.anaBatch || null})); }
  catch (e) { box.innerHTML = `<div class="empty">加载失败</div>`; return; }
  const maxC = Math.max(...d.top_categories.map(t => t.count), 1);
  const maxW = Math.max(...d.weekly_trend.map(w => w.count), 1);
  const weeks = d.weekly_trend;
  const trendTotal = weeks.length > 1;
  box.innerHTML = `
  <div class="card" style="padding:14px 18px;border-left:4px solid #4F46E5;margin-bottom:18px">
    <h3 style="margin-bottom:8px">小结</h3>
    <p style="font-size:14px;line-height:1.75;margin:0">${summarize(d)}</p>
  </div>
  <div class="grid c4" style="margin-bottom:18px">
    <div class="stat blue"><span class="num">${d.total_errors}</span><span class="label">错题总数</span></div>
    <div class="stat orange"><span class="num">${d.pending_review}</span><span class="label">待确认错因</span></div>
    <div class="stat purple"><span class="num">${d.confirmed}</span><span class="label">已确认（计入统计）</span></div>
    <div class="stat green"><span class="num">${d.students_involved}</span><span class="label">涉及学生人数</span></div>
  </div>
  <div class="grid c2">
    <div class="card">
      <h3>主要错因 <span class="hint">只统计您确认过的</span></h3>
      ${d.top_categories.length ? d.top_categories.map((t, i) => `
        <div class="bar-row">
          <div class="blabel">${catBadge(t.category_id)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.max(8, t.count / maxC * 100)}%;background:${BAR_COLORS[i % 10]}"></div></div>
          <div class="bval">${t.count}</div>
        </div>`).join("") : `<div class="empty">暂无确认数据</div>`}
    </div>
    <div class="card">
      <h3>周错题量 <span class="hint">按录入时间分周 · 全部录入（含待确认${examType ? " · 仅 " + esc(examType) : ""}${state.anaBatch ? " · 仅 " + esc(state.anaBatch) : ""}）</span></h3>
      ${trendTotal ? `<div class="chart-wrap">${miniBars(weeks, maxW)}</div>`
        : `<div class="empty">需≥2个周数据</div>`}
      <h3 style="margin-top:18px">预警 <span class="hint">比上一周增加超 50% 且人次 ≥3</span></h3>
      ${d.alerts.length ? d.alerts.map(a => `
        <div class="alert-item"><span class="alert-dot"></span>
        <div><b>${esc(a.name)}</b>：${esc(a.from_count)} → ${esc(a.to_count)} 人次
        <span style="color:var(--text2);font-size:12.5px">（${esc(a.from)} → ${esc(a.to)}，+${Math.round(a.rise * 100)}%）建议下节课重点关注</span></div></div>`).join("")
        : `<div style="color:var(--text3);font-size:13.5px;padding:8px 0">暂无预警</div>`}
    </div>
  </div>
  <div class="card no-print" style="display:flex;gap:12px;align-items:center">
    <div style="flex:1;font-size:13px;color:var(--text2)">导出${state.className ? esc(state.className) : "全部班级"}错题明细表格（Excel 可直接打开，仅含学生代号，不含姓名）</div>
    <a class="btn secondary sm" href="/api/export/errors.csv${qstr({class_name: state.className})}" download>⤓ 导出表格</a>
  </div>`;
}

/* 小结：确定性规则从真实数据生成一句话总结（数字不编造） */
function summarize(d) {
  const scope = state.className ? esc(state.className) : "全部班级";
  const top = d.top_categories || [];
  if (!d.confirmed) {
    if (!d.total_errors) {
      return `${scope}还没有错题记录：先在「错题录入」拍题或粘贴错题，再到「错题确认」页确认，这里会自动生成小结。`;
    }
    return `${scope}已有 ${d.total_errors} 条错题，其中 ${d.pending_review} 条待您确认——到「错题确认」页确认后，这里会自动生成小结。`;
  }
  let s = `${scope}已确认 <b>${d.confirmed}</b> 条错题，涉及 <b>${d.students_involved}</b> 名学生。`;
  if (top.length) {
    const t1 = top[0];
    const pct = Math.round(t1.count / d.confirmed * 100);
    s += ` 错因集中在「${esc(t1.name)}」（${t1.count} 人次，占 ${pct}%）`;
    if (top[1]) s += `，其次是「${esc(top[1].name)}」（${top[1].count} 人次）`;
    s += "。";
  }
  if (d.alerts && d.alerts.length) {
    const a = d.alerts[0];
    s += ` 其中「${esc(a.name)}」近两期从 ${a.from_count} 升至 ${a.to_count} 人次（+${Math.round(a.rise * 100)}%），建议下节课重点关注。`;
  }
  s += d.pending_review ? ` 另有 ${d.pending_review} 条错题待您确认。` : " 待确认已清零。";
  return s;
}

function miniBars(weeks, maxW) {
  const W = 520, H = 150, gap = Math.min(48, W / weeks.length - 8);
  const bw = Math.min(34, gap - 8);
  const x0 = 30, y0 = H - 26;
  const bars = weeks.map((w, i) => {
    const h = Math.max(4, w.count / maxW * (y0 - 16));
    const x = x0 + i * gap + (gap - bw) / 2;
    const full = String(w.week);
    const lbl = full.replace(/^\d{4}-/, "");   // 轴标签去年份，悬停提示完整
    return `<rect x="${x}" y="${y0 - h}" width="${bw}" height="${h}" rx="4" fill="#4F46E5" opacity=".9">
      <title>${esc(full)}：${w.count} 条</title></rect>
      <text class="ax" x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="10.5">${esc(lbl)}</text>
      ${w.count === maxW ? `<text class="axv" x="${x + bw / 2}" y="${y0 - h - 5}" text-anchor="middle" font-size="10.5" font-weight="700">${w.count}</text>` : ""}`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${bars}</svg>`;
}
/* ================= 错因分析页 · 趋势段 ================= */
/* ---- 趋势段 ---- */
const TREND_BY_LABEL = {week: "周", month: "月", half_year: "半年"};
async function renderTrendBody(box) {
  box.innerHTML = `
  <div class="seg" id="trendSeg" title="按录入错题的时间分桶查看变化">
    <button data-by="week" class="${state.trendsBy === "week" ? "active" : ""}" onclick="setTrendBy('week', this)">按周</button>
    <button data-by="month" class="${state.trendsBy === "month" ? "active" : ""}" onclick="setTrendBy('month', this)">按月</button>
    <button data-by="half_year" class="${state.trendsBy === "half_year" ? "active" : ""}" onclick="setTrendBy('half_year', this)">按半年</button>
  </div>
  <div id="trendBody"><div class="loading-block">加载中…</div></div>`;
  await loadTrends();
}

/* 趋势分桶切换（内联 onclick：页面缓存恢复后仍可用） */
function setTrendBy(by, btn) {
  state.trendsBy = by;
  $$("#trendSeg button").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadTrends();
}

const TREND_COLORS = ["#4F46E5","#DC2626","#16A34A","#7C3AED","#D97706","#0D9488","#DB2777"];

/* 时间分桶（复刻后端 SQLite strftime 语义，保证与类别趋势标签一致） */
function jsBucket(dateStr, by) {
  const d = new Date(dateStr);
  if (isNaN(d)) return "";
  const y = d.getFullYear();
  if (by === "month") return y + "-" + String(d.getMonth() + 1).padStart(2, "0") + "月";
  if (by === "half_year") return y + (d.getMonth() + 1 <= 6 ? "上半年" : "下半年");
  // week：复刻 SQLite %W（周一为一周首日，首个周一前为第 0 周）+1
  const start = new Date(y, 0, 1);
  const yday = Math.floor((d - start) / 86400000);
  const wday = d.getDay();   // Sunday=0，与 SQLite tm_wday 一致
  const week0 = Math.trunc((yday - ((wday + 6) % 7)) / 7);
  return y + "-第" + (week0 + 1) + "周";
}

async function loadTrends() {
  const box = $("#trendBody");
  let m;
  try { m = await api("/api/trends/categories" + qstr({by: state.trendsBy,
    class_name: state.className, batch_no: state.anaBatch || null})); }
  catch (e) { box.innerHTML = `<div class="empty">加载失败</div>`; return; }
  const series = (m.series || []).slice(0, 6);  // 图太挤，只画前6系列
  const xLabels = m.x_labels || [];
  const ov = m.overrides || {};
  // 前端聚合补充维度（与类别趋势同标准：只统计确认过的；随批次筛选）
  const rows = await api("/api/errors" + qstr({limit: 2000, class_name: state.className,
    batch_no: state.anaBatch || null})).catch(() => []);
  const okRows = rows.filter(e => e.teacher_action === "accept" || e.teacher_action === "modify");
  const xOf = e => jsBucket(e.created_at, state.trendsBy);
  // x 桶集合复用后端 x_labels（排序正确且与类别趋势口径一致）
  const xs = xLabels.length ? xLabels : [...new Set(okRows.map(xOf).filter(Boolean))];
  // 题型趋势：各题型确认错题量随时间分桶变化
  const qtMap = {};
  okRows.forEach(e => {
    const x = xOf(e); if (!x) return;
    const qt = e.qtype || "未注明题型";
    (qtMap[qt] = qtMap[qt] || {})[x] = (qtMap[qt][x] || 0) + 1;
  });
  const qtSeries = Object.entries(qtMap)
    .map(([qt, pts]) => ({qt, total: Object.values(pts).reduce((a, b) => a + b, 0)}))
    .sort((a, b) => b.total - a.total).slice(0, 6)
    .map(({qt}) => ({name: qt, points: xs.filter(x => qtMap[qt][x]).map(x => ({x, count: qtMap[qt][x]}))}));
  // 需关注学生：确认错题最多的前 8 人（含主要错因，点击看档案）
  let stuHtml = "";
  const byStu = {};
  okRows.forEach(e => { if (e.student_code) (byStu[e.student_code] = byStu[e.student_code] || []).push(e); });
  const stuCount = Object.keys(byStu).length;
  if (stuCount) {
    const stus = await api("/api/students").catch(() => []);
    const maxS = Math.max(...Object.values(byStu).map(es => es.length));
    stuHtml = Object.entries(byStu).map(([code, es]) => {
      const cc = {};
      es.forEach(e2 => { const c = e2.teacher_override || e2.category_id; if (c) cc[c] = (cc[c] || 0) + 1; });
      const tc = Object.entries(cc).sort((a, b) => b[1] - a[1])[0];
      return {code, n: es.length,
        nm: (stus.find(s => s.student_code === code) || {}).name_local || "",
        main: tc ? `${catName(tc[0])}（${tc[1]} 次）` : "—"};
    }).sort((a, b) => b.n - a.n).slice(0, 8).map((s, i) => `
      <div class="bar-row" style="cursor:pointer" onclick="openStudentProfile('${esc(s.code)}')" title="点击查看个人错题档案">
        <div class="blabel" style="width:150px">${i + 1}. ${esc(s.code)}${s.nm ? " · " + esc(s.nm) : ""}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.max(8, s.n / maxS * 100)}%;background:${BAR_COLORS[i % 10]}">${s.n}</div></div>
        <div class="bval">${s.n}</div>
      </div>
      <div style="font-size:12px;color:var(--text2);margin:-4px 0 6px 162px">主要错因：${esc(s.main)}</div>`).join("");
  }
  box.innerHTML = `
  <div class="grid c4" style="margin-bottom:18px">
    <div class="stat orange"><span class="num">${ov.override_rate != null ? Math.round(ov.override_rate * 100) + "%" : "—"}</span><span class="label">您的改判率</span></div>
    <div class="stat purple"><span class="num">${ov.modified || 0}</span><span class="label">您改判的题数</span></div>
  </div>
  <div class="card">
    <h3>错因类别趋势（前 6 类） <span class="hint">纵轴：人次（只统计您确认过的）· 横轴：录入错题的时间（按${TREND_BY_LABEL[state.trendsBy] || "周"}）${state.anaBatch ? " · 仅 " + esc(state.anaBatch) : ""}</span></h3>
    ${series.length && xLabels.length >= 2 ? drawLines(series, xLabels) : `<div class="empty"><div class="icon">📈</div>需要至少 2 个时间段的确认数据</div>`}
    <div class="legend">${series.map((s, i) => `<span><i style="background:${TREND_COLORS[i % 7]}"></i>${esc(s.name)}</span>`).join("")}</div>
  </div>
  <div class="card">
    <h3>题型趋势（前 6 种） <span class="hint">哪些题型错得多、是否在下降</span></h3>
    ${qtSeries.length && xs.length >= 2 ? drawLines(qtSeries, xs) : `<div class="empty"><div class="icon">📊</div>需要至少 2 个时间段的确认数据</div>`}
    <div class="legend">${qtSeries.map((s, i) => `<span><i style="background:${TREND_COLORS[i % 7]}"></i>${esc(s.name)}</span>`).join("")}</div>
  </div>
  <div class="card">
    <h3>需关注学生 <span class="hint">确认错题最多的前 8 人 · 点击看个人档案</span></h3>
    ${stuHtml || `<div class="empty">暂无关联学生的确认错题</div>`}
    <div class="policy-strip" style="margin-top:10px">仅供您安排辅导参考，不得向学生或家长公布。</div>
  </div>`;
}

function drawLines(series, xLabels) {
  const W = 860, H = 340, padL = 44, padR = 16, padT = 16, padB = 42;
  const iw = W - padL - padR, ih = H - padT - padB;
  const counts = series.flatMap(s => s.points.map(p => p.count));
  const maxV = Math.max(2, ...counts);
  const step = xLabels.length > 1 ? iw / (xLabels.length - 1) : 0;
  const xOf = i => padL + i * step;
  const yOf = v => padT + ih - (v / maxV) * ih;
  // 网格与刻度
  let grid = "";
  for (let g = 0; g <= 4; g++) {
    const v = Math.ceil(maxV / 4 * 4 - g * maxV / 4);
    const y = padT + ih * g / 4;
    grid += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(60,60,67,.1)" stroke-width="1"/>
      <text class="ax" x="${padL - 8}" y="${y + 3.5}" text-anchor="end" font-size="11">${Math.round(maxV - g * maxV / 4)}</text>`;
  }
  const xTicks = xLabels.map((lb, i) =>
    `<text class="ax" x="${xOf(i)}" y="${H - 14}" text-anchor="middle" font-size="11.5">${esc(lb)}</text>`).join("");
  const paths = series.map((s, si) => {
    const color = TREND_COLORS[si % 7];
    const pts = xLabels.map((lb, i) => {
      const hit = s.points.find(p => String(p.x) === String(lb));
      return hit ? {x: xOf(i), y: yOf(hit.count), v: hit.count, lb} : null;
    }).filter(Boolean);
    if (!pts.length) return "";
    const d = pts.map((p, i) => (i ? "L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1)).join(" ");
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
      ${pts.map(p => `<circle cx="${p.x}" cy="${p.y}" r="3.6" fill="#fff" stroke="${color}" stroke-width="2.2"><title>${esc(s.name)} @ ${esc(p.lb)}：${p.v} 人次</title></circle>`).join("")}`;
  }).join("");
  return `<div class="chart-wrap"><svg viewBox="0 0 ${W} ${H}" style="width:100%;min-width:640px;height:auto">${grid}${xTicks}${paths}</svg></div>`;
}
