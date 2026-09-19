"use strict";
/* 版本自查：打开浏览器控制台（F12）看到 UI-VERSION 即可确认加载的是最新文件 */
console.log("英语教学助手 UI-VERSION: v1.0.0");
/* ================= 基础设施 ================= */
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function toast(msg, ok) {
  const t = $("#toast");
  const sticky = ok === false;   // 错误：常驻至手动关闭，避免一闪而过看不清
  clearTimeout(t._h);
  t.textContent = msg;
  if (sticky) {
    const x = document.createElement("span");
    x.className = "toast-x";
    x.textContent = "✕";
    t.appendChild(x);
  }
  t.classList.toggle("sticky", sticky);
  t.style.background = sticky ? "rgba(200,30,30,.94)" : "rgba(28,28,30,.92)";
  t.classList.add("show");
  if (!sticky) t._h = setTimeout(() => t.classList.remove("show"), 2400);
}
/* 点击错误提示任意处关闭（成功提示不可点击，自动消失） */
$("#toast").addEventListener("click", () => {
  const t = $("#toast");
  clearTimeout(t._h);
  t.classList.remove("show");
});

async function api(path, opts) {
  opts = opts || {};
  if (opts.body) { opts.headers = {"Content-Type": "application/json"}; opts.body = JSON.stringify(opts.body); }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) {
    const msg = (data && data.detail) || ("请求失败 " + res.status);
    console.error("[请求失败]", path, res.status, msg);
    reportClientLog("ERROR", msg, path);   // 与后端日志汇流，便于整体定位
    throw new Error(msg);
  }
  return data;
}

/* 拼 query：自动跳过空值（null/""/undefined） */
function qstr(obj) {
  const q = Object.entries(obj || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => k + "=" + encodeURIComponent(v));
  return q.length ? "?" + q.join("&") : "";
}

/* ================= 通用分页（长列表防卡顿） =================
   替换式渲染：每页只挂当前页的卡片，DOM 数量恒定，翻页不累积——
   错题/练习再多也不卡（配合下方滑块拖动快速翻页）。
   用法：渲染处 box.innerHTML = 当前页卡片.map(cardHTML).join("") +
             pagerHTML("review", page, pages, total, "条")；
         翻页回调注册一次：PAGERS.review = {go: p => drawReviewPage(p)}；
         翻页由内联 onclick=pagerJump(...) 触发，页面缓存恢复后仍可用。 */
const PAGERS = {};   // id -> {pages, go(page, viaPager)}：pages 随渲染同步，go 只注册一次

function pagerHTML(id, page, pages, total, unit) {
  const p = PAGERS[id] = PAGERS[id] || {};
  p.pages = pages;   // 列表刷新后总页数可能变化，每次渲染同步
  if (pages <= 1) return "";   // 一页放得下：不显示分页条
  return `
  <div class="pager">
    <button class="btn secondary sm" ${page <= 1 ? "disabled" : ""} onclick="pagerJump('${id}',${page - 1})">‹ 上一页</button>
    <input type="range" class="pg-slider" min="1" max="${pages}" value="${page}" title="拖动滑块快速翻页"
      oninput="pagerSlide('${id}',this.value)" onchange="pagerJump('${id}',this.value)">
    <span class="pg-no" id="pgno-${id}">第 ${page} / ${pages} 页</span>
    <button class="btn secondary sm" ${page >= pages ? "disabled" : ""} onclick="pagerJump('${id}',${page + 1})">下一页 ›</button>
    <span class="pg-total">共 ${total} ${unit || "条"}</span>
  </div>`;
}
function pagerJump(id, page) {
  const p = PAGERS[id] || {};
  page = Math.min(Math.max(1, parseInt(page, 10) || 1), p.pages || 1);
  if (p.go) p.go(page, true);   // viaPager：翻页后回到列表顶部
}
function pagerSlide(id, v) {   // 拖动过程只更新页码数字，松手（change）才渲染，拖动才流畅
  const el = $("#pgno-" + id), p = PAGERS[id] || {};
  if (el) el.textContent = `第 ${v} / ${p.pages || 1} 页`;
}
/* 滚到某元素顶部（翻页后回到列表头，留出顶栏高度不遮内容） */
function scrollToSel(sel, offset) {
  const el = $(sel);
  if (el) window.scrollTo({top: el.getBoundingClientRect().top + window.scrollY - (offset || 70)});
}

/* 批次下拉填充（错题确认 / 错因分析 / 讲评备课共用）：返回批次列表。
   选项形如「考试试卷 · 期中考试（2025-11-07 · 42 条）」；value 为 batch_no */
async function fillBatchSelect(selId, current, class_name) {
  const sel = $("#" + selId);
  if (!sel) return [];
  const r = await api("/api/errors/batches" + qstr({class_name})).catch(() => null);
  const bs = (r && r.batches) || [];
  sel.innerHTML = `<option value="">全部批次</option>` + bs.map(b =>
    `<option value="${esc(b.batch_no)}">${esc(b.exam_type || "未分类型")} · ${esc(b.batch_no)}（${(b.first_at || "").slice(0, 10)} · ${b.count} 条）</option>`).join("");
  sel.value = current || "";
  return bs;
}

const groupName = g => ((state.ontology.groups || []).find(x => x.id === g) || {}).name || g;
/* 导航按工作流分四组：作业批改 → 错题管理 → 讲评备课 → 学生管理；
   讲评备课组的三个子标签（讲评方案/配套练习/智能组卷）落在顶栏二级导航 */
const NAV_GROUPS = [
  ["作业批改", [["assignment", "课时作业批改"], ["exam", "考试试卷批改"]]],
  ["错题管理", [["scan", "错题录入"], ["review", "错题确认"], ["analysis", "错因分析"]]],
  ["讲评备课", [["lesson", "讲评方案"], ["practice", "配套练习"], ["paper", "智能组卷"]]],
  ["学生管理", [["students", "学生管理"]]],
];
const NAV_FLAT = new Set(NAV_GROUPS.flatMap(([, ps]) => ps.map(([id]) => id)));
/* 旧地址兼容：升级前的页面地址自动映射到新页并定位对应分段 */
const HASH_ALIAS = {dashboard: "analysis", trends: "analysis", sheet: "lesson", practice: "practice",
                    dictation: "assignment"};

const state = {
  page: "",
  ontology: {categories: [], groups: []},
  catMap: {},        // id -> category
  scanResult: null,
  reviewFilter: "pending",
  sheet: null,
  trendsBy: "week",
  className: "",      // 全局班级筛选（"" = 全部班级）
  classes: [],
  practiceFilter: "draft",
  practicePage: 1,          // 练习列表页码（每页 10 套）
  practiceRange: {from: "", to: ""},  // 生成练习的错例时间范围（留空 = 全部历史）
  practiceCats: [],    // 生成练习已选类别（从讲评方案页跳转或多选添加；一次最多 6 类）
  paperFilter: "draft",   // 组卷段列表过滤
  paperPage: 1,             // 试卷列表页码（每页 10 套）
  paperRange: {from: "", to: ""},     // 组卷的错题时间范围（留空 = 全部历史）
  photoFiles: [],     // 拍照切题：待识别图片（File 对象，最多 10 张）
  splitQuestions: null, // 拍照切题：AI 切出的题目（null = 未切题）
  // ---- 批改模块（课时作业 / 考试试卷：批次文件夹上传 → 分组预检 → AI 解析 → 批改）----
  // 界面只填班级 + 唯一标识；批改类型/单元由文件夹命名自动带出（不展示）
  checkMeta: {class_name: "", unit: "", exam_type: "", batch_no: ""},
  checkMode: "assignment",             // 当前批改页（两页共用引擎，仅预设不同）
  checkReport: null,   // 当前批改报告
  checkHistory: [],    // 最近批改记录（已按页面过滤）
  checkFiles: [],      // 批改：待解析照片 [{path: "批次根/学生/文件名", file: File}]
  checkUpload: null,   // 批改：分组预检结果 {meta, groups, warnings}（null = 未预检）
  parsedPapers: null,  // 批改：AI 解析结果（null = 未解析，每份 = 一名学生）
  roster: [],          // 全部学生名册（解析匹配用）
  analysisTab: "overview",  // 错因分析页内分段：overview 总览 / trends 趋势
  anaBatch: "",             // 错因分析批次筛选（"" = 全部批次；总览与趋势共用）
  reviewBatch: "",          // 错题确认批次筛选（"" = 全部批次）
  reviewPage: 1,            // 错题确认列表页码（每页 50，长列表替换式渲染防卡顿）
  sheetBatches: [],         // 批次列表缓存（/api/errors/batches，随班级刷新）
  sheetRange: {mode: "all", picks: [], date_from: "", date_to: ""},  // 讲评方案数据范围
  lessonTab: "decision",    // 讲评备课组当前子标签：decision 讲评方案 / practice 配套练习 / paper 智能组卷
};

/* ---- 浏览器日志：控制台输出 + 上报服务端（fire-and-forget，绝不递归）---- */
function reportClientLog(level, message, page, stack) {
  try {
    fetch("/api/logs/client", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({level: level || "ERROR",
                            message: String(message || "").slice(0, 2000),
                            page: page || location.hash || "",
                            stack: String(stack || "").slice(0, 4000)})})
      .catch(() => {});   // 上报失败忽略，避免循环
  } catch (e) { /* 忽略 */ }
}
window.addEventListener("error", ev => {
  console.error("[页面错误]", ev.message, ev.filename + ":" + ev.lineno);
  reportClientLog("ERROR", ev.message || "页面脚本错误", ev.filename,
                  ev.error && ev.error.stack);
  toast("页面遇到问题，请稍后重试；如果反复出现，请截图联系技术人员", false);
});
window.addEventListener("unhandledrejection", ev => {
  const msg = (ev.reason && ev.reason.message) || String(ev.reason || "未处理错误");
  console.error("[未处理错误]", msg);
  reportClientLog("ERROR", msg, location.hash, ev.reason && ev.reason.stack);
  toast("操作遇到问题，请稍后重试", false);   // 报错处弹出提示（兜底，不暴露原始错误文案）
});

/* ================= 导航 ================= */
/* 两级导航：一级渲染工作流阶段，二级只渲染当前阶段下的页面（单页阶段不渲染二级） */
function groupOf(page) {
  const hit = NAV_GROUPS.find(([, ps]) => ps.some(([id]) => id === page));
  return hit ? hit[0] : NAV_GROUPS[0][0];
}
function pagesOf(group) {
  return (NAV_GROUPS.find(([g]) => g === group) || [])[1] || [];
}
function renderTabs() {
  const g0 = groupOf(state.page);
  $("#navTabs").innerHTML = NAV_GROUPS.map(([g]) =>
    `<button data-group="${g}" class="${g === g0 ? "active" : ""}">${g}</button>`).join("");
  const pages = pagesOf(g0);
  $("#navSub").innerHTML = pages.length > 1
    ? pages.map(([id, label]) =>
        `<button data-page="${id}" class="${state.page === id ? "active" : ""}">${label}</button>`).join("")
    : "";
}
/* 地址归一：旧地址映射到新页（并定位页内分段）；未知地址回首页 */
function routeFromHash() {
  let p = location.hash.replace("#", "") || "assignment";
  if (HASH_ALIAS[p]) {
    if (HASH_ALIAS[p] === "analysis") state.analysisTab = p === "trends" ? "trends" : "overview";
    else state.lessonTab = p === "practice" ? "practice" : "decision";
    p = HASH_ALIAS[p];
  }
  // 讲评备课组三个子标签页：同步页内分段状态（lesson 对应讲评方案段）
  if (p === "lesson" || p === "practice" || p === "paper") state.lessonTab = p === "lesson" ? "decision" : p;
  if (!NAV_FLAT.has(p)) p = "assignment";
  return p;
}
function go(page) {
  if (page === "lesson" || page === "practice" || page === "paper") state.lessonTab = page === "lesson" ? "decision" : page;
  state.page = page;
  location.hash = page;
  renderTabs();
  const t = $("#toast");
  if (t) { t.classList.remove("show"); clearTimeout(t._h); }
  render({useCache: true});   // 切回之前页面时保留上次的界面输出与滚动位置
}

/* ---- 主题（跟随系统 + 手动切换，localStorage 记忆） ---- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("ea-theme", t); } catch (e) { /* 隐私模式 */ }
  const b = $("#themeBtn");
  if (b) b.textContent = t === "dark" ? "☀️" : "🌙";
}

/* ---- 全局班级选择器 ---- */
async function initClasses() {
  try {
    const r = await api("/api/classes");
    state.classes = r.classes || [];
  } catch (e) { state.classes = []; }
  const sel = $("#classPicker");
  sel.innerHTML = `<option value="">全部班级</option>` +
    state.classes.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  sel.value = state.className;
  sel.onchange = () => {
    state.className = sel.value;
    invalidatePageCache();   // 班级是全局筛选，数据范围变化，所有页面重新渲染
    render();
  };
  const addBtn = $("#addClassBtn");
  if (addBtn) addBtn.onclick = () => openClassModal("");
}

/* ================= 组件片段 ================= */
function qtypeChip(qtype) {
  if (!qtype) return "";
  const cls = qtype === "阅读理解" ? " read" : qtype === "完形填空" ? " cloze" : "";
  return `<span class="qtype-chip${cls}">${esc(qtype)}</span>`;
}

function passageBox(passage, label) {
  const t = (passage == null ? "" : String(passage)).trim();
  if (!t) return "";
  return `<details class="passage-box"><summary>▸ ${esc(label || "阅读 / 完形原文（点击展开）")}</summary><div class="p-txt">${esc(t)}</div></details>`;
}

function syncPassageField() {
  const q = (($("#f-qtype") || {}).value) || "";
  const f = $("#passageField");
  if (f) f.style.display = (q === "阅读理解" || q === "完形填空") ? "" : "none";
}

function catBadge(cid) {
  if (!cid) return `<span class="badge gX">未归类</span>`;
  const c = state.catMap[cid];
  return `<span class="badge g${cid[0]}" title="类别编号 ${esc(cid)}">${esc(c ? c.name : cid)}</span>`;
}
const catName = id => ((state.catMap || {})[id] || {}).name || id;

/* 单元自然排序：Unit 2 排在 Unit 10 前，非 Unit 命名按拼音 */
function unitSort(a, b) {
  const na = parseInt((String(a).match(/\d+/) || [])[0], 10);
  const nb = parseInt((String(b).match(/\d+/) || [])[0], 10);
  if (!isNaN(na) && !isNaN(nb) && na !== nb) return na - nb;
  return String(a).localeCompare(String(b), "zh");
}
function confPct(c) { return Math.round((c || 0) * 100) + "%"; }

/* 学习行为参考数据容错解析：后端已解析为对象，异常时静默降级不破坏列表 */
function safeBehavior(b) {
  if (!b) return null;
  if (typeof b !== "string") return b;
  try { return JSON.parse(b); } catch (e) { return null; }
}

function behaviorNote(behavior) {
  if (!behavior) return "";
  const label = behavior.tag === "E02" ? "粗心倾向" : behavior.tag === "E05" ? "掌握不牢倾向" : behavior.tag;
  return `<div class="notice-strip">学习行为参考（仅供您参考）：该生此类别累计 ${behavior.student_cat_count} 次，本次判为
    <b>${esc(label)}</b>——${esc(behavior.advice || "")}</div>`;
}

function confirmButtons(errId, compact) {
  return `
  <div class="btn-row" style="margin-top:12px">
    <button class="btn primary sm" onclick="confirmErr(${errId},'accept')">✓ 采纳</button>
    <select id="ovr-${errId}" style="width:auto;min-width:200px;font-size:13px" title="改判类别">
      ${ontologyOptions()}
    </select>
    <button class="btn secondary sm" onclick="confirmErr(${errId},'modify')">改判确认</button>
    <button class="btn danger sm" onclick="confirmErr(${errId},'ignore')">忽略</button>
  </div>`;
}

function ontologyOptions(selected) {
  const groups = {};
  state.ontology.categories.forEach(c => { (groups[c.group] = groups[c.group] || []).push(c); });
  return Object.keys(groups).sort().map(g =>
    `<optgroup label="${esc(g)} · ${esc(groupName(g))}">` +
    groups[g].map(c => `<option value="${c.id}" ${c.id === selected ? "selected" : ""}>${esc(c.name)}</option>`).join("") +
    `</optgroup>`).join("");
}


/* ================= 确认操作 ================= */
async function confirmErr(errId, action) {
  const body = {action};
  if (action === "modify") {
    body.override = ($("#ovr-" + errId) || {}).value || "";
  }
  try {
    await api(`/api/errors/${errId}/confirm`, {method: "POST", body});
    toast(action === "accept" ? "已采纳" : action === "modify" ? "已按您的改判确认（统计以改判为准）" : "已忽略，不参与统计");
    if (state.page === "scan" && state.scanResult) { state.scanResult = null; renderScanResult(); }
    if (state.page === "review") loadReview();   // 只刷列表，保留浏览位置（不整页重渲染）
  } catch (e) { toast(e.message, false); }
}
