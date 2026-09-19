"use strict";
/* ================= 学生管理页 ================= */
async function renderStudents(main) {
  const students = await api("/api/students").catch(() => []);
  const byClass = {};
  students.forEach(s => { const k = s.class_name || "未分班"; (byClass[k] = byClass[k] || []).push(s); });
  // 班级分组 = 已建的班级（含空班级，可在这里改名/删除）+ 有学生的班级 + “未分班”置底
  const classNames = [...new Set([
    ...(state.classes || []).filter(c => c),
    ...Object.keys(byClass).filter(k => k && k !== "未分班"),
  ])].sort();
  if (byClass["未分班"]) classNames.push("未分班");
  const classBlocks = classNames.map(cn => `
    <h3 style="margin-top:14px">${esc(cn)} <span class="hint">${(byClass[cn] || []).length} 人</span>
    ${cn !== "未分班" ? `<a data-cls="${esc(cn)}" onclick="openClassModal(this.dataset.cls)" style="font-size:13px;cursor:pointer;opacity:.5;text-decoration:none;margin-left:6px" title="班级改名 / 删除">✎ 管理</a>` : `<span class="hint" style="margin-left:6px">在顶栏「＋」新建班级，或在批量导入中写明班级</span>`}</h3>
    ${(byClass[cn] || []).length ? `<div style="display:flex;flex-wrap:wrap;gap:8px">${byClass[cn].map(s =>
      `<span class="stu-chip">
        <button class="badge gA" style="font-size:13px;padding:5px 12px;cursor:pointer;border:none;font-family:inherit" title="点击查看个人错题档案" onclick="openStudentProfile('${esc(s.student_code)}')">${esc(s.student_code)}${s.name_local ? " · " + esc(s.name_local) : ""}</button>
        <a title="编辑（改名/换班/删除均在此）" onclick="editStudent('${esc(s.student_code)}')">✎</a>
      </span>`).join("")}</div>` : `<div class="hint" style="margin:2px 0 0 2px">暂无学生——可在左侧添加或导入时写明本班</div>`}`).join("");
  main.innerHTML = `
  <div class="page-title">学生管理</div>
  <div class="page-sub">仅您可见：显示“代号 · 姓名”，点击学生可查看个人错题档案（供辅导参考，不产生评价与排名）；点 ✎ 编辑信息（删除也在此，防误触）</div>
  <div class="grid c2">
    <div class="card tint-blue">
      <h3>新增 / 导入名单</h3>
      <div class="grid c2 f2" style="gap:10px">
        <div class="field"><label>代号</label><input type="text" id="ns-code" placeholder="9101"></div>
        <div class="field"><label>姓名</label><input type="text" id="ns-name" placeholder="崔梦琪"></div>
      </div>
      <div class="btn-row" style="margin-bottom:14px">
        <button class="btn secondary" onclick="addStudent()">＋ 添加一人</button>
        <span style="font-size:12px;color:var(--text2)">${state.className ? "默认归入当前班级 " + esc(state.className) : "未选班级时需在批量导入中写明班级"}</span>
      </div>
      <div class="field"><label>批量导入：每行一个「代号,姓名」或「代号,姓名,班级」（中文逗号、空格分隔均可）</label>
        <textarea id="stuText" rows="7" placeholder="9101,王一帆&#10;9102,李知行&#10;9103"></textarea></div>
      <button class="btn primary" onclick="importStudents()">导入</button>
      <div class="policy-strip" style="margin-top:14px"><b>三条使用禁令</b>：<br>
      ① 讲评方案与练习不得下发给学生排名比对；<br>
      ② 数据不得用于学生综合素质评价；<br>
      ③ 学生代号与姓名的对应关系只保存在您这台电脑上。</div>
    </div>
    <div class="card">
      <h3>已录入学生 <span class="hint">点人名看档案</span>
        <a href="/api/students/reports.zip${qstr({class_name: state.className})}" download title="每生一个错题档案报告（统计 + 错因分布 + 月度趋势 + 最近错题），打包 zip 一次下载" style="font-size:13px;cursor:pointer;opacity:.65;text-decoration:none;white-space:nowrap;margin-left:auto">⤓ 全班学生报告${state.className ? "（本班）" : "（全部）"}</a>
        <a href="/api/students/export.csv${qstr({class_name: state.className})}" download title="代号/姓名/错题计数/TOP3错因/批改次数与均分（不排名）" style="font-size:13px;cursor:pointer;opacity:.65;text-decoration:none;white-space:nowrap;margin-left:8px">⤓ 导出综合信息${state.className ? "（本班）" : "（全部）"}</a></h3>
      ${students.length ? classBlocks
        : `<div class="empty"><div class="icon">👥</div>暂无名单——建议先导入再开始录入错题</div>`}
    </div>
  </div>`;
}

/* ---- 班级管理：新建 / 改名 / 删除（动态初始化，无需预先建好） ---- */
function openClassModal(name) {
  const existing = name || "";
  document.querySelectorAll(".overlay").forEach(el => el.remove());
  const ov = document.createElement("div");
  ov.className = "overlay"; ov.id = "classOverlay";
  ov.innerHTML = `<div class="modal-card" style="max-width:420px">
    <h3 style="margin:0 0 14px">${existing ? "班级设置：" + esc(existing) : "新建班级"}</h3>
    <div class="field"><label>班级名称</label>
      <input type="text" id="cls-name" placeholder="如：九(1)班" value="${esc(existing)}"></div>
    <div class="btn-row">
      <button class="btn primary" onclick="saveClass(this.dataset.old)" data-old="${esc(existing)}">${existing ? "保存改名" : "创建"}</button>
      <button class="btn secondary" onclick="closeClassModal()">取消</button>
    </div>
    ${existing ? `<div class="btn-row" style="margin-top:10px"><button class="btn danger sm" onclick="deleteClass(this.dataset.old)" data-old="${esc(existing)}">删除该班级</button></div>` : ""}
    <div class="hint" style="margin-top:12px">班级、学生都可以在使用中随时添加与修改，无需提前全部建好。</div>
  </div>`;
  ov.onclick = ev => { if (ev.target === ov) ov.remove(); };
  document.body.appendChild(ov);
  const inp = $("#cls-name"); if (inp) { inp.focus(); inp.select(); }
}

function closeClassModal() { const ov = $("#classOverlay"); if (ov) ov.remove(); }

async function saveClass(oldName) {
  const name = (($("#cls-name") || {}).value || "").trim();
  if (!name) { toast("请输入班级名称", false); return; }
  try {
    if (oldName) {
      const r = await api(`/api/classes/${encodeURIComponent(oldName)}`, {method: "PUT", body: {new_name: name}});
      if (state.className === oldName) state.className = r.name;
      toast("班级已改名");
    } else {
      const r = await api("/api/classes", {method: "POST", body: {name}});
      state.className = r.name;
      toast(`已新建班级 ${name}，已自动切换到这个班级`);
    }
    closeClassModal();
    await initClasses();
    render();
  } catch (e) { toast(e.message, false); }
}

async function deleteClass(name) {
  if (!confirm(`确定删除班级「${name}」？\n将同时删除该班全部数据：学生、错题、讲评记录、配套练习与组卷（不可恢复）。`)) return;
  try {
    const r = await api(`/api/classes/${encodeURIComponent(name)}`, {method: "DELETE"});
    closeClassModal();
    if (state.className === name) state.className = "";
    toast(`已删除班级 ${name}（学生 ${r.removed_students} 人、错题 ${r.removed_errors} 条）`);
    await initClasses();
    render();
  } catch (e) { toast(e.message, false); }
}

async function addStudent() {
  const code = $("#ns-code").value.trim(), name = $("#ns-name").value.trim();
  if (!code) { toast("请填写学生代号", false); return; }
  try {
    await api("/api/students", {method: "POST", body: {
      student_code: code, name_local: name, class_name: state.className || ""}});
    toast(`已添加 ${code}${name ? " · " + name : ""}`);
    $("#ns-code").value = ""; $("#ns-name").value = "";
    await initClasses(); render();
  } catch (e) { toast(e.message, false); }
}

async function editStudent(code) {
  let stu;
  try { stu = await api(`/api/students/${encodeURIComponent(code)}/profile`).then(p => p.student); }
  catch (e) { toast(e.message, false); return; }
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal-card">
    <h3 style="margin:0 0 12px">编辑学生</h3>
    <div class="grid c2 f2" style="gap:10px">
      <div class="field"><label>代号（改后错题关联自动跟随）</label><input type="text" id="es-code" value="${esc(stu.student_code)}"></div>
      <div class="field"><label>姓名</label><input type="text" id="es-name" value="${esc(stu.name_local || "")}"></div>
    </div>
    <div class="field"><label>班级</label><input type="text" id="es-class" value="${esc(stu.class_name || "")}"></div>
    <div class="btn-row">
      <button class="btn primary" onclick="saveStudent('${esc(stu.student_code)}')">保存</button>
      <button class="btn secondary" onclick="this.closest('.overlay').remove()">取消</button>
    </div>
    <div style="border-top:.5px solid var(--sep);margin-top:16px;padding-top:12px;display:flex;align-items:center;gap:10px">
      <span style="font-size:12px;color:var(--text3);flex:1">危险操作：删除学生将同时删除该生全部错题与已上传的批改照片（不可恢复）</span>
      <button class="btn danger sm" onclick="delStudent('${esc(stu.student_code)}', this)">删除该学生…</button>
    </div></div>`;
  ov.onclick = ev => { if (ev.target === ov) ov.remove(); };
  document.body.appendChild(ov);
}

async function saveStudent(code) {
  const newCode = $("#es-code").value.trim(), name = $("#es-name").value.trim(), cls = $("#es-class").value.trim();
  if (!newCode) { toast("代号不能为空", false); return; }
  try {
    await api(`/api/students/${encodeURIComponent(code)}`, {method: "PUT", body: {
      new_code: newCode, name_local: name, class_name: cls}});
    toast("已保存");
    document.querySelector(".overlay").remove();
    await initClasses(); render();
  } catch (e) { toast(e.message, false); }
}

async function delStudent(code, fromEditBtn) {
  // 防误删：删除只能从编辑弹窗进入（名册无直接删除入口），此处再做二次确认
  const ov = document.createElement("div");
  ov.className = "overlay";
  ov.innerHTML = `<div class="modal-card">
    <h3 style="margin:0 0 10px;color:#B91C1C">确认删除学生 ${esc(code)}？</h3>
    <div style="font-size:13px;color:var(--text2);line-height:1.8">
      将同时删除该生对应的全部数据（错题、已上传的批改照片等，不可恢复）。</div>
    <div class="btn-row">
      <button class="btn primary" style="background:#DC2626" onclick="doDeleteStudent('${esc(code)}', ${fromEditBtn ? "true" : "false"})">确认删除</button>
      <button class="btn secondary" onclick="this.closest('.overlay').remove()">取消</button>
    </div></div>`;
  ov.onclick = ev => { if (ev.target === ov) ov.remove(); };
  document.body.appendChild(ov);
}

async function doDeleteStudent(code, closeEdit) {
  try {
    const r = await api(`/api/students/${encodeURIComponent(code)}?purge_errors=true`, {method: "DELETE"});
    toast(`已删除 ${code} 及其 ${r.removed_errors} 条错题`);
    document.querySelectorAll(".overlay").forEach(el => el.remove());
    await initClasses(); render();
  } catch (e) { toast(e.message, false); }
}

async function importStudents() {
  let text = $("#stuText").value.trim();
  if (!text) { toast("请先填写名单", false); return; }
  if (state.className) {
    // 未写班级的行自动补当前班级，避免多班混录
    text = text.split("\n").map(line => {
      const t = line.trim();
      if (!t) return t;
      const parts = t.replace(",", " ").replace("，", " ").replace("\t", " ").split(/\s+/);
      return parts.length >= 3 ? t : t + "," + state.className;
    }).join("\n");
  }
  try {
    const r = await api("/api/students/import", {method: "POST", body: {text}});
    toast(`已导入 ${r.imported} 人，共 ${r.total} 人`);
    await initClasses();
    render();
  } catch (e) { toast(e.message, false); }
}

/* ---- 学生个人错题档案（仅供辅导参考） ---- */
async function openStudentProfile(code) {
  const ov = document.createElement("div");
  ov.className = "overlay"; ov.id = "stuOverlay";
  ov.innerHTML = `<div class="modal-card"><div class="loading-block">加载中…</div></div>`;
  ov.onclick = ev => { if (ev.target === ov) ov.remove(); };
  document.body.appendChild(ov);
  let p;
  try { p = await api(`/api/students/${encodeURIComponent(code)}/profile`); }
  catch (e) { ov.querySelector(".modal-card").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`; return; }
  state.profileCode = code;   // 清理入口读取
  state.profileData = p;      // 清理弹窗读取该生错因/题型列表
  const stu = p.student || {};
  const cats = p.categories || [];
  const maxC = Math.max(1, ...cats.map(c => c.count));
  // 月度趋势（v4.13）：展示最近 6 个月，条形 + 五大组分布；末期较前期变化只描述数据
  const trend = p.trend || [];
  const shownTrend = trend.slice(-6);
  const maxT = Math.max(1, ...shownTrend.map(t => t.total));
  let trendNote = "";
  if (shownTrend.length >= 2) {
    const d = shownTrend[shownTrend.length - 1].total - shownTrend[shownTrend.length - 2].total;
    trendNote = d > 0 ? `最近一月较前一月多 ${d} 条` : d < 0 ? `最近一月较前一月少 ${-d} 条` : "最近一月与前一月持平";
  }
  ov.querySelector(".modal-card").innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
      <h3 style="font-size:19px;margin:0">${esc(stu.name_local || stu.student_code)}</h3>
      <span class="hint">${esc(stu.student_code)}${stu.class_name ? " · " + esc(stu.class_name) : ""}</span>
      <a href="/api/students/${encodeURIComponent(code)}/report" target="_blank" class="btn secondary sm" style="text-decoration:none;margin-left:auto" title="打印友好网页：统计 + 错因分布 + 月度趋势 + 最近错题，可另存 PDF">⇩ 导出报告</a>
      <button class="btn secondary sm" onclick="closeStudentProfile()">关闭</button>
    </div>
    <div class="grid c4" style="margin:10px 0">
      <div class="stat"><span class="num">${p.total_confirmed}</span><span class="label">确认错题</span></div>
      <div class="stat blue"><span class="num">${p.pending}</span><span class="label">待确认</span></div>
      <div class="stat purple"><span class="num">${cats.length}</span><span class="label">涉及错因类别</span></div>
      <div class="stat green"><span class="num">${(p.recent || []).length}</span><span class="label">最近错题</span></div>
    </div>
    ${cats.length ? `<h3 style="font-size:15px;margin-top:12px">错因分布（已确认的）</h3>
      ${cats.slice(0, 6).map(c => `
      <div style="display:flex;align-items:center;gap:8px;margin:5px 0">
        ${catBadge(c.category_id)}
        <span style="flex:1;height:8px;border-radius:99px;background:var(--fill);overflow:hidden;display:block">
          <span style="display:block;width:${Math.round(c.count / maxC * 100)}%;height:100%;background:#4F46E5"></span></span>
        <b style="font-size:13px">${c.count}</b>
      </div>`).join("")}
      ${cats.length > 6 ? `<div class="hint">另有 ${cats.length - 6} 类未展示</div>` : ""}` : `<div class="hint">暂无已确认错因</div>`}
    ${shownTrend.length ? `<h3 style="font-size:15px;margin-top:14px">📈 月度趋势（近 ${shownTrend.length} 个月${trend.length > 6 ? `，共 ${trend.length} 个月` : ""}）</h3>
      ${trendNote ? `<div class="hint" style="margin:2px 0 4px">${trendNote}（按错题录入时间，已确认口径）</div>` : ""}
      ${shownTrend.map(t => {
        const parts = ["A", "B", "C", "D", "E"].filter(g => (t["g" + g] || 0) > 0)
          .map(g => `${groupName(g)} ${t["g" + g]}`).join(" · ");
        return `
      <div style="display:flex;align-items:center;gap:8px;margin:5px 0">
        <span style="font-size:12.5px;color:var(--text2);width:56px">${esc(t.ym)}</span>
        <span style="flex:1;height:8px;border-radius:99px;background:var(--fill);overflow:hidden;display:block">
          <span style="display:block;width:${Math.round(t.total / maxT * 100)}%;height:100%;background:#7C3AED"></span></span>
        <b style="font-size:13px;width:24px;text-align:right">${t.total}</b>
        <span style="font-size:11.5px;color:var(--text3);min-width:170px">${esc(parts || "—")}</span>
      </div>`;}).join("")}` : ""}
    <h3 style="font-size:15px;margin-top:14px">最近错题</h3>
    ${(p.recent || []).length ? p.recent.map(e => `
      <div class="card" style="margin:8px 0;padding:12px 14px">
        <div class="result-head">${catBadge(e.teacher_override || e.category_id)}${qtypeChip(e.qtype)}
          <span style="margin-left:auto;font-size:12px;color:var(--text2)">${esc(e.exam_type || "")}${e.batch_no ? " · " + esc(e.batch_no) : ""}${e.teacher_action ? "" : " · 待确认"}</span></div>
        <div class="clamp" style="font-weight:600;margin:5px 0 3px" title="点击展开/收起">${esc(e.question)}</div>
        ${passageBox(e.passage)}
        ${e.answer ? `<div style="font-size:13px;color:var(--red)">作答：${esc(e.answer)}</div>` : ""}
      </div>`).join("") : `<div class="hint">暂无错题记录</div>`}
    <h3 style="font-size:15px;margin-top:14px">🧹 清理历史数据</h3>
    <div class="hint" style="margin:3px 0 8px">按时间、错因类别或题型删除该生的历史数据；班级批改存档不受影响。</div>
    <div class="btn-row" style="justify-content:flex-start">
      <button class="btn secondary sm" onclick="openCleanupOverlay()">打开清理…</button>
    </div>
    <div class="policy-strip">仅供您辅导参考：这里只呈现原始错题与计数，不构成学生评价，不得用于排名或综合素质评定。</div>`;
}

function closeStudentProfile() { const ov = $("#stuOverlay"); if (ov) ov.remove(); }

/* ---- 学生历史数据清理（维度选择 → 确认 → 执行） ---- */
const CLEANUP_META = {
  "1w": ["按时间 · 一周前", "删除一周以前的错题记录与照片登记"],
  "1m": ["按时间 · 一个月前", "删除一个月以前的错题记录与照片登记"],
  "all": ["全部清除", "删除该生全部错题记录与照片登记"],
  "category": ["按错因类别", "删除该生指定错因类别的全部错题记录（照片登记不受影响）"],
  "qtype": ["按题型", "删除该生指定题型的全部错题记录（照片登记不受影响）"],
};
function openCleanupOverlay() {
  const code = state.profileCode;
  if (!code) return;
  const ov = document.createElement("div");
  ov.className = "overlay"; ov.id = "cleanupOverlay";
  ov.onclick = ev => { if (ev.target === ov) ov.remove(); };
  ov.innerHTML = `<div class="modal-card" style="max-width:540px">
    <div class="result-head"><b>🧹 清理历史数据</b><a style="cursor:pointer;margin-left:auto" onclick="closeCleanupOverlay()">✕</a></div>
    <div class="seg" id="cleanup-seg" style="margin:12px 0 6px">
      <button data-dim="1w" class="active">一周前</button>
      <button data-dim="1m">一个月前</button>
      <button data-dim="category">按错因</button>
      <button data-dim="qtype">按题型</button>
      <button data-dim="all">全部清除</button>
    </div>
    <div id="cleanup-params" style="margin:4px 0"></div>
    <div class="hint" style="margin:6px 0 2px">此操作不可撤销；班级批改历史（成绩单存档）不受影响。</div>
    <div class="btn-row">
      <button class="btn primary" style="background:#DC2626" id="cleanup-go">确认清理</button>
      <button class="btn secondary" onclick="closeCleanupOverlay()">取消</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  let dim = "1w";
  const box = $("#cleanup-params");
  const go = $("#cleanup-go");
  function renderParams() {
    const p = state.profileData || {};
    const m = CLEANUP_META[dim] || ["", ""];
    if (dim === "category") {
      const cats = (p.categories || []).map(c => c.category_id);
      box.innerHTML = cats.length
        ? `<div class="field"><label>错因类别</label><select id="cleanup-cat">${
            cats.map(cid => `<option value="${esc(cid)}">${esc(cid)} · ${esc(catName(cid))}</option>`).join("")
          }</select></div>`
        : `<div class="empty">该生暂无已确认错因记录可清理</div>`;
    } else if (dim === "qtype") {
      const qtypes = [...new Set((p.recent || []).map(e => e.qtype).filter(Boolean))];
      box.innerHTML = qtypes.length
        ? `<div class="field"><label>题型</label><select id="cleanup-qtype">${
            qtypes.map(t => `<option value="${esc(t)}">${esc(t)}</option>`).join("")
          }</select></div>`
        : `<div class="empty">该生暂无错题记录可清理</div>`;
    } else {
      box.innerHTML = `<div style="font-size:13px;color:var(--text2)">${esc(m[1])}。</div>`;
    }
    const sel = box.querySelector("select");
    go.disabled = (dim === "category" || dim === "qtype") && !sel;
  }
  $$("#cleanup-seg button").forEach(b => b.onclick = () => {
    dim = b.dataset.dim;
    $$("#cleanup-seg button").forEach(x => x.classList.toggle("active", x === b));
    renderParams();
  });
  renderParams();
  go.onclick = () => {
    const body = { dimension: dim };
    if (dim === "category") { const s = $("#cleanup-cat"); if (!s) return; body.category_id = s.value; }
    if (dim === "qtype") { const s = $("#cleanup-qtype"); if (!s) return; body.qtype = s.value; }
    doCleanupStudent(body);
  };
}
function closeCleanupOverlay() { const ov = $("#cleanupOverlay"); if (ov) ov.remove(); }
async function doCleanupStudent(body) {
  const code = state.profileCode;
  closeCleanupOverlay();
  try {
    const r = await api(`/api/students/${encodeURIComponent(code)}/cleanup`,
                        { method: "POST", body });
    toast(`已清理（${r.label}）：错题 ${r.removed.errors} 条，照片登记 ${r.removed.paper_files} 条`);
    closeStudentProfile();
    openStudentProfile(code);   // 清理后刷新档案
  } catch (e) { toast(e.message, false); }
}
