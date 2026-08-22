# ruff: noqa: E501
"""Single-page Delivery preview served by the reference API."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def install_preview_ui(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def preview_home() -> str:
        return _PAGE


_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent-Team-OS · Delivery Control</title>
  <style>
    :root {
      --ink: #10283d; --muted: #637889; --paper: #f4f9fb; --panel: #ffffff;
      --line: #cbdde5; --blue: #1769e0; --blue-soft: #e8f1ff;
      --orange: #e56b2f; --orange-soft: #fff0e8; --green: #147d64; --red: #b33a3a;
      --shadow: 0 22px 70px rgba(21, 60, 84, .12);
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    body {
      margin: 0; color: var(--ink); background: var(--paper);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background-image: linear-gradient(rgba(23,105,224,.045) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(23,105,224,.045) 1px, transparent 1px);
      background-size: 32px 32px;
    }
    button, textarea { font: inherit; }
    .shell { max-width: 1240px; margin: 0 auto; padding: 28px 28px 54px; }
    header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; }
    .brand { display: flex; gap: 15px; align-items: center; }
    .mark { width: 42px; height: 42px; border: 2px solid var(--ink); position: relative; }
    .mark::after { content: ""; position: absolute; inset: 8px -7px -7px 8px; background: var(--blue); z-index: -1; }
    .eyebrow, .mono { font: 700 11px/1.4 "IBM Plex Mono", "SFMono-Regular", monospace; letter-spacing: .13em; text-transform: uppercase; }
    h1 { margin: 2px 0 0; font-size: clamp(24px, 3vw, 40px); letter-spacing: -.04em; }
    .mode { border: 1px solid var(--orange); background: var(--orange-soft); color: #99421f; padding: 8px 11px; border-radius: 2px; }
    .thesis { margin: 54px 0 25px; display: grid; grid-template-columns: 1fr 1fr; gap: 36px; align-items: end; }
    .thesis h2 { margin: 0; max-width: 760px; font-size: clamp(34px, 5vw, 68px); line-height: .98; letter-spacing: -.055em; }
    .thesis p { margin: 0 0 5px; color: var(--muted); line-height: 1.7; max-width: 480px; }
    .rail { display: grid; grid-template-columns: repeat(5, 1fr); margin: 34px 0 24px; }
    .stage { position: relative; border-top: 3px solid var(--line); padding: 14px 8px 0 0; color: var(--muted); }
    .stage::before { content: ""; width: 11px; height: 11px; border: 3px solid var(--paper); background: var(--line); border-radius: 50%; position: absolute; top: -7px; left: 0; box-shadow: 0 0 0 1px var(--line); }
    .stage.active { border-color: var(--blue); color: var(--ink); }
    .stage.active::before { background: var(--blue); box-shadow: 0 0 0 1px var(--blue), 0 0 0 6px var(--blue-soft); }
    .stage.done { border-color: var(--green); color: var(--green); }
    .stage.done::before { background: var(--green); box-shadow: 0 0 0 1px var(--green); }
    .stage strong { display: block; font-size: 13px; margin-top: 3px; }
    .workspace { display: grid; grid-template-columns: minmax(300px, .78fr) minmax(440px, 1.22fr); gap: 18px; }
    .card { background: rgba(255,255,255,.94); border: 1px solid var(--line); box-shadow: var(--shadow); padding: 24px; }
    .card-title { display: flex; justify-content: space-between; gap: 18px; align-items: baseline; border-bottom: 1px solid var(--line); padding-bottom: 15px; margin-bottom: 19px; }
    .card-title h3 { margin: 0; font-size: 20px; letter-spacing: -.025em; }
    label { display: block; margin: 0 0 9px; font-weight: 700; }
    textarea { width: 100%; min-height: 180px; resize: vertical; border: 1px solid #9eb7c4; background: #faffff; color: var(--ink); padding: 15px; line-height: 1.55; outline: none; }
    textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-soft); }
    .hint { color: var(--muted); font-size: 13px; line-height: 1.55; margin: 10px 0 20px; }
    .btn-row { display: flex; flex-wrap: wrap; gap: 9px; }
    button { border: 1px solid var(--ink); background: var(--ink); color: white; padding: 11px 15px; cursor: pointer; font-weight: 700; }
    button.secondary { background: white; color: var(--ink); }
    button.accept { background: var(--green); border-color: var(--green); }
    button.reject { background: white; color: var(--red); border-color: var(--red); }
    button:disabled { cursor: wait; opacity: .5; }
    button:focus-visible { outline: 3px solid var(--orange); outline-offset: 2px; }
    .identity { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
    .identity div { background: #edf5f8; padding: 12px; min-width: 0; }
    .identity span { display: block; color: var(--muted); font-size: 11px; margin-bottom: 5px; }
    .identity strong { font: 650 12px/1.45 "IBM Plex Mono", monospace; overflow-wrap: anywhere; }
    .status { display: inline-flex; align-items: center; gap: 8px; font: 700 12px "IBM Plex Mono", monospace; color: var(--blue); }
    .status::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
    .empty { min-height: 300px; display: grid; place-items: center; border: 1px dashed #a8bec9; color: var(--muted); text-align: center; padding: 30px; }
    .evidence { display: none; }
    .evidence.visible { display: block; }
    .block { margin-top: 14px; border-left: 3px solid var(--blue); background: #f7fbfd; padding: 14px 16px; }
    .block h4 { margin: 0 0 8px; font-size: 13px; }
    pre { white-space: pre-wrap; word-break: break-word; margin: 0; color: #304d60; font: 12px/1.55 "IBM Plex Mono", monospace; }
    .notice { display: none; margin-top: 14px; padding: 12px; background: var(--orange-soft); color: #8b3d1d; font-size: 13px; }
    .notice.visible { display: block; }
    footer { margin-top: 22px; color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; }
    @media (max-width: 820px) {
      .shell { padding: 20px 16px 40px; } .thesis, .workspace { grid-template-columns: 1fr; }
      .thesis { margin-top: 40px; } .rail { overflow-x: auto; grid-template-columns: repeat(5, minmax(120px, 1fr)); }
      header { flex-direction: column; } .identity { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: no-preference) {
      .card { animation: arrive .45s ease-out both; } .card:nth-child(2) { animation-delay: .08s; }
      @keyframes arrive { from { opacity: 0; transform: translateY(8px); } }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand"><div class="mark" aria-hidden="true"></div><div><div class="eyebrow">Delivery control plane / V2</div><h1>Agent-Team-OS</h1></div></div>
      <div class="mode mono">Preview · Codex 模拟角色</div>
    </header>
    <section class="thesis">
      <h2>交付不是一句<br>“已经完成”。</h2>
      <p>每次变更都必须经过需求、任务、候选、验证和人工决策。这个 Preview 使用真实 API 合同；执行与应用为确定性预览，不修改真实仓库。</p>
    </section>
    <nav class="rail" aria-label="Delivery stages">
      <div class="stage" data-stage="request"><span class="mono">01</span><strong>需求</strong></div>
      <div class="stage" data-stage="plan"><span class="mono">02</span><strong>计划 Gate</strong></div>
      <div class="stage" data-stage="candidate"><span class="mono">03</span><strong>候选变更</strong></div>
      <div class="stage" data-stage="verification"><span class="mono">04</span><strong>机器验证</strong></div>
      <div class="stage" data-stage="decision"><span class="mono">05</span><strong>应用决策</strong></div>
    </nav>
    <section class="workspace">
      <article class="card">
        <div class="card-title"><h3>提交 Backend 需求</h3><span class="mono">backend-demo</span></div>
        <form id="request-form">
          <label for="request">你希望交付什么？</label>
          <textarea id="request" required>增加一个 GET /health 接口，返回服务状态和版本号，并补充机器测试。</textarea>
          <p class="hint">创建后会先停在计划审批。审批前不会执行代码变更。</p>
          <div class="btn-row"><button id="create" type="submit">创建 Delivery</button></div>
        </form>
        <div id="actions" class="btn-row" style="margin-top:14px"></div>
        <div id="notice" class="notice" role="alert"></div>
      </article>
      <article class="card">
        <div class="card-title"><h3>交付证据</h3><span id="status" class="status">等待创建 Delivery</span></div>
        <div id="empty" class="empty"><div><div class="eyebrow">No active delivery</div><p>提交一个有边界的 Backend 需求，证据将在这里逐步出现。</p></div></div>
        <div id="evidence" class="evidence">
          <div class="identity"><div><span>规划身份</span><strong id="planning-id">—</strong></div><div><span>执行身份</span><strong id="execution-id">尚未执行</strong></div></div>
          <div class="block"><h4>Requirement Artifact</h4><pre id="requirements"></pre></div>
          <div class="block"><h4>Task Contract</h4><pre id="task"></pre></div>
          <div id="candidate-block" class="block" hidden><h4>Candidate Change</h4><pre id="candidate"></pre></div>
          <div id="verification-block" class="block" hidden><h4>Verification Run</h4><pre id="verification"></pre></div>
          <div id="receipt-block" class="block" hidden><h4>Apply Receipt</h4><pre id="receipt"></pre></div>
        </div>
      </article>
    </section>
    <footer><span>Agent-Team-OS V2 · evidence before confidence</span><span class="mono">API /docs</span></footer>
  </main>
  <script>
    let delivery = null;
    const $ = (id) => document.getElementById(id);
    const pretty = (value) => JSON.stringify(value, null, 2);
    const stageOrder = {awaiting_plan_decision:1, awaiting_candidate_decision:4, completed:5, rejected:5, failed:4};
    function setBusy(busy) { document.querySelectorAll('button').forEach((b) => b.disabled = busy); }
    function showError(message) { $('notice').textContent = message; $('notice').classList.add('visible'); }
    async function request(url, options) {
      setBusy(true); $('notice').classList.remove('visible');
      try {
        const response = await fetch(url, {headers:{'content-type':'application/json'}, ...options});
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `Request failed: ${response.status}`);
        delivery = body; render();
      } catch (error) { showError(error.message); } finally { setBusy(false); }
    }
    function render() {
      $('empty').hidden = true; $('evidence').classList.add('visible');
      $('status').textContent = delivery.status;
      $('planning-id').textContent = delivery.planning_identity;
      $('execution-id').textContent = delivery.execution_identity || '尚未执行';
      $('requirements').textContent = pretty(delivery.requirements);
      $('task').textContent = pretty(delivery.task);
      [['candidate', delivery.candidate], ['verification', delivery.verification], ['receipt', delivery.apply_receipt]].forEach(([name, value]) => {
        $(`${name}-block`).hidden = !value; if (value) $(name).textContent = pretty(value);
      });
      const current = stageOrder[delivery.status] || 0;
      document.querySelectorAll('.stage').forEach((node, index) => { node.className = `stage ${index < current ? 'done' : index === current ? 'active' : ''}`; });
      const actions = $('actions'); actions.replaceChildren();
      if (delivery.status === 'awaiting_plan_decision') {
        addAction('批准计划并执行', 'accept', () => decide('plan', 'approve'));
        addAction('拒绝计划', 'reject', () => decide('plan', 'reject'));
      } else if (delivery.status === 'awaiting_candidate_decision') {
        addAction('接受并应用候选', 'accept', () => decide('candidate', 'accept'));
        addAction('拒绝候选', 'reject', () => decide('candidate', 'reject'));
      }
    }
    function addAction(label, style, handler) { const button = document.createElement('button'); button.textContent = label; button.className = style; button.onclick = handler; $('actions').append(button); }
    function decide(kind, decision) { request(`/v1/deliveries/${delivery.id}/${kind}-decision`, {method:'POST', body:pretty({decision, expected_version:delivery.version})}); }
    $('request-form').addEventListener('submit', (event) => { event.preventDefault(); request('/v1/deliveries', {method:'POST', body:pretty({workspace_id:'backend-demo', user_request:$('request').value})}); });
  </script>
</body>
</html>"""
