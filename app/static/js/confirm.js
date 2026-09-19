"use strict";
/* ================= 页面：错题确认（批改收集 + 手动录入的质检关口） ================= */
async function renderReview(main) {
  state.reviewPage = 1;   // 整页重渲染（首次进入/班级切换/生成后）从第 1 页开始
  main.innerHTML = `<div class="page-title">错题确认</div>
    <div class="page-sub">AI 的判断只是建议，以您确认的为准；有把握的一键采纳，没把握的逐条看</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <div class="seg" id="revSeg">
        <button data-f="pending" class="${state.reviewFilter === "pending" ? "active" : ""}" onclick="setReviewFilter('pending', this)">待确认</button>
        <button data-f="confirmed" class="${state.reviewFilter === "confirmed" ? "active" : ""}" onclick="setReviewFilter('confirmed', this)">已确认</button>
        <button data-f="all" class="${state.reviewFilter === "all" ? "active" : ""}" onclick="setReviewFilter('all', this)">全部</button>
      </div>
      <div class="seg" title="只看某次作业 / 某次考试的错题（默认全部批次）">
        <select id="revBatch" style="border:none;background:transparent;font-size:13px;color:inherit;padding:6px 10px;cursor:pointer" onchange="setReviewBatch(this.value)"></select>
      </div>
      <div class="seg">
        <button onclick="loadReview()">↻ 刷新</button>
      </div>
    </div>
    <div id="reviewList"><div class="loading-block">加载中…</div></div>
    <div style="height:76px"></div>
    <div class="check-dock" id="revDock" style="display:none"></div>`;
  await fillBatchSelect("revBatch", state.reviewBatch, state.className);
  await loadReview();
}

/* 筛选切换（内联 onclick：页面缓存恢复后仍可用） */
function setReviewFilter(f, btn) {
  state.reviewFilter = f;
  state.reviewPage = 1;   // 切筛选从头开始
  $$("#revSeg button").forEach(x => x.classList.remove("active"));
  if (btn) btn.classList.add("active");
  loadReview();
}

/* 批次筛选切换：只看某次作业 / 某次考试（空 = 全部批次） */
function setReviewBatch(v) {
  state.reviewBatch = v || "";
  state.reviewPage = 1;
  loadReview();
}

/* ---- 批量采纳（减负）：AI 把握大的一键采纳，其余留待逐条确认 ---- */
async function confirmBulk(minConf) {
  const scopeTxt = state.className ? esc(state.className) : "全部";
  const batchTxt = state.reviewBatch ? `「${esc(state.reviewBatch)}」` : "";
  const label = minConf ? `一键采纳 ${scopeTxt}${batchTxt}中 AI 把握 ≥ ${Math.round(minConf * 100)}% 的待确认记录？`
                        : `采纳 ${scopeTxt}${batchTxt}的全部待确认记录（含 AI 把握不足的）？`;
  if (!confirm(label + "\n（忽略与改判不受影响；把握不足的建议逐条看一遍）")) return;
  try {
    const r = await api("/api/errors/confirm-bulk", {method: "POST", body: {
      action: "accept", min_confidence: minConf || null,
      class_name: state.className || null,
      batch_no: state.reviewBatch || null}});
    toast(`已批量采纳 ${r.confirmed} 条`);
    loadReview();
  } catch (e) { toast(e.message, false); }
}

async function loadReview() {
  const box = $("#reviewList");
  let url = "/api/errors" + qstr({
    limit: 2000,
    confirmed: state.reviewFilter === "pending" ? false : state.reviewFilter === "confirmed" ? true : null,
    class_name: state.className,
    batch_no: state.reviewBatch || null,
  });
  const rows = await api(url).catch(() => []);
  // 列表按 2000 条截断展示（后端上限），但批量采纳作用于全部待确认——条数取真实总数
  let pendingTotal = rows.length;
  if (state.reviewFilter === "pending" && rows.length >= 2000) {
    const d = await api("/api/dashboard" + qstr({class_name: state.className,
      batch_no: state.reviewBatch || null})).catch(() => null);
    if (d && d.pending_review != null) pendingTotal = d.pending_review;
  }
  const truncated = pendingTotal > rows.length;
  state.reviewRows = rows;
  // 底部固定操作条：错题再多，「批量采纳」始终可见（一键采纳有把握的 / 采纳全部）
  const dock = $("#revDock");
  if (dock) {
    const hiCnt = rows.filter(e => (e.confidence || 0) >= 0.8).length;
    dock.style.display = state.reviewFilter === "pending" && rows.length ? "" : "none";
    dock.innerHTML = state.reviewFilter === "pending" && rows.length ? `
      <span style="font-size:12.5px;color:var(--text2)">减负：AI 有把握的一键采纳，把握不足的请逐条把关</span>
      <span style="flex:1"></span>
      <button class="btn success sm" onclick="confirmBulk(0.8)">✓ 一键采纳有把握的（≥80%，共 ${truncated ? "≥" + hiCnt : hiCnt} 条）</button>
      <button class="btn secondary sm" onclick="confirmBulk(null)">采纳全部（${pendingTotal} 条）</button>` : "";
  }
  if (!rows.length) {
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">✅</div>
      ${state.reviewFilter === "pending" ? "没有待确认的错题——到「错题录入」页录入" : "暂无记录"}</div></div>`;
    return;
  }
  // 分页渲染（替换式）：只挂当前页的卡，DOM 恒定 ≤50 张，错题再多也不卡；
  // 确认/采纳单条后刷新时留在当前页（条数变少则回退到最后一页）
  const pages = Math.ceil(rows.length / REVIEW_PAGE);
  if (typeof state.reviewPage !== "number") state.reviewPage = 1;
  else if (state.reviewPage > pages) state.reviewPage = pages;
  drawReviewPage(state.reviewPage);
}

/* 分页渲染：每页 50 条，滑块/按钮翻页（长列表防卡顿，替代旧版滚动累积加载） */
const REVIEW_PAGE = 50;
function drawReviewPage(page) {
  state.reviewPage = page;
  const rows = state.reviewRows || [];
  const pages = Math.max(1, Math.ceil(rows.length / REVIEW_PAGE));
  const slice = rows.slice((page - 1) * REVIEW_PAGE, page * REVIEW_PAGE);
  $("#reviewList").innerHTML = slice.map(e => reviewCardHTML(e)).join("") +
    pagerHTML("review", page, pages, rows.length, "条");
}
PAGERS.review = {go: (p, viaPager) => {
  drawReviewPage(p);
  if (viaPager) scrollToSel("#revSeg", 60);   // 翻页后回到筛选条，从本页第一张卡开始看
}};

function reviewCardHTML(e) {
  const finalCat = e.teacher_override || e.category_id;
  const done = e.teacher_action;
  return `
  <div class="card" style="${done === "ignore" ? "opacity:.55" : ""}">
    <div class="result-head">
      ${catBadge(finalCat)}
      ${qtypeChip(e.qtype)}
      ${done === "modify" ? '<span class="badge gE">您改判</span>' : ""}
      ${done === "accept" ? '<span class="badge gX">已采纳</span>' : ""}
      ${done === "ignore" ? '<span class="badge gX">已忽略</span>' : ""}
      ${done ? "" : '<span class="ai-tag">AI 生成 · 待确认</span>'}
      <span style="margin-left:auto;font-size:12px;color:var(--text2)">
        ${esc(e.exam_type || "")}${e.batch_no ? " · " + esc(e.batch_no) : ""}${e.student_code ? " · " + esc(e.student_code) : ""}${e.source ? " · 来源 " + esc(e.source) : ""}</span>
    </div>
    ${done === "modify" && e.category_id !== e.teacher_override ?
      `<div style="font-size:12.5px;color:var(--text2);margin-bottom:4px">AI 原判：${esc(catName(e.category_id))} → 您改判：${esc(catName(e.teacher_override))}</div>` : ""}
    <div class="clamp" style="font-weight:600;margin:6px 0 4px" title="点击展开/收起">${esc(e.question)}</div>
    ${(e.options || []).length ? `<div class="opts-view" style="margin:2px 0 6px">${e.options.map(o => esc(o)).join("<br>")}</div>` : ""}
    ${passageBox(e.passage)}
    ${e.answer ? `<div style="font-size:13.5px;color:var(--red)">作答：${esc(e.answer)}</div>` : ""}
    ${e.correct ? `<div style="font-size:13.5px;color:#15803D">正确：${esc(e.correct)}</div>` : ""}
    ${e.evidence ? `<div style="font-size:12.5px;color:var(--text2);margin-top:5px">判断依据：${esc(e.evidence)}</div>` : ""}
    ${behaviorNote(safeBehavior(e.behavior))}
    ${done ? "" : confirmButtons(e.id)}
  </div>`;
}
