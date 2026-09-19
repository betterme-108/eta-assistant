"use strict";
/* ================= 错题录入页 ================= */
async function renderScan(main) {
  const students = await api("/api/students" + qstr({class_name: state.className})).catch(() => []);
  state.scanStudents = students;
  // 已有唯一标识（错题库中出现过的）：录入时可从下拉选择，选中自动带出任务类型
  const tagsR = await api("/api/errors/tags" + qstr({class_name: state.className})).catch(() => ({tags: []}));
  state.examTags = tagsR.tags || [];
  const batchOpts = state.examTags.map(t => `<option value="${esc(t.batch_no)}" label="${esc(t.batch_no)} · ${esc(t.exam_type || '未分类')}">`).join("");
  if (!state.scanMode || state.scanMode === "batch") state.scanMode = "photo";
  main.innerHTML = `
  <div class="page-title">错题录入</div>
  <div class="page-sub">拍照或手动输入 → AI 判断错因（36 类常见错因）→ 到「错题确认」页确认后计入统计
    ${state.className ? "" : "<b style='color:#B91C1C'>未选班级（右上角可切换）——录入前建议先选定班级</b>"}</div>
  <div class="steps">
    <div class="step done"><span class="n">1</span>拍照 / 输入</div><span class="step-arrow">›</span>
    <div class="step"><span class="n">2</span>AI 分析（数秒）</div><span class="step-arrow">›</span>
    <div class="step"><span class="n">3</span>您确认后计入统计</div>
  </div>
  <div class="mode-tabs">
    <button id="tabPhoto" class="${state.scanMode === "photo" ? "active" : ""}" onclick="switchScanMode('photo')">📷 拍照切题（推荐）</button>
    <button id="tabSingle" class="${state.scanMode === "single" ? "active" : ""}" onclick="switchScanMode('single')">✎ 单题录入</button>
  </div>
  <div id="scanPhoto">
  <div class="grid c2">
    <div class="card tint-blue">
      <h3>📷 上传错题照片 <span class="hint">1–10 张 · 一页多题、混合题型、跨页均自动切分</span></h3>
      <div class="drop-zone" id="pDropZone" onclick="document.getElementById('p-files').click()">
        <input type="file" id="p-files" accept="image/*" multiple hidden>
        <span style="margin-right:6px">📷</span><b>点击拍照 / 选择多张</b>，或把图片拖到这里<br>
        <span style="font-size:12px">试卷页、书本页、多页连拍都可以——AI 自动切出每一道题并识别题型</span>
        <div id="pPreview"></div>
      </div>
      <div class="grid c2 f2" style="gap:12px">
        <div class="field"><label>任务类型</label>
          <select id="p-exam-type">${examTypeOptions()}</select></div>
        <div class="field"><label>唯一标识 <span class="hint">手动输入，或从已有标识选择</span></label>
          <input type="text" id="p-batch" list="batchList" placeholder="如：1单元2课时 / 期中考试">
          <datalist id="batchList">${batchOpts}</datalist></div>
      </div>
      <div class="btn-row" style="margin-top:6px">
        <button class="btn primary" id="splitBtn" onclick="doPhotoSplit()">开始识别切题</button>
        <span style="font-size:12.5px;color:var(--text2)">${state.photoFiles.length ? state.photoFiles.length + " 张待识别" : "每张约 10–60 秒"}</span>
      </div>
      <div class="notice-strip">拍不清晰或切分不准？可让学生自行裁剪单题照片发来，用「单题录入」上传（切换上方标签）。</div>
      <div class="policy-strip">照片只拍题目本身，不需要出现学生姓名。</div>
    </div>
    <div id="splitBox"></div>
  </div>
  </div>
  <div id="scanSingle" style="display:none">
  <div class="grid c2">
    <div class="card tint-blue">
      <h3>✎ 单题录入 <span class="hint">${state.className ? "当前班级：" + esc(state.className) : "适合：学生裁剪好的单题照片、手动输入文本"}</span></h3>
      <div class="sec-title">① 题目内容<span class="sec-line"></span></div>
      <div class="photo-inline" id="dropZone" onclick="document.getElementById('f-image').click()">
        <input type="file" id="f-image" accept="image/*" hidden>
        <span>📷</span><b>上传本题照片</b>
        <span style="font-size:12px">不手动输入时，自动识别照片里的题目；也可直接在下方粘贴文本</span>
        <div id="dzPreview" style="flex-basis:100%"></div>
      </div>
      <div class="field"><label>题干<em class="req">*</em></label>
        <textarea id="f-question" placeholder="例：He ____ (go) to the park yesterday."></textarea></div>
      <div class="field"><label>题型</label>
        <select id="f-qtype">${qtypeOptions()}</select></div>
      <div class="grid f2" style="gap:12px">
        <div class="field"><label>学生作答（错例）</label>
          <textarea id="f-answer" placeholder="例：has gone"></textarea></div>
        <div class="field"><label>正确答案</label>
          <textarea id="f-correct" rows="2" placeholder="例：went"></textarea></div>
      </div>
      <div class="field" id="passageField" style="display:none"><label>阅读理解 / 完形填空原文</label>
        <textarea id="f-passage" rows="4" placeholder="选阅读理解/完形填空时建议粘贴完整原文：判断依据、错题呈现与练习生成都依赖原文"></textarea></div>
      <div class="sec-title" style="margin-top:18px">② 关联与上下文<span class="sec-line"></span></div>
      <div class="field"><label>关联学生（留空则不关联）</label>
        <div class="stu-picker">
          <input type="text" id="f-student-input" placeholder="输入代号或姓名搜索，如 9101 / 崔梦琪…" autocomplete="off">
          <input type="hidden" id="f-student">
          <div class="sp-list" id="spList"></div>
        </div></div>
      <details class="more-info" open><summary>▸ 任务类型 · 唯一标识（连续录入时自动保留）</summary>
        <div class="grid c2 f2" style="gap:12px">
          <div class="field"><label>任务类型</label>
            <select id="f-exam-type">${examTypeOptions()}</select></div>
          <div class="field"><label>唯一标识</label>
            <input type="text" id="f-batch" list="batchList" placeholder="如：1单元2课时 / 期中考试"></div>
        </div>
      </details>
      <div class="btn-row" style="margin-top:14px">
        <button class="btn primary" id="scanBtn" onclick="doScan()">开始分析</button>
        <button class="btn secondary sm" onclick="resetScanForm()">清空</button>
        <span style="margin-left:auto;font-size:12px;color:var(--text3)">⌘ / Ctrl + Enter 快速分析</span>
      </div>
      <div id="scanCount" style="margin-top:10px;font-size:13px;color:#15803D;font-weight:600"></div>
      <div class="policy-strip">只上传题目本身；学生代号只保存在您这台电脑上，不会发送给 AI。</div>
    </div>
    <div id="scanResultBox"></div>
  </div>
  </div>
  <div style="height:76px"></div>
  <div class="check-dock" id="scanDock" style="display:none">
    <span style="font-size:12.5px;color:var(--text2)">切题结果：逐题核对勾选，点右侧一次提交</span>
    <span style="flex:1"></span>
    <button class="btn primary" id="scanDockBtn" onclick="submitPhotos()">✓ 提交所选 → AI 分析</button>
  </div>`;
  bindScanEvents();
}

/* 录入页事件绑定 + 状态同步渲染（页面缓存恢复时重跑，保留切题/分析结果） */
function bindScanEvents() {
  // 拍照切题：多选上传 + 拖拽绑定
  const pdz = $("#pDropZone"), pfi = $("#p-files");
  if (pdz && pfi) {
    pdz.ondragover = e => { e.preventDefault(); pdz.classList.add("over"); };
    pdz.ondragleave = () => pdz.classList.remove("over");
    pdz.ondrop = e => {
      e.preventDefault(); pdz.classList.remove("over");
      if (e.dataTransfer.files && e.dataTransfer.files.length) appendPhotoFiles(e.dataTransfer.files);
    };
    pfi.onchange = () => { appendPhotoFiles(pfi.files); pfi.value = ""; };
  }
  renderPhotoPreview();
  renderSplitCards(null);
  // 唯一标识输入：选择已有标识时自动带出任务类型（手动输入新标识亦可）
  const pb = $("#p-batch");
  if (pb) pb.oninput = () => autoFillExamType(pb, "#p-exam-type");
  const fb = $("#f-batch");
  if (fb) fb.oninput = () => autoFillExamType(fb, "#f-exam-type");
  // 单题拍照拖放绑定
  const dz = $("#dropZone"), fi = $("#f-image");
  if (dz && fi) {
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); };
    dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = e => {
      e.preventDefault(); dz.classList.remove("over");
      if (e.dataTransfer.files && e.dataTransfer.files.length) { fi.files = e.dataTransfer.files; showImagePreview(); }
    };
    fi.onchange = showImagePreview;
  }
  // 题型↔原文联动（阅读理解/完形需原文）
  const fq = $("#f-qtype");
  if (fq) fq.onchange = syncPassageField;
  syncPassageField();
  bindStudentPicker();
  // Ctrl/Cmd + Enter 快捷提交（连续录入提速）
  ["#f-question","#f-answer","#f-correct"].forEach(id => { const el = $(id); if (el) el.onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); doScan(); } }; });
  updateScanCount();
  renderScanResult();
  switchScanMode(state.scanMode);
}

/* ---- 拍照切题：多图管理 → AI 切题 → 逐题核对 → 一次提交 ---- */
function appendPhotoFiles(fileList) {
  const files = Array.from(fileList || []).filter(f => f.type.startsWith("image/"));
  if (!files.length) return;
  state.photoFiles = state.photoFiles.concat(files).slice(0, 10);
  if (state.photoFiles.length >= 10) toast("已达上限 10 张，多余图片已忽略");
  renderPhotoPreview();
}

function removePhotoFile(idx) {
  state.photoFiles.splice(idx, 1);
  renderPhotoPreview();
}

function renderPhotoPreview() {
  const box = $("#pPreview");
  if (!box) return;
  if (!state.photoFiles.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;justify-content:center">` +
    state.photoFiles.map((f, i) => `<div style="position:relative">
      <img src="${URL.createObjectURL(f)}" alt="第${i + 1}张" style="height:64px;border-radius:8px;box-shadow:0 1px 5px rgba(0,0,0,.18);display:block">
      <span style="position:absolute;left:4px;top:4px;background:rgba(0,0,0,.55);color:#fff;font-size:10.5px;font-weight:700;border-radius:5px;padding:1px 6px">${i + 1}</span>
      <a title="移除" style="position:absolute;right:-7px;top:-7px;width:20px;height:20px;border-radius:50%;background:#DC2626;color:#fff;font-size:12px;display:flex;align-items:center;justify-content:center;cursor:pointer;text-decoration:none" onclick="event.stopPropagation();removePhotoFile(${i})">✕</a>
    </div>`).join("") + `</div>`;
}

async function doPhotoSplit() {
  const btn = $("#splitBtn");
  if (!state.photoFiles.length) { toast("请先上传照片（可多选/拖拽，最多 10 张）", false); return; }
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> 识别切题中（${state.photoFiles.length} 张，每张约 10–60 秒）…`;
  try {
    const images = await Promise.all(state.photoFiles.map(f => fileToBase64(f)));
    const r = await api("/api/errors/scan-photos", {method: "POST", body: {images_base64: images}});
    state.splitQuestions = r.questions;
    renderSplitCards(r);
    toast(`已切出 ${r.questions.length} 道题，请逐题核对后提交`);
  } catch (e) {
    toast(e.message, false);
    const box = $("#splitBox");
    if (box) box.innerHTML = `<div class="card"><div class="empty"><div class="icon">📷</div>${esc(e.message)}<br><span style="font-size:12px">可改用「单题录入」逐题上传，或让学生裁剪单题后发送</span></div></div>`;
  }
  btn.disabled = false; btn.textContent = "开始识别切题";
}

const SPLIT_QTYPES = ["单选", "多选", "判断题", "语法选择", "完形填空", "阅读理解", "口语应用", "任务型阅读", "完成句子", "概要补全", "书面表达", "词语运用", "其他"];

// 全部题型（错题录入/批改共用）：听力等音频类题目暂不支持
const ALL_QTYPES = ["单选", "多选", "判断题", "语法选择", "完形填空", "阅读理解", "口语应用", "任务型阅读", "完成句子", "概要补全", "书面表达", "词语运用", "听写", "其他"];
const qtypeOptions = sel => `<option value="">未指定</option>` +
  ALL_QTYPES.map(t => `<option ${t === sel ? "selected" : ""}>${t}</option>`).join("");

/* 任务类型（两大任务：课时作业 / 考试试卷） */
const EXAM_TYPES = ["课时作业", "考试试卷"];
const examTypeOptions = sel => `<option value="">未指定</option>` +
  EXAM_TYPES.map(t => `<option value="${t}" ${t === sel ? "selected" : ""}>${t}</option>`).join("");

/* 唯一标识输入：匹配到已有标识时自动带出任务类型 */
function autoFillExamType(batchInput, typeSelId) {
  const v = (batchInput.value || "").trim();
  const hit = (state.examTags || []).find(t => t.batch_no === v && t.exam_type);
  if (!hit) return;
  const sel = $(typeSelId);
  if (sel) sel.value = hit.exam_type;
}

function renderSplitCards(r) {
  const box = $("#splitBox");
  if (!box) return;
  if (!r || !state.splitQuestions) {
    const dock = $("#scanDock");
    if (dock) dock.style.display = "none";
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">✂️</div>
      <b style="font-size:14px">切题结果将显示在这里</b><br>
      <span style="font-size:12.5px;line-height:2;display:inline-block;text-align:left">
      ① 左侧上传试卷/书本照片（可多张，支持多页连拍）<br>
      ② 点「开始识别切题」——AI 自动切出每一道题并识别题型<br>
      ③ 逐题核对：取消勾选不要的题、编辑识别偏差、填学生代号<br>
      ④ 点「提交所选」——一次全部分析，到「错题确认」页确认计入统计</span></div></div>`;
    return;
  }
  const qs = state.splitQuestions;
  box.innerHTML = `<div class="card" style="padding:16px 18px">
    <h3 style="margin-bottom:4px">✂️ 切出 ${qs.length} 道题 <span class="hint">${r.pages} 页 · AI 生成，请逐题核对（可编辑/取消勾选）</span></h3>
    ${qs.map((q, i) => `
      <div class="split-q card" data-i="${i}" style="margin:10px 0;padding:12px 14px;box-shadow:none;border:1px solid var(--sep)">
        <div class="result-head" style="margin-bottom:8px">
          <input type="checkbox" id="sqk-${i}" checked style="width:17px;height:17px;accent-color:#16A34A;flex-shrink:0" onclick="updateSplitPick()">
          <select id="sq-type-${i}" style="width:auto;font-size:12.5px">${SPLIT_QTYPES.map(t=>`<option ${t===q.qtype?"selected":""}>${t}</option>`).join("")}</select>
          <span class="badge gX">第 ${i + 1} 题</span>
          ${(q.option_issues || []).length ? `<span class="badge gC" title="${esc(q.option_issues.join("；"))}">⚠ 选项已自动规范</span>` : ""}
          <a title="移除该题" style="margin-left:auto;cursor:pointer;color:#B91C1C;font-size:13px" onclick="removeSplitCard(${i})">✕ 不要这题</a>
        </div>
        <textarea id="sq-q-${i}" rows="3" style="font-size:13.5px" placeholder="题干（可编辑修正识别结果）">${esc(q.question)}</textarea>
        ${q.options && q.options.length ? `<div style="font-size:12.5px;color:var(--text2);margin:5px 0 0;line-height:1.8">${q.options.map(o => esc(o)).join("<br>")}</div>` : ""}
        ${q.passage ? `<details class="passage-box"><summary>▸ 原文（已随题带出，展开可编辑）</summary>
          <textarea id="sq-passage-${i}" rows="4" style="font-size:12.5px">${esc(q.passage)}</textarea></details>` : ""}
        <div class="grid c2 f2" style="gap:8px;margin-top:8px">
          <input type="text" id="sq-ans-${i}" placeholder="学生作答（可空）" style="font-size:13px">
          <input type="text" id="sq-corr-${i}" placeholder="正确答案（可空）" style="font-size:13px">
        </div>
        <input type="text" id="sq-stu-${i}" placeholder="关联学生代号，多人用逗号分隔（不填 = 班级记录）" list="stuCodes" style="font-size:13px;margin-top:8px">
      </div>`).join("")}
    <div class="btn-row" style="margin-top:10px">
      <button class="btn primary" id="photoSubmitBtn" onclick="submitPhotos()">✓ 提交所选 <span id="pickN">${qs.length}</span> 题 → AI 分析</button>
      <span style="font-size:12.5px;color:var(--text2)">提交后到「错题确认」页确认计入统计</span>
    </div>
  </div>`;
  // 底部固定提交条与卡片内按钮同步（切题多时无需拉到底部）
  const dock = $("#scanDock"), dbtn = $("#scanDockBtn");
  if (dock) {
    dock.style.display = qs.length ? "" : "none";
    if (dbtn) dbtn.textContent = `✓ 提交所选 ${qs.length} 题 → AI 分析`;
  }
  const dl = document.createElement("datalist");
  dl.id = "stuCodes";
  dl.innerHTML = (state.scanStudents || []).map(s => `<option value="${esc(s.student_code)}">${esc(s.name_local || "")}</option>`).join("");
  box.appendChild(dl);
}

function removeSplitCard(i) {
  const card = document.querySelector(`.split-q[data-i="${i}"]`);
  if (!card) return;
  const ck = $("#sqk-" + i);
  if (ck) ck.checked = false;
  card.style.display = "none";
  updateSplitPick();
}

function updateSplitPick() {
  const n = $$(".split-q").filter(c => {
    const ck = $("#sqk-" + c.dataset.i);
    return ck && ck.checked && c.style.display !== "none";
  }).length;
  const el = $("#pickN");
  if (el) el.textContent = n;
  const dbtn = $("#scanDockBtn");
  if (dbtn) dbtn.textContent = `✓ 提交所选 ${n} 题 → AI 分析`;
}

async function submitPhotos() {
  const btn = $("#photoSubmitBtn");
  const qs = [];
  $$(".split-q").forEach(card => {
    const i = card.dataset.i;
    const ck = $("#sqk-" + i);
    if (!ck || !ck.checked || card.style.display === "none") return;
    qs.push({
      qtype: $("#sq-type-" + i).value,
      question: $("#sq-q-" + i).value.trim(),
      answer: $("#sq-ans-" + i).value.trim(),
      correct: $("#sq-corr-" + i).value.trim(),
      passage: (($("#sq-passage-" + i) || {value: ""}).value || "").trim(),
      students: (($("#sq-stu-" + i).value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean)),
    });
  });
  if (!qs.length) { toast("请勾选至少一题", false); return; }
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> 分析中（${qs.length} 题）…`;
  try {
    const r = await api("/api/errors/scan-photos-submit", {method: "POST", body: {
      questions: qs,
      exam_type: (($("#p-exam-type") || {}).value) || null,
      batch_no: (($("#p-batch") || {value: ""}).value || "").trim() || null,
      class_name: state.className || null,
    }});
    state.scanCount = (state.scanCount || 0) + r.stats.inserted;
    updateScanCount();
    state.photoFiles = [];
    state.splitQuestions = null;
    renderPhotoPreview();
    renderSplitCards(null);
    const fails = r.results.filter(x => !x.ok);
    toast(`已入库 ${r.stats.inserted} 条（相同题目只分析一次），${fails.length} 题未提交`);
    if (fails.length) {
      $("#splitBox").innerHTML = `<div class="card"><h3>未提交的题目</h3>${fails.map(x =>
        `<div class="batch-row"><span class="badge gR">✗</span><div style="flex:1">
          <div style="color:#B91C1C">${esc(x.msg)}</div>
          <div class="clamp" style="margin-top:3px;color:var(--text2)">${esc(x.question || "")}</div></div></div>`).join("")}</div>`;
    }
  } catch (e) { toast(e.message, false); }
  btn.disabled = false;
  btn.textContent = "✓ 提交所选题 → AI 分析";
}

async function doScan() {
  const btn = $("#scanBtn");
  const stuCode = resolveStudent();
  const payload = {
    question: $("#f-question").value.trim(),
    answer: $("#f-answer").value.trim(),
    correct: $("#f-correct").value.trim(),
    qtype: $("#f-qtype").value,
    passage: (($("#f-passage") || {value: ""}).value || "").trim(),
    exam_type: $("#f-exam-type").value || null,
    batch_no: $("#f-batch").value.trim() || null,
    student_code: stuCode || null,
    class_name: state.className || null,
  };
  const file = ($("#f-image") || {}).files;
  const ocrMode = !payload.question && file && file[0];
  if (ocrMode) {
    payload.image_base64 = await fileToBase64(file[0]);
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> 正在识别照片文字（约 30–90 秒）→ 随后 AI 分析…';
  } else {
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> AI 分析中…';
  }
  if (!payload.question && !payload.image_base64) { btn.disabled = false; btn.textContent = "开始分析"; toast("请填写题干或上传错题照片", false); return; }
  try {
    state.scanResult = await api("/api/errors/scan", {method: "POST", body: payload});
    // 结果卡展示关联对象（您确认前可核对；未关联则不显示）
    const stuHit = stuCode ? (state.scanStudents || []).find(s => s.student_code === stuCode) : null;
    state.scanResult.student_label = stuCode
      ? stuCode + (stuHit && stuHit.name_local ? " · " + stuHit.name_local : "") : "";
    // 拍照识别拆出的题型回填（连续录入同类照片时省事）；原文已展示在右侧结果卡
    if (ocrMode && state.scanResult.qtype && $("#f-qtype")) { $("#f-qtype").value = state.scanResult.qtype; }
    syncPassageField();
    // 连续录入：清空本题字段，保留题型/任务类型/唯一标识等上下文
    ["f-question","f-answer","f-correct","f-passage","f-student-input"].forEach(id => { const el = $("#"+id); if (el) el.value = ""; });
    const hid = $("#f-student"); if (hid) hid.value = "";
    removeImage();
    state.scanCount = (state.scanCount || 0) + 1;
    updateScanCount();
    renderScanResult();
    toast("分析完成，已加入待确认队列");
    const q = $("#f-question"); if (q) q.focus();
  } catch (e) { toast(e.message, false); }
  btn.disabled = false; btn.textContent = "开始分析";
}

function resetScanForm() {
  state.scanResult = null;
  ["f-question","f-answer","f-correct","f-passage","f-student-input"].forEach(id => { const el = $("#"+id); if (el) el.value = ""; });
  const hid = $("#f-student"); if (hid) hid.value = "";
  removeImage();
  renderScanResult();
  const q = $("#f-question"); if (q) q.focus();
}

function showImagePreview() {
  const fi = $("#f-image"), box = $("#dzPreview");
  if (!fi || !box) return;
  const f = fi.files && fi.files[0];
  if (!f) { box.innerHTML = ""; return; }
  const r = new FileReader();
  r.onload = () => {
    box.innerHTML = `<img class="dz-img" src="${r.result}" alt="错题照片预览">
      <div class="dz-file">✓ ${esc(f.name)}（${Math.round(f.size / 1024)} KB）
      <a style="cursor:pointer;margin-left:6px;color:#B91C1C" onclick="event.stopPropagation();removeImage()">移除</a></div>`;
  };
  r.readAsDataURL(f);
}

function removeImage() {
  const fi = $("#f-image");
  if (fi) fi.value = "";
  showImagePreview();
}

function updateScanCount() {
  const txt = state.scanCount ? `本次已录 ${state.scanCount} 条，到「错题确认」页确认 →` : "";
  const a = $("#scanCount");
  if (a) a.textContent = txt;
}

/* ---- 学生搜索选择器：输入代号或姓名实时匹配（名册大时下拉难找） ---- */
function bindStudentPicker() {
  const inp = $("#f-student-input"), list = $("#spList");
  if (!inp || !list) return;
  inp.oninput = () => {
    const hid = $("#f-student"); if (hid) hid.value = "";
    const q = inp.value.trim().toLowerCase();
    if (!q) { list.style.display = "none"; return; }
    const hits = (state.scanStudents || []).filter(s =>
      (s.student_code || "").toLowerCase().includes(q) ||
      (s.name_local || "").toLowerCase().includes(q)).slice(0, 30);
    list.innerHTML = hits.length ? hits.map(s =>
      `<div class="sp-item" data-code="${esc(s.student_code)}"><span>${esc(s.student_code)}</span><span class="nm">${esc(s.name_local || "")}</span></div>`).join("")
      : `<div class="sp-item" style="color:var(--text3);cursor:default">无匹配学生（名册在「学生」页维护）</div>`;
    list.style.display = "block";
    list.querySelectorAll(".sp-item[data-code]").forEach(el => {
      el.onmousedown = ev => {  // mousedown 先于 blur，避免列表消失
        ev.preventDefault();
        const nm = el.querySelector(".nm").textContent;
        $("#f-student").value = el.dataset.code;
        inp.value = el.dataset.code + (nm ? " · " + nm : "");
        list.style.display = "none";
      };
    });
  };
  inp.onblur = () => setTimeout(() => { list.style.display = "none"; }, 150);
  inp.onfocus = () => { if (inp.value.trim()) { inp.oninput(); } };
  // 键盘操作：↑↓ 选择、Enter 确认（名册大时键盘比鼠标快）
  inp.onkeydown = e => {
    if (list.style.display === "none") return;
    const items = Array.from(list.querySelectorAll(".sp-item[data-code]"));
    if (!items.length) return;
    let idx = items.findIndex(el => el.classList.contains("sel"));
    if (e.key === "ArrowDown") { e.preventDefault(); idx = idx < 0 ? 0 : (idx + 1) % items.length; }
    else if (e.key === "ArrowUp") { e.preventDefault(); idx = idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length; }
    else if (e.key === "Enter") {
      e.preventDefault();
      const pick = items[idx >= 0 ? idx : 0];
      if (pick) pick.dispatchEvent(new Event("mousedown"));
      return;
    } else { return; }
    items.forEach(el => el.classList.remove("sel"));
    items[idx].classList.add("sel");
    items[idx].scrollIntoView({ block: "nearest" });
  };
}

function resolveStudent() {
  const hid = $("#f-student");
  const raw = (($("#f-student-input") || {value: ""}).value || "").trim();
  if (!raw) return "";
  if (hid && hid.value) return hid.value;  // 已从建议列表选中
  const hit = (state.scanStudents || []).find(s => s.student_code === raw);
  if (hit) return hit.student_code;        // 直接输入了完整代号
  toast("学生「" + raw + "」未匹配到名册，本题将不关联学生（请从建议列表选择）", false);
  return "";
}

/* ---- 录入模式切换 ---- */
function switchScanMode(m) {
  state.scanMode = m;
  const tabs = {photo: "#tabPhoto", single: "#tabSingle"};
  Object.entries(tabs).forEach(([k, id]) => { const el = $(id); if (el) el.classList.toggle("active", m === k); });
  $("#scanPhoto").style.display = m === "photo" ? "" : "none";
  $("#scanSingle").style.display = m === "single" ? "" : "none";
}



/* 图片 → 压缩后的 base64（去 DataUrl 头）：canvas 重绘统一长边 ≤2000px、JPEG 85%。
   手机原图（3000-4000px、3-8MB）直传会在识别端被二次压缩，小字与手写细节丢失且
   上传慢；浏览器 <img> 解码 JPEG 默认按 EXIF 摆正方向，重绘即完成方向修正。
   解码/绘制失败时回退原图 FileReader，保证功能不受阻。 */
const IMG_MAX_EDGE = 2000, IMG_JPEG_QUALITY = 0.85;

function fileToBase64(file) {
  const rawBase64 = () => new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1] || "");
    r.onerror = rej;
    r.readAsDataURL(file);
  });
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    const fallback = () => { URL.revokeObjectURL(url); resolve(rawBase64()); };
    img.onload = () => {
      try {
        const scale = Math.min(1, IMG_MAX_EDGE / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        const b64 = (canvas.toDataURL("image/jpeg", IMG_JPEG_QUALITY).split(",")[1]) || "";
        URL.revokeObjectURL(url);
        resolve(b64 || rawBase64());
      } catch (e) { fallback(); }
    };
    img.onerror = fallback;
    img.src = url;
  });
}

function renderScanResult() {
  const box = $("#scanResultBox");
  if (!box) return;
  const r = state.scanResult;
  if (!r) {
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">🧭</div>
      <b style="font-size:14px">分析结果将显示在这里</b><br>
      <span style="font-size:12.5px;line-height:2;display:inline-block;text-align:left">
      ① 左侧粘贴题干与作答（或拍照上传）<br>
      ② 点「开始分析」（Ctrl+Enter），数秒后显示错因类别、判断依据与教学要点<br>
      ③ 到「错题确认」页确认后，此题才计入统计</span><br>
      <span style="font-size:12.5px;color:var(--text3)">AI 只给出错因类别与判断依据，不下结论、不评价学生</span></div></div>`;
    return;
  }
  const ai = r.ai || {};
  box.innerHTML = `
  <div class="card">
    <div class="result-head">
      ${catBadge(ai.category_id)}
      ${qtypeChip(r.qtype)}
      <span class="ai-tag">AI 生成 · 待您确认</span>
      ${ai.needs_review ? '<span class="badge gC">AI 把握不足，请您判断</span>' : ""}
      ${r.student_label ? `<span class="badge gX">👤 关联 ${esc(r.student_label)}</span>` : ""}
    </div>
    <div class="result-cat">${esc(ai.category_id ? (state.catMap[ai.category_id]||{}).name || "" : "未能归类")}</div>
    <div class="conf-bar"><div style="width:${confPct(ai.confidence)}"></div></div>
    <dl class="kv">
      <dt>判断依据</dt><dd>${esc(ai.evidence || "—")}</dd>
      <dt>教学要点</dt><dd>${esc(ai.teaching_point || "—")}</dd>
      <dt>AI 把握</dt><dd>${confPct(ai.confidence)}</dd>
      ${r.ocr_note ? `<dt>识别</dt><dd>${esc(r.ocr_note)}（AI 识别·请核对）</dd>` : ""}
    </dl>
    ${passageBox(r.passage)}
    ${behaviorNote(r.behavior)}
    ${confirmButtons(r.error_id)}
    <div class="policy-strip">您确认后此题计入统计；改判会留下记录（用于统计您的改判率）。</div>
  </div>`;
}
