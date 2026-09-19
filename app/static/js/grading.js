"use strict";
/* ================= 批改模块（课时作业 / 考试试卷） ================= */
/* 主观题题型（与批改引擎判定一致）：参考答案可空，由 AI 读作答判分 */
const SUBJECTIVE_QTYPES = ["完成句子", "概要补全", "书面表达", "任务型阅读"];

/* ---- 本次批改信息：班级 / 唯一标识（选好文件夹自动带出，批改后随时可改；唯一标识也用作本次标题） ---- */
function checkMetaHTML() {
  const m = state.checkMeta || {};
  return `
  <div class="card tint-blue">
    <h3>🗂 本次批改信息 <span class="hint">班级 / 唯一标识——选好文件夹会自动带出，批改后也可随时修改</span></h3>
    <div class="grid f2" style="gap:12px">
      <div class="field"><label>班级</label>
        <input type="text" id="m-class" list="classDatalist2" placeholder="如 九(1)班（留空 = 不分班）" value="${esc(m.class_name)}">
        <datalist id="classDatalist2">${(state.classes || []).map(c => `<option>${esc(c)}</option>`).join("")}</datalist></div>
      <div class="field"><label>唯一标识 <span style="color:var(--text3)">（定位这次提交，也用作标题）</span></label>
        <input type="text" id="m-batch" placeholder="如 1单元2课时 / 第一次月考 / 半期考试" value="${esc(m.batch_no)}"></div>
    </div>
  </div>`;
}
function readCheckMeta() {
  const v = id => { const el = $("#" + id); return el ? el.value : "" };
  state.checkMeta.class_name = v("m-class").trim();   // 只改可见两项，批改类型/单元跟随文件夹留档
  state.checkMeta.batch_no = v("m-batch").trim();
}
function bindCheckMeta() {
  ["m-class", "m-batch"].forEach(id => {
    const el = $(id);
    if (el) el.oninput = readCheckMeta;
  });
}

/* ---- 批改上传：选择整个批次文件夹（班级-批改类型-唯一标识）→ 分组预检 → AI 解析 ---- */
function bindCheckUpload() {
  const dz = $("#checkDropZone"), fi = $("#check-files");
  if (!dz || !fi) return;
  dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = async e => {
    e.preventDefault(); dz.classList.remove("over");
    const items = Array.from(e.dataTransfer.items || []);
    const entry = items.length && items[0].webkitGetAsEntry && items[0].webkitGetAsEntry();
    if (entry && entry.isDirectory) {
      appendCheckSelection(await filesFromDrop(items));
    } else if (e.dataTransfer.files && e.dataTransfer.files.length) {
      toast("请上传整个文件夹：外层按「班级-批改类型-唯一标识」命名，里面每位学生一个子文件夹", false);
    }
  };
  fi.onchange = () => { appendCheckSelection(fi.files); fi.value = ""; };
  renderCheckPreview();
}
/* 拖拽文件夹：递归读出全部照片（含每位学生的子文件夹） */
function filesFromDrop(items) {
  const walk = entry => new Promise(resolve => {
    if (entry.isFile) {
      entry.file(f => resolve([{file: f, path: entry.fullPath.replace(/^\//, "")}]),
                 () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const out = [];
      const readBatch = () => reader.readEntries(async es => {
        if (!es.length) {
          const nested = await Promise.all(out.map(walk));
          resolve(nested.flat());
        } else {
          out.push(...es);
          readBatch();   // readEntries 每次最多返回 100 条，需读到空
        }
      }, () => resolve([]));
      readBatch();
    } else resolve([]);
  });
  const entries = items.map(it => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
  return Promise.all(entries.map(walk)).then(lists => lists.flat());
}
/* 加入所选文件夹内容：只收照片，构建「批次根/学生/文件名」路径，去重后立即预检 */
function appendCheckSelection(fileList) {
  const picked = Array.from(fileList || [])
    .map(f => ({file: f.file || f, path: (f.path || f.webkitRelativePath || f.name).replace(/\\/g, "/")}))
    .filter(x => x.file.type && x.file.type.startsWith("image/"));
  if (!picked.length) return;
  const known = new Set(state.checkFiles.map(x => x.path));
  const fresh = picked.filter(x => !known.has(x.path));
  if (!fresh.length) { toast("这些照片已在列表中"); return; }
  const snapshot = state.checkFiles.slice();   // 预检失败可回滚本次选择
  state.checkFiles = state.checkFiles.concat(fresh);
  organizeCheckUpload(snapshot);
}
/* 分组预检：把所选路径交给系统核对命名规范，并按学生分组；
   exam_type 随当前批改页带上（两段式「班级-唯一标识」命名时作为批改类型，页面即类型） */
async function organizeCheckUpload(snapshot) {
  const paths = state.checkFiles.map(x => x.path);
  try {
    const r = await api("/api/check/organize-upload", {method: "POST", body: {paths, exam_type: pageExamType()}});
    state.checkUpload = r;
    applyFolderMeta(r.meta || {}, r.warnings || []);
    renderCheckPreview();
  } catch (e) {
    if (snapshot) state.checkFiles = snapshot;   // 命名不合规：回滚本次选择
    renderCheckPreview();
    toast(e.message, false);
  }
}
/* 当前批改页对应的批改类型（课时作业批改 / 考试试卷批改是两个子页面，页面即类型） */
function pageExamType() { return state.checkMode === "exam" ? "考试试卷" : "课时作业"; }
/* 文件夹名 → 班级 / 唯一标识预填（解析值更准）；批改类型、单元跟随文件夹留档（不展示） */
function applyFolderMeta(meta, warnings) {
  const m = state.checkMeta;
  [["class_name", "m-class"], ["batch_no", "m-batch"]].forEach(([k, id]) => {
    if (meta[k]) { m[k] = meta[k]; const el = $("#" + id); if (el) el.value = meta[k]; }
  });
  m.unit = meta.unit || "";   // 单元以文件夹为准（供趋势分析，界面不展示）
  if (meta.exam_type) {   // 类型以文件夹为准（考试试卷的提交自动归入「考试试卷批改」页历史）
    m.exam_type = meta.exam_type;
  }
  if (meta.folder) toast("已识别批次：" + meta.folder + "（班级与唯一标识已自动带出，可修改）");
  (warnings || []).forEach(w => toast(w, false));
}
/* 移除一名学生（整个分组）后重新预检 */
function removeCheckGroup(gi) {
  const g = ((state.checkUpload || {}).groups || [])[gi];
  if (!g) return;
  const drop = new Set(g.files);
  state.checkFiles = state.checkFiles.filter(x => !drop.has(x.path.split("/").slice(1).join("/")));
  organizeCheckUpload(null);
}
/* 移除单张照片（按选择顺序的索引）后重新预检 */
function removeCheckPhotoIdx(idx) {
  state.checkFiles.splice(idx, 1);
  organizeCheckUpload(null);
}
function renderCheckPreview() {
  const box = $("#checkPreview");
  if (!box) return;
  updateCheckDock();   // 文件增删后同步底部操作条（识别并解析可用性）
  const up = state.checkUpload;
  if (!state.checkFiles.length || !up) {
    box.innerHTML = state.checkFiles.length
      ? `<div class="loading-block" style="padding:8px;font-size:12.5px">正在核对文件夹命名…</div>` : "";
    const t0 = $("#checkFileTip");
    if (t0) t0.textContent = state.checkFiles.length ? "核对中…" : "先选好文件夹再解析";
    return;
  }
  const byPath = {};
  state.checkFiles.forEach((x, i) => byPath[x.path] = {file: x.file, idx: i});
  const groups = up.groups || [], root = (up.meta || {}).folder || "";
  box.innerHTML = (up.warnings || []).map(w =>
      `<div class="notice-strip" style="margin:10px 0 0">${esc(w)}</div>`).join("") +
    `<div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">` +
    groups.map((g, gi) => `
      <div style="border:1px solid var(--sep);border-radius:10px;padding:8px 10px;text-align:left">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <b style="font-size:13.5px">${esc(g.name)}</b>
          <span class="badge gX">${g.files.length} 张照片</span>
          <a title="移除这名学生" style="margin-left:auto;color:var(--red);font-size:12px;cursor:pointer" onclick="event.stopPropagation();removeCheckGroup(${gi})">移除</a>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          ${g.files.map(rel => {
            const hit = byPath[root + "/" + rel];
            return hit ? `<div style="position:relative">
              <img src="${URL.createObjectURL(hit.file)}" alt="${esc(rel)}" title="${esc(rel)}" style="height:52px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.15);display:block">
              <a title="移除这张" style="position:absolute;right:-6px;top:-6px;width:18px;height:18px;border-radius:50%;background:#DC2626;color:#fff;font-size:11px;display:flex;align-items:center;justify-content:center;cursor:pointer;text-decoration:none" onclick="event.stopPropagation();removeCheckPhotoIdx(${hit.idx})">✕</a>
            </div>` : "";
          }).join("")}
        </div>
      </div>`).join("") + `</div>`;
  const tip = $("#checkFileTip");
  if (tip) tip.textContent = `${groups.length} 名学生 · ${state.checkFiles.length} 张照片待解析`;
}
async function doParsePhotos() {
  const btn = $("#parseBtn");
  if (!state.checkFiles.length) { toast("请先选择整个批次文件夹（外层按「班级-批改类型-唯一标识」或「班级-唯一标识」命名）", false); return; }
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> 识别解析中（${state.checkFiles.length} 张，每张约 30–120 秒）…`;
  try {
    const items = await Promise.all(state.checkFiles.map(async x => ({
      path: x.path, image_base64: await fileToBase64(x.file)})));
    const r = await api("/api/check/photo-parse", {method: "POST", body: {
      items, exam_type: pageExamType(),
      class_name: state.className || null,   // 名册匹配限定右上角所选班级
    }});
    state.parsedPapers = r.papers || [];
    state.mdDirty = {};   // 新解析结果覆盖旧原文，清掉旧的“修改未应用”标记
    (r.warnings || []).forEach(w => toast(w, false));
    renderParseResults();
    const ok = state.parsedPapers.filter(p => p.questions.length).length;
    if (ok) toast(`已解析 ${ok} 名学生的作答：请核对题目、参考答案与学生，然后开始批改`);
    else toast("未能从照片解析出题目——请检查照片是否清晰、完整", false);
  } catch (e) { toast(e.message, false); }
  btn.disabled = false; btn.textContent = "📷 识别并解析";
}
/* 合并题目（批改答案表）：同一份作业多张照片时，按题号取首次出现的题干与 AI 参考答案 */
function mergedKeyQuestions() {
  const byNo = {};
  (state.parsedPapers || []).forEach(p => (p.questions || []).forEach(q => {
    if (!byNo[q.no]) byNo[q.no] = Object.assign({}, q);
  }));
  return Object.values(byNo).sort((a, b) => a.no - b.no);
}
/* 智能体提取原文可直接编辑：修改后重新结构化（复用后端 photo-parse 同一套解析规则） */
function stripMdTagLines(md) {   // 去掉前端标注的来源照片行（【p1.jpg】），避免干扰重新解析
  return (md || "").split("\n").filter(l => !/^【[^】]+】\s*$/.test(l.trim())).join("\n");
}
function markMdDirty(i) {
  state.mdDirty = state.mdDirty || {};
  state.mdDirty[i] = true;
  const tag = $("#ps-md-dirty-" + i);
  if (tag) tag.textContent = "有修改未应用";
}
async function applyMdEdit(i) {
  const p = (state.parsedPapers || [])[i];
  const ta = $("#ps-md-" + i);
  if (!p || !ta) return;
  const btn = $("#ps-md-apply-" + i);
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> 重新解析中…`;
  try {
    const r = await api("/api/check/md-reparse", {method: "POST", body: {
      exam_type: pageExamType(), markdown: stripMdTagLines(ta.value)}});
    p.questions = r.questions || [];
    p.extract_mds = [stripMdTagLines(ta.value)];   // 编辑后的文本回写为提取原文
    state.mdDirty = state.mdDirty || {};
    delete state.mdDirty[i];
    if ((r.questions || []).length) toast("已按修改重新解析该生题目与作答");
    else toast("已应用修改，但未解析出题目——请检查原文格式后重试", false);
    renderParseResults();
    updateCheckDock();
  } catch (e) {
    toast(e.message, false);
    btn.disabled = false;
    btn.textContent = "✓ 应用修改并重新解析";
  }
}
/* 题目按大题连续分组：相邻同 section 归一组（顺序已由后端按书本大题序排好）；
   完形/阅读的文章与方框选词清单（passage）提升到组级展示，不再只挂在首题上 */
function groupBySection(qs) {
  const groups = [];
  (qs || []).forEach(q => {
    const name = q.section || "其他题目";
    const g = groups[groups.length - 1];
    if (g && g.name === name) { g.questions.push(q); return; }
    groups.push({name, questions: [q]});
  });
  groups.forEach(g => {
    g.passage = (g.questions.find(q => q.passage) || {}).passage || "";
  });
  return groups;
}
function renderParseResults() {
  const box = $("#parseBox"), intro = $("#parseIntro");
  if (!box) return;
  const papers = state.parsedPapers;
  if (intro) intro.style.display = papers ? "none" : "";
  if (!papers) { box.innerHTML = ""; return; }
  const keyQs = mergedKeyQuestions();
  const usable = papers.filter(p => p.questions.length);
  if (!keyQs.length) {
    box.innerHTML = `<div class="card"><div class="empty"><div class="icon">📷</div>
      未能从这些照片解析出题目<br><span style="font-size:12.5px">请重拍（光线充足、整页入镜、文字清晰）后再试一次</span></div></div>`;
    return;
  }
  const typeOpts = t => (t && !SPLIT_QTYPES.includes(t) ? `<option selected>${esc(t)}</option>` : "") +   // 试卷标注的题型不套白名单，保留原样
    SPLIT_QTYPES.map(x => `<option ${x === t ? "selected" : ""}>${x}</option>`).join("");
  const keyGroups = groupBySection(keyQs);
  const anyMd = papers.some(p => (p.extract_mds || []).length);
  box.innerHTML = `
  <div class="card">
    <h3>📄 解析结果 · 提取原文 <span class="hint">${usable.length}/${papers.length} 名学生解析成功 · 共 ${keyQs.length} 题（跨页已合并）</span></h3>
    <div class="policy-strip" style="margin:6px 0 0">原文可直接编辑——识别错字、漏题可直接改，点「✓ 应用修改并重新解析」后题目与作答按修改刷新；参考答案与分值由 AI 给出，需修改时展开下方「核对修正」区。</div>
    ${papers.map((p, i) => `
      <div class="parse-stu${p.questions.length ? "" : " parse-bad"}">
        <div class="parse-q-head">
          <span class="badge gX">学生 ${i + 1}</span>
          ${p.questions.length ? `<span class="badge gE">${p.questions.length} 题</span>`
                               : `<span class="badge gR">未解析出题目</span>`}
          ${(p.files || []).length ? `<span class="badge gB">${p.files.length} 张照片</span>` : ""}
          ${(p.failed_files || []).length ? `<span class="badge gR" title="${esc(p.failed_files.join("、"))}">${p.failed_files.length} 张识别失败</span>` : ""}
          ${p.name ? `<span style="font-size:12.5px;color:var(--text2)">${p.name_source === "folder" ? "文件夹名" : "照片识别"}：${esc(p.name)}</span>` : ""}
          ${p.student_code
            ? `<span class="badge gE" style="margin-left:auto">${esc(p.student_code)}${p.name_local ? " · " + esc(p.name_local) : ""}</span>`
            : `<span class="badge gR" style="margin-left:auto" title="名册里没有匹配的学生，不参与批改；可在「学生管理」页补充该生">未匹配名册</span>`}
        </div>
        ${(p.extract_mds || []).length
          ? `<textarea id="ps-md-${i}" class="md-edit" rows="10" spellcheck="false" oninput="markMdDirty(${i})">${esc(p.extract_mds.join("\n\n"))}</textarea>
          <div class="md-edit-bar">
            <button class="btn secondary sm" id="ps-md-apply-${i}" onclick="applyMdEdit(${i})">✓ 应用修改并重新解析</button>
            <span id="ps-md-dirty-${i}" class="md-dirty">${state.mdDirty && state.mdDirty[i] ? "有修改未应用" : ""}</span>
          </div>`
          : `<div style="font-size:12.5px;color:var(--text3);padding:6px 0">本批没有提取原文，题目与作答请在下方「核对修正」区查看。</div>`}
      </div>`).join("")}
    <details class="md-src"${anyMd ? "" : " open"}>
      <summary>⚙️ 核对修正：参考答案 / 题型 / 分值 / 学生作答（默认用 AI 解析值，有误再展开修改）</summary>
      ${keyGroups.map(g => `
      <div class="parse-sec">
        <div class="parse-sec-head">
          <span class="badge gA">${esc(g.name)}</span>
          <span class="hint">${g.questions.length} 题 · 题型 ${esc(g.questions[0].qtype)}</span>
        </div>
        ${g.passage ? passageBox(g.passage, "本大题原文 / 选词素材") : ""}
        ${g.questions.map(q => keyQuestionBlock(q, typeOpts)).join("")}
      </div>`).join("")}
      ${papers.map((p, i) => `
      <div class="parse-stu" style="border-style:dashed">
        <div class="parse-q-head">
          <span class="badge gX">学生 ${i + 1} 作答</span>
          ${p.questions.length ? "" : `<span class="badge gR">未解析出题目</span>`}
        </div>
        ${p.questions.length ? keyGroups.map(g => `
          <div class="parse-ans-sec">${esc(g.name)} · ${g.questions.length} 题</div>
          <div class="grid f2" style="gap:8px;margin-top:8px">
          ${g.questions.map(q => {
            const hit = p.questions.find(x => x.no === q.no);
            const src = hit && hit.file ? hit.file.split("/").pop() : "";
            const flag = hit && hit.confidence === "medium"
              ? '<span class="ans-flag">待核对</span>' : "";
            const marks = hit && (hit.grading_marks || []).length
              ? `<span class="ans-marks">老师已批 ${esc(hit.grading_marks.join(" "))}</span>` : "";
            const hint = (q.question || "").replace(/\s+/g, " ").trim().slice(0, 30);
            return `<div class="field" style="margin:0"><label>第 ${q.no} 题${src ? `（${esc(src)}）` : ""}${flag}${marks}<span class="ans-q-hint">${esc(hint)}${(q.question || "").length > 30 ? "…" : ""}</span></label>
              <input type="text" id="ps-ans-${i}-${q.no}" value="${esc(hit ? hit.student_answer : "")}" placeholder="（未识别到作答）"></div>`;
          }).join("")}
          </div>`).join("") : ""}
      </div>`).join("")}
    </details>
    <div class="policy-strip" style="margin-top:12px">未选学生的作答不会参与批改；判错的题自动进入「错题确认」页——核对无误后点页面底部「开始批改」。</div>
  </div>`;
  updateCheckDock();
}
/* 底部固定操作条：内容再长，「识别并解析 / 开始批改」始终可见（解析后同步状态） */
function updateCheckDock() {
  const pb = $("#photoCheckBtn"), pf = $("#parseBtn");
  if (!pb) return;
  const papers = state.parsedPapers;
  const n = papers ? mergedKeyQuestions().length : 0;
  const usable = papers ? papers.filter(p => p.questions.length).length : 0;
  pb.disabled = !n;
  pb.textContent = papers && n ? `✓ 开始批改（${usable} 名学生 · ${n} 题）` : "开始批改（先解析照片）";
  pf.disabled = !state.checkFiles.length;
}
/* 卡1 单题块：题干 + 参考解析折叠 */
function keyQuestionBlock(q, typeOpts) {
  return `
      <div class="parse-q">
        <div class="parse-q-head">
          <span class="badge gA">第 ${q.no} 题</span>
          ${(q.option_issues || []).length ? `<span class="badge gC" title="${esc(q.option_issues.join("；"))}">⚠ 选项已自动规范</span>` : ""}
          <select id="pq-type-${q.no}" style="width:auto;font-size:12.5px" onchange="parseTypeBadge(${q.no}, this.value)">${typeOpts(q.qtype)}</select>
          <label class="score-mini">分值<input type="number" id="pq-score-${q.no}" min="0.5" step="0.5" value="1"></label>
          <span class="badge gB subj-badge-${q.no}" style="${SUBJECTIVE_QTYPES.includes(q.qtype) ? "" : "display:none"}">主观题 · AI 判分</span>
        </div>
        <textarea id="pq-q-${q.no}" rows="2" style="font-size:13.5px" placeholder="题干（可编辑修正识别结果）">${esc(q.question)}</textarea>
        ${q.options && q.options.length ? `<div class="opts-view">${q.options.map(o => esc(o)).join("<br>")}</div>` : ""}
        <input type="text" id="pq-ans-${q.no}" placeholder="参考答案（智能解析已给出，请核对；主观题可留空）" value="${esc(q.suggested_answer || "")}" style="margin-top:8px">
      </div>`;
}
/* 题型切换时同步“主观题”徽章（主观题参考答案可留空，由 AI 判分） */
function parseTypeBadge(no, t) {
  const b = $(".subj-badge-" + no);
  if (b) b.style.display = SUBJECTIVE_QTYPES.includes(t) ? "" : "none";
}

/* ---- 课时作业 / 考试试卷批改页（共用批改引擎：拍照上传 → AI 解析 → 规则 + AI 判卷） ---- */
async function renderAssignment(main) { await renderCheckingPage(main, "assignment"); }
async function renderExam(main) { await renderCheckingPage(main, "exam"); }
async function renderCheckingPage(main, mode) {
  const kind = "assignment";
  if (state.checkMode !== mode) {   // 作业页 ⇄ 考试页：清掉另一页残留的上传/解析状态，两页数据不串
    const rptExam = (((state.checkReport || {}).meta || {}).exam_type) === "考试试卷";
    state.checkMode = mode;
    state.checkFiles = [];
    state.checkUpload = null;
    state.parsedPapers = null;
    state.mdDirty = {};
    state.checkMeta = {class_name: "", unit: "", exam_type: "", batch_no: ""};
    // 报告仅在其归属与目标页一致时保留（从历史载入时先设报告再切页）
    if (state.checkReport && rptExam !== (mode === "exam")) state.checkReport = null;
  }
  state.checkMeta.exam_type =   // 类型：文件夹解析值优先，否则按当前页预设（切页不留旧值）
    (((state.checkUpload || {}).meta || {}).exam_type) ||
    (mode === "exam" ? "考试试卷" : "课时作业");
  // 右上角已选班级 → 本次批改信息预填该班（文件夹解析出的班级会覆盖此值）
  if (!state.checkMeta.class_name && state.className) state.checkMeta.class_name = state.className;
  state.roster = await api("/api/students" + qstr({class_name: state.className})).catch(() => []);
  await refreshCheckHistory();   // 历史按批改类型与右上角班级隔离
  const isExam = mode === "exam";
  main.innerHTML = `
  <div class="page-title">${isExam ? "考试试卷批改" : "课时作业批改"}</div>
  <div class="page-sub">上传整个${isExam ? "试卷" : "作业"}文件夹（外层「班级-批改类型-唯一标识」，里面每位学生一个子文件夹）→ AI 解析题目与作答 → 客观题规则判卷 / 主观题 AI 判定 / 作文分维评分 → 判错的题自动进「错题确认」
    <br><span style="font-size:12.5px">${isExam ? "适用于月考 / 半期 / 期中期末等整卷" : "适合每天的课时作业与课后练习"}；听力等音频类题目暂不支持</span></div>
  ${checkMetaHTML()}
  <div class="grid c2">
    <div class="card tint-blue">
      <h3>📁 上传${isExam ? "试卷" : "作业"}文件夹 <span class="hint">一次一批 · 按学生分组</span></h3>
      <div class="drop-zone" id="checkDropZone" onclick="document.getElementById('check-files').click()">
        <input type="file" id="check-files" accept="image/*" webkitdirectory multiple hidden>
        <span style="margin-right:6px">📁</span><b>点击选择整个文件夹</b>，或把文件夹拖到这里<br>
        <span style="font-size:12px">文件夹按「<b>班级-批改类型-唯一标识</b>」命名（如 九年级5班-考试试卷-半期考试；两段式「班级-唯一标识」也可，类型以当前页为准），里面每位学生一个子文件夹、放该生本次的照片（多张 = 多页）</span>
        <div id="checkPreview"></div>
      </div>
      <div class="policy-strip" style="margin-top:10px">学生姓名只用于与名册匹配；发送给 AI 的只有题目与作答本身，不含学生信息。选好文件夹后点页面底部「识别并解析」。</div>
    </div>
    <div class="card tint-purple" id="parseIntro">
      <h3>🤖 AI 批改五步走 <span class="hint">全程可核对、可修改</span></h3>
      <div class="howto">
        <div class="howto-item"><div class="howto-n">1</div><div><b>选择文件夹</b><span>整个批次文件夹一次上传：外层「班级-批改类型-唯一标识」，每位学生一个子文件夹，多张照片按文件名顺序即是页序</span></div></div>
        <div class="howto-item"><div class="howto-n">2</div><div><b>识别并解析</b><span>按学生分组解析题干与手写作答，多张照片自动合并跨页大题，并给出参考答案</span></div></div>
        <div class="howto-item"><div class="howto-n">3</div><div><b>核对修正</b><span>提取原文可直接编辑，改完重新解析；再逐题核对题干与参考答案（AI 答案可能有误）、逐生核对作答</span></div></div>
        <div class="howto-item"><div class="howto-n">4</div><div><b>开始批改</b><span>客观题即时判卷，主观题 AI 判定，作文分维评分</span></div></div>
        <div class="howto-item"><div class="howto-n${state.checkReport ? " on" : ""}" id="howtoStep5">5</div><div><b>查看报告 → 去确认错题</b><span>批改完成自动生成成绩单（逐生得分 / 逐题判定 / 作文分维评语），可导出全班或单生报告；判错的题自动进「错题确认」页，确认后才计入错因统计与讲评备课</span></div></div>
      </div>
    </div>
  </div>
  <div id="parseBox"></div>
  ${renderCheckReportHTML(kind)}
  ${renderCheckHistoryHTML(kind)}
  <div style="height:76px"></div>
  <div class="check-dock" id="checkDock">
    <button class="btn secondary" id="parseBtn" onclick="doParsePhotos()">📷 识别并解析</button>
    <span id="checkFileTip" style="font-size:12.5px;color:var(--text2)">${state.checkFiles.length ? state.checkFiles.length + " 张待解析" : "每张约 30–120 秒"}</span>
    <span style="flex:1"></span>
    <button class="btn primary" id="photoCheckBtn" onclick="doCheckFromPhotos()">开始批改</button>
  </div>`;
  bindCheckingEvents();
}

/* 批改页事件绑定 + 状态同步渲染（页面缓存恢复时重跑，保留解析/批改结果） */
function bindCheckingEvents() {
  bindCheckUpload();
  bindCheckMeta();
  renderParseResults();
  updateCheckDock();
}

/* 解析结果 → 既有批改引擎：题目核对表 + 学生作答 → 规则判卷 / AI 判定 */
async function doCheckFromPhotos() {
  const keyQs = mergedKeyQuestions();
  if (!keyQs.length) { toast("还没有可批改的题目：请先上传照片并「识别并解析」", false); return; }
  const key = keyQs.map(q => {
    const qt = (($("#pq-q-" + q.no) || {value: q.question}).value || "").trim();
    return {
      no: q.no,
      qtype: (($("#pq-type-" + q.no) || {value: q.qtype}).value || "").trim(),
      question: qt,
      options: q.options || [],          // 选项/原文分开传：判错的题随错题入库，
      passage: q.passage || "",          // 「错题确认」页才能完整展示（选项+原文）
      answer: (($("#pq-ans-" + q.no) || {value: ""}).value || "").trim(),
      score: parseFloat(($("#pq-score-" + q.no) || {value: 1}).value) || 1,
    };
  });
  // 客观题缺参考答案会全部判错——提交前提醒（主观题由 AI 判分，可留空）
  const missing = key.filter(k => !k.answer && !SUBJECTIVE_QTYPES.includes(k.qtype)).map(k => k.no);
  if (missing.length &&
      !confirm(`第 ${missing.join("、")} 题还没有参考答案，将按全部答错处理（错题会进确认页）。\n建议先在上方补上参考答案。仍要开始批改吗？`)) return;
  // 原文编辑未应用 → 批改前提醒（批改按当前解析结果进行，避免静默丢弃修改）
  const dirtyN = Object.keys(state.mdDirty || {}).length;
  if (dirtyN &&
      !confirm(`有 ${dirtyN} 名学生的原文修改尚未应用。\n建议先点每生原文下方的「✓ 应用修改并重新解析」。\n仍按当前解析结果开始批改吗？`)) return;
  const answers = (state.parsedPapers || []).map((p, i) => ({
    student_code: (p.student_code || "").trim(),   // 学生由文件夹名/照片识别确定，老师无需再选
    name: p.name || "",
    files: p.files || [],
    items: keyQs.map(q => {
      const hit = p.questions.find(x => x.no === q.no);
      const val = (($("#ps-ans-" + i + "-" + q.no) || {value: ""}).value || "").trim();
      return {no: q.no,
        answer: val,
        file: hit ? (hit.file || "") : ""};
    }),
  })).filter(a => a.student_code);
  const unmatched = (state.parsedPapers || []).filter(p => p.questions.length && !p.student_code).length;
  if (!answers.length) { toast("没有可批改的学生：请在「学生管理」页补充名册后再试", false); return; }
  if (unmatched) toast(`提示：${unmatched} 名学生未匹配名册，本次不参与批改`, false);
  readCheckMeta();
  const btn = $("#photoCheckBtn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> 批改中（主观题需 AI 判定，稍等）…`;
  try {
    const r = await api("/api/check/assignment", {method: "POST", body: {
      key, answers,
      unit: state.checkMeta.unit || null,
      class_name: state.checkMeta.class_name || null,
      exam_type: state.checkMeta.exam_type || null,
      batch_no: state.checkMeta.batch_no || null,
      folder: (state.checkUpload && state.checkUpload.meta && state.checkUpload.meta.folder) || null,
    }});   // 标题不单独填：后端用唯一标识作标题
    state.checkReport = r;
    state.reportStu = 0;   // 新报告默认显示第一名学生
    state.checkFiles = [];
    state.checkUpload = null;
    state.parsedPapers = null;
    state.mdDirty = {};
    render();
    toast("批改完成：错题已自动进入「错题确认」页");
  } catch (e) {
    toast(e.message, false);
    btn.disabled = false;
    updateCheckDock();
  }
}

/* ---- 成绩单渲染 ---- */
function verdictChip(v) {
  const map = {correct: ["gE", "对"], misspell: ["gR", "拼写错"], blank: ["gX", "空白"],
               wrong: ["gR", "错"], uncertain: ["gC", "待判定"]};
  const m = map[v] || ["gX", String(v || "—")];
  return `<span class="badge ${m[0]}">${m[1]}</span>`;
}
/* 单个学生的成绩单卡：报告区每次只显示一名学生（下拉切换），卡片内容不变 */
function reportStuCard(r, s) {
    const scoreBadge = s.score == null ? "gX" : s.score === 100 ? "gE" : s.score >= 60 ? "gC" : "gR";
    const items = `
      <div style="margin-top:6px">${s.items.map(it => `
        <div style="padding:7px 0;border-bottom:.5px solid var(--sep)">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <b>${it.no}.</b>${qtypeChip(it.qtype || "题")} ${verdictChip(it.verdict)}
            ${it.subjective ? `<span class="ai-tag">AI 判定</span>` : ""}
            ${it.got != null ? `<span style="font-size:12px;color:var(--text2)">得 ${it.got} / ${it.score} 分</span>` : ""}
            <span style="margin-left:auto;font-size:12px;color:var(--text2)">${it.note ? esc(it.note) : ""}</span>
          </div>
          <div style="font-size:12.5px;color:var(--text2);margin:3px 0 2px" class="clamp">${esc(it.question)}</div>
          ${(it.options || []).length ? `<div class="opts-view" style="margin:2px 0 4px">${it.options.map(o => esc(o)).join("<br>")}</div>` : ""}
          <div style="font-size:13px">作答：${esc(it.answer || "（空白）")}${it.correct ? ` <span style="color:var(--text2)">· 标准：${esc(it.correct)}</span>` : ""}</div>
          ${it.rubric && it.rubric.length ? `<details class="passage-box" style="margin-top:5px"><summary>▸ 分维评语（AI 评分）</summary>
            ${it.rubric.map(d => `<div style="font-size:12.5px;margin:4px 0"><b>${esc(d.name)}：</b>${esc(d.comment)}</div>`).join("")}</details>` : ""}
        </div>`).join("")}</div>`;
    return `<div class="card" style="margin:10px 0;padding:14px 16px">
      <div class="result-head">
        <b>${esc(s.student_code || "未填代号")}</b>
        ${s.name ? `<span style="font-size:13px;color:var(--text2)">${esc(s.name)}</span>` : ""}
        ${s.in_roster ? "" : `<span class="badge gX">不在名册</span>`}
        <span style="margin-left:auto" class="badge ${scoreBadge}">${s.score != null ? s.score + " 分" : "待判定"}</span>
        <span style="font-size:12px;color:var(--text2)">${s.correct_n}/${s.total || s.judged_n} 正确</span>
        <a class="btn secondary sm" style="text-decoration:none;margin-left:6px" target="_blank" title="该生个人报告（可打印/另存 PDF，只描述错误不排名）" href="/api/check/sessions/${r.session_id}/export?student_code=${encodeURIComponent(s.student_code || "")}">⇩ 单生报告</a>
      </div>
      ${s.files && s.files.length ? `<div style="font-size:12px;color:var(--text3);margin:2px 0 4px">📎 本次照片：${s.files.map(f => esc(f.split("/").pop())).join("、")}</div>` : ""}
      ${items}</div>`;
}
function renderCheckReportHTML(kind) {
  const r = state.checkReport;
  if (!r || r.kind !== kind) return "";
  const rExam = ((r.meta || {}).exam_type) === "考试试卷";
  if (rExam !== (state.checkMode === "exam")) return "";   // 报告按页归属显示：作业/考试不串页
  const st = r.stats || {}, meta = r.meta || {};
  const stat = (cls, num, label) => `<div class="stat ${cls}"><div class="num">${num}</div><div class="label">${label}</div></div>`;
  const stats = [stat("blue", st.students || 0, "学生数"),
     stat("purple", st.avg_score != null ? st.avg_score : "—", "平均分"),
     stat("orange", st.wrong_items || 0, "错题数"),
     stat("green", st.uncertain || 0, "待判定"),
     stat("blue", st.inserted_errors || 0, "已收集错题")];
  const results = r.results || [];
  const stuIdx = Math.min(Math.max(state.reportStu || 0, 0), Math.max(results.length - 1, 0));   // 新报告学生更少时回落
  const stuOpts = results.map((s, i) => `<option value="${i}"${i === stuIdx ? " selected" : ""}>${esc(s.student_code || "未填代号")}${s.name ? " · " + esc(s.name) : ""}（${s.score != null ? s.score + " 分" : "待判定"}）</option>`).join("");
  const body = `<div style="display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap">
      <label for="reportStuSel" style="font-size:13px;color:var(--text2)">学生</label>
      <select id="reportStuSel" style="max-width:340px" onchange="switchReportStu(this.value)">${stuOpts}</select>
      <span class="hint">页面每次显示一名学生 · 共 ${results.length} 人</span>
    </div>
    <div id="reportStuBody">${results.length ? reportStuCard(r, results[stuIdx]) : ""}</div>`;
  const info = [meta.class_name, meta.batch_no, (meta.items || 0) + " 题"].filter(Boolean).join(" · ");
  return `<div class="card" id="checkReportCard">
    <div class="result-head">
      <b style="font-size:16px">${esc(r.title || "批改结果")}</b>
      <span style="margin-left:auto;display:flex;gap:8px">
        <button class="btn primary sm" onclick="exportCheck()">⬇ 导出全班结果</button>
        <button class="btn secondary sm" title="每生一个独立报告文件，打包一次下载" onclick="exportCheckAllStu()">⬇ 全部单生报告</button>
        <button class="btn secondary sm" onclick="editSessionMeta(${r.session_id})">✎ 修改信息</button>
      </span>
    </div>
    <div style="font-size:12.5px;color:var(--text2);margin:6px 0 12px">${esc(info)}</div>
    <div class="grid c4" style="gap:10px">${stats.join("")}</div>
    ${body}
    <div class="notice-strip" style="margin-top:10px">判错内容已自动进入「错题确认」页（待确认），确认后才计入错因统计；「待判定」的题由您人工把关；导出报告附带 AI 总结与学习建议。
      <a style="margin-left:6px;font-weight:700;cursor:pointer;color:var(--brand);white-space:nowrap" onclick="go('review')">去错题确认 →</a></div>
  </div>`;
}
/* 下拉切换学生：只替换学生卡区域，不重拉整页 */
function switchReportStu(v) {
  const r = state.checkReport;
  if (!r) return;
  const results = r.results || [];
  const i = Math.min(Math.max(parseInt(v) || 0, 0), results.length - 1);
  state.reportStu = i;
  const box = $("#reportStuBody");
  if (box) box.innerHTML = results.length ? reportStuCard(r, results[i]) : "";
}
function exportCheck() {
  const r = state.checkReport;
  if (!r || !r.session_id) { toast("还没有可导出的批改结果", false); return; }
  location.href = "/api/check/sessions/" + r.session_id + "/export";
}
/* 全部单生报告：每生一个独立 HTML 打包 zip 一次下载（省去逐生点击导出） */
function exportCheckAllStu() {
  const r = state.checkReport;
  if (!r || !r.session_id) { toast("还没有可导出的批改结果", false); return; }
  toast("正在打包全班单生报告（AI 总结逐生生成，学生多时需稍等）…");
  location.href = "/api/check/sessions/" + r.session_id + "/export-all";
}

/* ---- 修改批改信息（班级 / 唯一标识） ---- */
function editSessionMeta(id) {
  const r = state.checkReport;
  if (!r) return;
  const m = r.meta || {};
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.id = "metaOverlay";
  ov.innerHTML = `<div class="modal-card" style="max-width:480px">
    <div class="result-head"><b>修改批改信息</b><a style="cursor:pointer;margin-left:auto" onclick="closeMetaOverlay()">✕</a></div>
    <div class="field" style="margin-top:12px"><label>班级</label><input type="text" id="e-class" value="${esc(m.class_name || "")}"></div>
    <div class="field" style="margin-top:12px"><label>唯一标识 <span style="color:var(--text3);font-weight:400">（也用作标题）</span></label><input type="text" id="e-batch" placeholder="如 1单元2课时 / 半期考试" value="${esc(m.batch_no || "")}"></div>
    <div class="btn-row">
      <button class="btn primary" onclick="saveSessionMeta(${id})">保存</button>
      <button class="btn secondary sm" onclick="closeMetaOverlay()">取消</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
}
function closeMetaOverlay() { const ov = $("#metaOverlay"); if (ov) ov.remove(); }
async function saveSessionMeta(id) {
  const body = {
    class_name: (($("#e-class") || {value: ""}).value || "").trim() || null,
    batch_no: (($("#e-batch") || {value: ""}).value || "").trim() || null,
  };
  try {
    const r = await api(`/api/check/sessions/${id}`, {method: "PATCH", body});
    r.report.session_id = id;
    state.checkReport = r.report;
    closeMetaOverlay();
    render();
    toast("已保存修改");
  } catch (e) { toast(e.message, false); }
}

/* ---- 批改历史 ---- */
/* 拉取本页的批改历史（服务端按 exam_type 过滤：作业页取非考试，考试页取考试试卷） */
async function refreshCheckHistory() {
  const hist = await api("/api/check/sessions" +
    qstr({kind: "assignment", exam_type: pageExamType(),
          class_name: state.className, limit: 100})).catch(() => ({}));
  state.checkHistory = hist.sessions || [];
}
function renderCheckHistoryHTML(kind) {
  const list = state.checkHistory || [];
  const label = state.checkMode === "exam" ? "考试" : "作业";
  return `<div class="card">
    <h3>🕘 最近${label}批改 <span class="hint">可查看、导出、修改信息、删除</span></h3>
    ${list.length ? list.map(s => `
      <div class="list-row">
        <div class="grow">
          <div class="title">${esc(s.title || "未命名批改")}${s.exam_type ? ` <span class="qtype-chip">${esc(s.exam_type)}</span>` : ""}</div>
          <div class="desc">${esc(s.class_name || "未分班")}${s.batch_no ? " · " + esc(s.batch_no) : ""} · ${s.students} 人 · 平均 ${s.avg_score != null ? s.avg_score : "—"} 分 · 错题 ${s.wrong_items}${s.uncertain ? " · 待判定 " + s.uncertain : ""}</div>
        </div>
        <button class="btn secondary sm" onclick="loadCheckSession(${s.id},'${kind}')">查看</button>
        <a class="btn secondary sm" style="text-decoration:none" href="/api/check/sessions/${s.id}/export">导出</a>
        <button class="btn secondary sm" style="color:var(--red)" title="删除这条批改记录" onclick="confirmDeleteSession(${s.id})">删除</button>
      </div>`).join("") : `<div class="hint">还没有批改记录——填写上方内容后点「开始批改」。</div>`}
  </div>`;
}
/* 删除确认弹层：默认保留错题（仅解除关联），勾选后连错题一并删除 */
function confirmDeleteSession(id) {
  const s = (state.checkHistory || []).find(x => x.id === id) || {};
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.id = "delOverlay";
  ov.innerHTML = `<div class="modal-card" style="max-width:480px">
    <div class="result-head"><b>删除批改记录</b><a style="cursor:pointer;margin-left:auto" onclick="closeDelOverlay()">✕</a></div>
    <div style="font-size:13.5px;margin:10px 0 2px">确定删除「${esc(s.title || "未命名批改")}」？删除后本次批改的成绩单与已上传的批改照片不可恢复。</div>
    <label style="display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:var(--text2);margin:8px 0 2px;cursor:pointer">
      <input type="checkbox" id="del-purge" style="width:auto;margin-top:2px">同时删除本次批改收集的错题（不勾选则保留错题，仅解除与本次批改的关联，错题仍在「错题确认」页）
    </label>
    <div class="btn-row">
      <button class="btn primary" style="background:#DC2626" onclick="doDeleteSession(${id})">删除</button>
      <button class="btn secondary" onclick="closeDelOverlay()">取消</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
}
function closeDelOverlay() { const ov = $("#delOverlay"); if (ov) ov.remove(); }
async function doDeleteSession(id) {
  const purge = !!($("#del-purge") || {}).checked;
  try {
    const r = await api(`/api/check/sessions/${id}` + (purge ? "?purge_errors=true" : ""),
                        {method: "DELETE"});
    closeDelOverlay();
    if (state.checkReport && state.checkReport.session_id === id) state.checkReport = null;
    await refreshCheckHistory();
    render();
    toast(r.purged ? `已删除批改记录（连同本次收集的 ${r.removed_errors} 道错题）`
                   : `已删除批改记录（${r.removed_errors} 道错题已保留，解除关联）`);
  } catch (e) { toast(e.message, false); }
}
async function loadCheckSession(id, kind) {
  try {
    const r = await api("/api/check/sessions/" + id);
    r.report.session_id = id;
    state.checkReport = r.report;
    state.reportStu = 0;   // 载入历史默认显示第一名学生
    let target = kind;
    if (kind === "assignment") {   // 按批改类型落到作业页或考试页
      target = (r.report.meta || {}).exam_type === "考试试卷" ? "exam" : "assignment";
    }
    state.page = target;
    location.hash = target;
    renderTabs();
    render();
    toast("已载入：" + (r.title || "批改记录"));
  } catch (e) { toast(e.message, false); }
}
