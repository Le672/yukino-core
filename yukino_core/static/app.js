"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const viewNames = { overview: "运行概览", sessions: "会话与历史", settings: "人格与称呼", providers: "模型服务", connections: "平台连接", strategy: "对话策略", memories: "长期记忆", plugins: "插件管理", resources: "资源库", events: "事件追踪", test: "隔离测试", system: "系统与发行" };
  const state = {
    csrf: "", authenticated: false, view: "overview", status: null,
    sessions: [], selectedSession: "", memoryScope: "", memoryEditing: null, plugins: [],
    config: null, configDirty: false, configRevision: 0, poll: null, polling: false,
    lastSuccess: null, activeRequests: 0, toastTimer: null, sessionsRequest: 0,
    memoriesRequest: 0, settingsRequest: 0, events: [], pollError: false,
  };
  const library = { kind: "images", query: "", offset: 0, limit: 24, includeDisabled: false, total: null, items: [], request: 0, summaryRequest: 0, loading: false, editor: null };
  const has = (obj, key) => obj != null && Object.prototype.hasOwnProperty.call(obj, key);
  const obj = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const pick = (...values) => values.find((value) => value !== undefined && value !== null);
  const display = (value, fallback = "暂无数据") => value === undefined || value === null || value === "" ? fallback : typeof value === "object" ? safeStringify(value) : String(value);
  const arrayFrom = (value, key) => Array.isArray(value) ? value : Array.isArray(value?.[key]) ? value[key] : [];
  const number = (value) => typeof value !== "boolean" && value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
  const count = (value) => number(value) === null ? "—" : new Intl.NumberFormat("zh-CN").format(Number(value));
  function safeStringify(value) { try { return JSON.stringify(value, null, 2); } catch { return "无法显示数据"; } }
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }
  function empty(container, text) { container.replaceChildren(node("p", "empty", text)); }
  function pill(text, tone = "neutral") { return node("span", `pill ${tone}`, text); }
  function toast(message) {
    window.clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = window.setTimeout(() => { $("toast").hidden = true; }, 4200);
  }
  function showError(error, prefix = "") {
    if (error?.code === "auth_expired") return;
    const message = prefix + (error?.message || "请求失败，请稍后重试。");
    $("global-error").textContent = message;
    $("global-error").hidden = false;
  }
  function clearError() { $("global-error").hidden = true; $("global-error").textContent = ""; }
  function errorOf(message, code = "request_error") { const error = new Error(message); error.code = code; return error; }

  async function api(path, { method = "GET", body, timeout = 20000, auth = true } = {}) {
    const headers = { Accept: "application/json" };
    const multipart = typeof FormData !== "undefined" && body instanceof FormData;
    if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
    if (method !== "GET" && method !== "HEAD" && auth) {
      if (!state.csrf) throw errorOf("安全会话尚未建立，请重新登录。", "csrf_missing");
      headers["X-CSRF-Token"] = state.csrf;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {
        method, headers, credentials: "same-origin", cache: "no-store",
        signal: controller.signal, ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }),
      });
      let payload;
      try { payload = await response.json(); } catch { throw errorOf(`服务返回了无法读取的响应（HTTP ${response.status}）。`, "invalid_response"); }
      if (response.status === 401 && auth) {
        showLogin("管理会话已过期，请重新输入令牌。");
        throw errorOf("管理会话已过期。", "auth_expired");
      }
      if (!response.ok || payload?.ok === false) {
        const details = obj(payload?.error);
        const additional = details.details || details.fields || payload?.details;
        throw errorOf(display(details.message, typeof payload?.error === "string" ? payload.error : `请求失败（HTTP ${response.status}）。`) + (additional ? `\n${safeStringify(additional)}` : ""), display(details.code, "request_error"));
      }
      if (payload?.ok !== true) throw errorOf("服务响应格式不符合预期。", "invalid_response");
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") throw errorOf("请求超时，尚未确认处理结果。请刷新状态后再决定是否重试。", "timeout");
      if (error instanceof TypeError) throw errorOf("无法连接到内核。请检查服务与网络连接。", "network_error");
      throw error;
    } finally { window.clearTimeout(timer); }
  }
  async function get(path) { return (await api(path)).data; }
  function formatTime(value, fallback = "时间未知", { short = false } = {}) {
    if (value === undefined || value === null || value === "") return fallback;
    let parsed = value;
    if (typeof parsed === "number") parsed = parsed < 1e12 ? parsed * 1000 : parsed;
    else if (/^\d+(\.\d+)?$/.test(String(parsed))) parsed = Number(parsed) < 1e12 ? Number(parsed) * 1000 : Number(parsed);
    const date = new Date(parsed);
    if (Number.isNaN(date.getTime())) return display(value, fallback);
    return new Intl.DateTimeFormat("zh-CN", short ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false } : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
  }
  function duration(value) {
    const seconds = number(value);
    if (seconds === null) return "—";
    if (seconds < 60) return `${Math.floor(seconds)} 秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
    return `${Math.floor(seconds / 86400)} 天 ${Math.floor((seconds % 86400) / 3600)} 小时`;
  }
  function latency(value) {
    const milliseconds = number(value);
    if (milliseconds === null) return "—";
    return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)} s` : `${Math.round(milliseconds)} ms`;
  }
  function health(value, kind) {
    const source = obj(value);
    const status = String(pick(source.status, source.state, "")).toLowerCase();
    if (kind === "transport") {
      const connected = typeof value === "boolean" ? value : source.connected;
      const qq = obj(source.qq);
      if (connected === false) return { tone: ["connecting", "starting"].includes(status) ? "warning" : "bad", text: status === "connecting" ? "通道连接中" : "通道未连接" };
      if (connected === true && qq.online === true) return { tone: "good", text: "账号在线" };
      if (connected === true && qq.online === false) return { tone: "bad", text: "账号离线" };
      if (connected === true) return { tone: "warning", text: "在线待确认" };
      return { tone: "neutral", text: "通道状态未知" };
    }
    if (["not_checked", "unknown", "checking", "starting", "pending"].includes(status)) return { tone: "warning", text: status === "checking" ? "检查中" : status === "not_checked" ? "尚未检查" : "状态待确认" };
    if (["reachable", "models_unavailable"].includes(status)) return { tone: "warning", text: "待生成验证" };
    const explicit = typeof value === "boolean" ? value : pick(source.healthy, source.available, source.ready);
    if (explicit === true) return { tone: "good", text: "可用" };
    if (explicit === false) return { tone: "bad", text: "不可用" };
    if (["ok", "healthy", "ready", "connected", "online", "available", "up"].includes(status)) return { tone: "good", text: "可用" };
    if (["error", "failed", "unhealthy", "disconnected", "offline", "unavailable", "unreachable", "down", "not_configured", "not-configured"].includes(status)) return { tone: "bad", text: ["not_configured", "not-configured"].includes(status) ? "未配置" : "不可用" };
    if (["connecting", "degraded"].includes(status)) return { tone: "warning", text: status === "connecting" ? "连接中" : "状态待确认" };
    return { tone: "neutral", text: "状态未知" };
  }
  const eventLabels = {
    received: "已接收", queued: "已排队", processing: "处理中", generating: "生成中",
    completed: "已完成", replied: "已回复", sent: "已发送", done: "已完成", ignored: "已忽略",
    failed: "失败", error: "错误", unknown: "结果未知", send_unknown: "发送结果未知",
    cancelled: "已取消", canceled: "已取消", reset: "上下文已重置", duplicate: "重复事件",
    dropped: "已丢弃", rejected: "已拒绝", pending: "待处理", skipped: "已跳过",
    notice_disabled: "通知插件已停用", notice_ignored: "通知未触发", notice_probability: "未抽中回复",
    notice_cooldown: "通知冷却中", notice_replied: "通知已回应", notice_replied_poked: "已回应并回戳",
    notice_backpoke_sending: "正在回戳", notice_backpoke_failed: "回复成功，回戳失败",
    notice_backpoke_unknown: "回复成功，回戳结果未知", notice_error: "通知处理失败", receive_disabled: "平台接收已关闭",
    notice_interrupted: "通知被服务停止中断",
  };
  function eventState(event) {
    const raw = String(pick(event.state, event.status, event.type, "unknown"));
    const bad = /fail|error|unknown|reject/.test(raw);
    const good = /completed|replied|sent|done/.test(raw);
    return { text: eventLabels[raw] || raw, tone: bad ? "bad" : good ? "good" : /processing|generating|queue|pending/.test(raw) ? "warning" : "neutral", raw };
  }
  function eventTime(event) { return pick(event.updated_at, event.created_at, event.time, event.timestamp); }

  function showLogin(message = "") {
    state.authenticated = false;
    state.csrf = "";
    window.clearInterval(state.poll);
    state.poll = null;
    if ($("resource-editor").open) $("resource-editor").close();
    if ($("image-preview").open) $("image-preview").close();
    if ($("provider-editor").open) $("provider-editor").close();
    if ($("plugin-config-editor").open) $("plugin-config-editor").close();
    if ($("plugin-remove-dialog").open) $("plugin-remove-dialog").close();
    $("connection-token").value = "";
    $("boot-screen").hidden = true;
    $("app").hidden = true;
    $("login-screen").hidden = false;
    $("login-token").value = "";
    $("login-error").textContent = message;
    $("login-error").hidden = !message;
    $("login-submit").disabled = false;
    $("login-submit").textContent = "进入控制台 ↗";
    if (message) $("login-token").focus();
  }
  async function startApp(payload) {
    const session = obj(payload?.data);
    state.csrf = String(pick(payload?.csrf_token, session.csrf_token, ""));
    if (!state.csrf) {
      const restored = await api("/api/me", { auth: false });
      state.csrf = String(pick(restored?.csrf_token, restored?.data?.csrf_token, ""));
    }
    if (!state.csrf) throw errorOf("服务没有返回 CSRF 会话信息，请检查内核配置。", "csrf_missing");
    state.authenticated = true;
    $("boot-screen").hidden = true;
    $("login-screen").hidden = true;
    $("app").hidden = false;
    $("login-token").value = "";
    clearError();
    state.status = null;
    state.config = null;
    state.configDirty = false;
    settingsUI.dirty.clear(); connectionsUI.dirty = false;
    state.selectedSession = "";
    state.lastSuccess = null;
    const requested = window.location.hash.slice(1);
    setView(has(viewNames, requested) ? requested : "overview", { force: true });
    window.clearInterval(state.poll);
    state.poll = window.setInterval(poll, 8000);
  }
  async function restore() {
    try { await startApp(await api("/api/me", { auth: false })); }
    catch (error) { showLogin(error?.code === "unauthorized" ? "" : error.message); }
  }
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("login-token").value.trim();
    if (!token) return;
    $("login-submit").disabled = true;
    $("login-submit").textContent = "正在建立会话…";
    $("login-error").hidden = true;
    try { await startApp(await api("/api/login", { method: "POST", body: { token }, auth: false })); }
    catch (error) {
      $("login-error").textContent = error.message;
      $("login-error").hidden = false;
      $("login-submit").disabled = false;
      $("login-submit").textContent = "进入控制台 ↗";
    }
  });
  $("logout-button").addEventListener("click", async () => {
    $("logout-button").disabled = true;
    try { await api("/api/logout", { method: "POST", body: {} }); showLogin(); }
    catch (error) { showError(error, "退出失败："); }
    finally { $("logout-button").disabled = false; }
  });
  function setView(view, { force = false } = {}) {
    if (!has(viewNames, view) || (!force && state.view === view)) return;
    state.view = view;
    document.querySelectorAll(".view").forEach((section) => { section.hidden = section.id !== `view-${view}`; });
    document.querySelectorAll("[data-view]").forEach((button) => {
      const selected = button.dataset.view === view;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    $("page-label").textContent = viewNames[view];
    document.title = `${viewNames[view]} · Yukino Core`;
    window.history.replaceState(null, "", `#${view}`);
    clearError();
    void loadView(view);
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
  window.addEventListener("hashchange", () => { if (state.authenticated) setView(window.location.hash.slice(1) || "overview"); });
  async function loadView(view) {
    const loaders = {
      overview: [loadStatus, loadEvents], sessions: [loadSessions, loadProviders], settings: [loadConfig], strategy: [loadConfig],
      providers: [loadProviders], connections: [loadConnections], system: [loadSystem],
      memories: [() => loadMemories(state.memoryScope)], plugins: [loadPlugins],
      resources: [loadResources], events: [loadEvents], test: [],
    };
    state.activeRequests += 1;
    $("refresh-button").disabled = true;
    const results = await Promise.allSettled((loaders[view] || []).map((loader) => loader()));
    const failed = results.find((result) => result.status === "rejected");
    if (failed && state.view === view) showError(failed.reason);
    state.activeRequests -= 1;
    $("refresh-button").disabled = state.activeRequests > 0;
    if (state.authenticated && !state.status && view !== "overview") {
      try { await loadStatus(); } catch (error) { if (error.code !== "auth_expired") showError(error, "运行状态暂不可用："); }
    }
  }
  $("refresh-button").addEventListener("click", () => { clearError(); void loadView(state.view); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.authenticated) void poll(); });
  async function poll() {
    if (!state.authenticated || state.polling || document.hidden) return;
    state.polling = true;
    try {
      await loadStatus();
      if (["overview", "events"].includes(state.view)) await loadEvents();
      if (state.pollError) { clearError(); state.pollError = false; }
    } catch (error) {
      if (error.code !== "auth_expired") {
        state.pollError = true;
        $("updated-at").textContent = "同步失败";
        $("sidebar-dot").className = "status-dot warning";
        $("sidebar-state").textContent = "数据同步失败";
        $("overall-pill").replaceChildren(document.createTextNode("数据可能过期"));
        $("overall-pill").className = "pill warning";
        showError(error, "自动同步失败：");
      }
    } finally { state.polling = false; }
  }

  async function loadStatus() {
    const data = obj(await get("/api/status"));
    if (!state.authenticated) return;
    state.status = data;
    state.lastSuccess = new Date();
    $("updated-at").textContent = `${formatTime(state.lastSuccess, "—", { short: true })} 已同步`;
    $("last-success").textContent = formatTime(state.lastSuccess, "—", { short: true });
    renderStatus(data);
  }
  function metric(title, value, description, icon, tone = "neutral", word = false) {
    const card = node("article", "metric-card");
    const top = node("div", "metric-top");
    top.append(node("span", "metric-title", title), node("span", "metric-icon", icon));
    const valueNode = node("p", `metric-value${word ? " word-value" : ""}`, value);
    const foot = node("p", `metric-description ${tone}`);
    foot.append(node("span", `status-dot ${tone}`), document.createTextNode(description));
    card.append(top, valueNode, foot);
    return card;
  }
  function renderStatus(data) {
    const transport = obj(data.transport), provider = obj(data.provider), queue = obj(data.queue);
    const metrics = obj(data.metrics), plugins = obj(data.plugins), store = obj(pick(data.store, data.statistics));
    const resources = obj(data.resources), transportHealth = health(data.transport, "transport"), providerHealth = health(data.provider, "provider");
    const lastLatency = pick(metrics.last_latency_ms, metrics.latest_latency_ms, provider.last_latency_ms, metrics.latency_ms);
    const latencySample = number(lastLatency) === 0 && number(metrics.replied) === 0 ? undefined : lastLatency;
    const failures = pick(metrics.failures, metrics.failed, metrics.failure_count);
    const pending = pick(queue.pending, queue.pending_count), active = pick(queue.active, queue.active_count);
    const named = pick(data.name, data.bot_name, state.config?.name);
    $("instance-name").textContent = display(named, "你的对话工作空间");
    $("core-version").textContent = `版本 ${display(data.version, "未知")}`;
    $("sidebar-version").textContent = display(data.version, "");
    $("core-uptime").textContent = `已运行 ${duration(pick(data.uptime, data.uptime_seconds))}`;
    const isBad = [transportHealth.tone, providerHealth.tone].includes("bad");
    const isGood = transportHealth.tone === "good" && providerHealth.tone === "good";
    const tone = isBad ? "bad" : isGood ? "good" : "warning";
    $("sidebar-dot").className = `status-dot ${tone}`;
    $("sidebar-state").textContent = isBad ? "有服务需要关注" : isGood ? "连接状态正常" : "部分状态待确认";
    $("overall-pill").className = `pill ${tone}`;
    $("overall-pill").textContent = isBad ? "需要关注" : isGood ? "连接正常" : "状态待确认";
    $("overall-summary").textContent = isBad ? `QQ ${transportHealth.text}，模型服务${providerHealth.text}。请检查连接详情和近期事件。` : isGood ? `QQ 与模型服务均已就绪。${number(pending) !== null ? `当前有 ${pending} 条消息等待处理。` : "队列数据以服务返回为准。"}` : "部分依赖尚未返回明确的健康状态，暂不能确认完整回复链路可用。";
    $("status-cards").replaceChildren(
      metric("QQ 消息通道", transportHealth.text, display(pick(transport.last_error, transport.error, transport.self_id ? `账号 ${transport.self_id}` : undefined), "OneBot 实时连接"), "⌁", transportHealth.tone, true),
      metric("模型服务", providerHealth.text, providerDescription(provider), "✦", providerHealth.tone, true),
      metric("待处理 / 处理中", `${count(pending)} / ${count(active)}`, number(pick(queue.max_pending, queue.max)) !== null ? `队列上限 ${count(pick(queue.max_pending, queue.max))}` : "公平调度 · 按会话隔离", "☷", pending === undefined && active === undefined ? "neutral" : "good"),
      metric("最近一次回复延迟", latency(latencySample), failures === undefined ? "失败计数暂无数据" : `累计失败 ${count(failures)} 次`, "↗", number(failures) > 0 ? "warning" : latencySample === undefined ? "neutral" : "good")
    );
    const stats = [
      ["会话", pick(store.sessions, store.session_count), "个"], ["历史消息", pick(store.messages, store.message_count), "条"],
      ["长期记忆", pick(store.memories, store.memory_count), "条"],
      ["启用插件", pick(plugins.enabled, data.plugins_enabled), plugins.total !== undefined ? `/ ${count(plugins.total)}` : "个"],
      ["资源条目", pick(resourceTotal(resources), data.resource_count), resources.ready === false ? "项 · 未就绪" : "项"],
    ];
    $("workspace-stats").replaceChildren(...stats.map(([title, value, unit]) => {
      const item = node("div", "workspace-stat");
      item.append(node("span", "", title), node("strong", "", count(value)), node("small", "", unit));
      return item;
    }));
    const transportDetail = transportDescription(transport);
    const providerDetail = providerDescription(provider);
    $("connection-details").replaceChildren(
      connection("QQ / OneBot", transportDetail, "⌁", transportHealth),
      connection("模型提供方", providerDetail, "✦", providerHealth)
    );
  }
  function connection(title, detail, icon, status) {
    const item = node("div", "connection-item"), info = node("div", "connection-info");
    info.append(node("strong", "", title), node("p", "", detail));
    item.append(node("span", "connection-badge", icon), info, pill(status.text, status.tone));
    return item;
  }
  function providerDescription(provider) {
    const reported = pick(provider.last_error, provider.error, provider.model, provider.name);
    if (reported) return display(reported);
    const items = [
      provider.browser_connected === true ? "浏览器已连接" : provider.browser_connected === false ? "浏览器未连接" : "浏览器状态未知",
      provider.signed_in === true ? "账号已登录" : provider.signed_in === false ? "账号未登录" : "登录状态未知",
    ];
    if (typeof provider.busy === "boolean" && provider.busy) items.push("正在生成");
    return items.length ? items.join(" · ") : display(provider.state, "健康信息暂未提供");
  }
  function transportDescription(transport) {
    const qq = obj(transport.qq), account = pick(qq.user_id, transport.self_id);
    const items = [transport.connected === true ? "WebSocket 已连接" : transport.connected === false ? "WebSocket 未连接" : "WebSocket 状态未知"];
    items.push(qq.online === true ? "QQ 账号在线" : qq.online === false ? "QQ 账号离线" : "QQ 在线状态未知");
    if (account) items.push(`账号 ${account}`);
    if (transport.last_error || transport.error) items.push(display(transport.last_error || transport.error));
    return items.join(" · ");
  }

  async function loadEvents() {
    const data = await get("/api/events");
    if (!state.authenticated) return;
    state.events = arrayFrom(data, "events");
    renderEvents();
  }
  function renderEvents() {
    const events = state.events;
    $("event-count").textContent = `最近 ${events.length} 条事件`;
    if (!events.length) {
      empty($("overview-events"), "还没有近期事件");
      const cell = node("td", "empty", "还没有近期事件。收到消息后，事件会显示在这里。"); cell.colSpan = 4;
      const row = node("tr"); row.append(cell); $("event-table").replaceChildren(row); return;
    }
    $("overview-events").replaceChildren(...events.slice(0, 5).map((event) => {
      const item = node("div", "activity-row"), status = eventState(event), content = node("div", "activity-content");
      content.append(node("strong", "", `${status.text} · ${display(pick(event.session_id, event.scope), "内核事件")}`), node("p", "", display(pick(event.error, event.message, event.event_id, event.detail), "无附加说明")));
      item.append(node("span", `activity-bullet${status.tone === "bad" ? " bad" : ""}`), content, node("time", "activity-time", formatTime(eventTime(event), "—", { short: true })));
      return item;
    }));
    $("event-table").replaceChildren(...events.map((event) => {
      const row = node("tr"), first = node("td"), status = eventState(event), statusCell = node("td");
      first.append(node("span", "event-id mono", display(pick(event.event_id, event.id), "—")), node("time", "event-time", formatTime(eventTime(event))));
      statusCell.append(pill(status.text, status.tone));
      row.append(first, node("td", "mono", display(event.session_id, "—")), statusCell, node("td", `event-detail${event.error ? " error-text" : ""}`, display(pick(event.error, event.detail, event.message), "—")));
      return row;
    }));
  }

  function sessionID(session) { return String(pick(session.session_id, session.id, "")); }
  function sessionName(session) {
    const id = sessionID(session);
    return display(pick(session.display_name, session.name, session.title), id.startsWith("g:") ? `群聊 ${id.slice(2)}` : id.startsWith("p:") ? `私聊 ${id.slice(2)}` : id || "未知会话");
  }
  async function loadSessions() {
    const data = await get("/api/sessions");
    if (!state.authenticated) return;
    state.sessions = arrayFrom(data, "sessions");
    renderSessions();
    if (state.selectedSession) await loadHistory(state.selectedSession);
  }
  function renderSessions() {
    const query = $("session-search").value.trim().toLowerCase();
    const sessions = state.sessions.filter((session) => `${sessionID(session)} ${sessionName(session)}`.toLowerCase().includes(query));
    if (!sessions.length) { empty($("session-list"), query ? "没有匹配的会话" : "还没有会话记录"); return; }
    $("session-list").replaceChildren(...sessions.map((session) => {
      const id = sessionID(session), button = node("button", `session-item${state.selectedSession === id ? " selected" : ""}`);
      button.type = "button"; button.setAttribute("aria-pressed", state.selectedSession === id ? "true" : "false");
      const info = node("span"); info.append(node("strong", "", sessionName(session)), node("small", "mono", id));
      button.append(node("span", "session-avatar", id.startsWith("g:") ? "群" : "私"), info);
      button.addEventListener("click", () => {
        state.selectedSession = id; renderSessions(); clearError();
        void loadHistory(id).catch((error) => showError(error, "历史记录加载失败："));
      });
      return button;
    }));
  }
  $("session-search").addEventListener("input", renderSessions);
  async function loadHistory(id) {
    const request = ++state.sessionsRequest;
    $("history-title").textContent = sessionName(state.sessions.find((session) => sessionID(session) === id) || { session_id: id });
    $("history-subtitle").textContent = id;
    renderSessionProvider();
    $("reset-session").disabled = true;
    empty($("session-history"), "正在读取历史消息…");
    try {
      const data = await get(`/api/sessions/${encodeURIComponent(id)}/messages`);
      if (request !== state.sessionsRequest || id !== state.selectedSession || !state.authenticated) return;
      const messages = arrayFrom(data, "messages");
      $("reset-session").disabled = false;
      if (!messages.length) { empty($("session-history"), "此会话暂无历史消息"); return; }
      $("session-history").replaceChildren(...messages.map((message) => {
        const role = display(message.role, "unknown");
        const item = node("article", `chat-message ${["assistant", "system", "user"].includes(role) ? role : "user"}`);
        const meta = node("div", "chat-meta");
        const name = display(pick(message.display_name, message.user_id), role === "assistant" ? display(state.config?.name, "Yukino") : role === "system" ? "系统" : "用户");
        meta.append(node("span", "", name), node("time", "", formatTime(pick(message.created_at, message.time, message.timestamp), "")));
        const attachments = Array.isArray(message.attachments) ? message.attachments.filter((attachment) => ["image", "audio", "file"].includes(attachment?.type)) : [];
        item.append(meta);
        if (message.content || !attachments.length) item.append(node("div", "chat-bubble", display(message.content, "（空消息）")));
        if (attachments.length) {
          const media = node("div", "chat-attachments");
          media.append(...attachments.map((attachment) => attachment.type === "image" ? managedImageButton(attachment, "chat-image") : managedFile(attachment)));
          item.append(media);
        }
        return item;
      }));
      $("session-history").scrollTop = $("session-history").scrollHeight;
    } catch (error) {
      if (request === state.sessionsRequest) empty($("session-history"), "历史记录暂不可用，请刷新重试。");
      throw error;
    }
  }
  $("reset-session").addEventListener("click", async () => {
    const id = state.selectedSession;
    if (!id) return;
    $("reset-session").disabled = true; clearError();
    try {
      await api(`/api/sessions/${encodeURIComponent(id)}/reset`, { method: "POST", body: {} });
      toast("上下文已重置，历史消息已保留。");
      if (state.selectedSession === id) await loadHistory(id);
    } catch (error) { showError(error, "重置失败："); }
    finally { $("reset-session").disabled = !state.selectedSession; }
  });

  const settingsUI = { schema: [], controls: new Map(), dirty: new Set(), revisions: { settings: 0, strategy: 0 } };
  const modelsUI = { profiles: [], types: [], defaultID: "", editing: null, busy: false, request: 0, formType: "" };
  const connectionsUI = { data: null, dirty: false, revision: 0 };
  const pluginUI = { id: "", controls: new Map(), request: 0, removing: null, removeBusy: false, installBusy: false };
  const sectionNames = { general: "基本信息", persona: "人格与称呼", access: "对话范围与权限", triggers: "唤醒与触发", context: "上下文与资源", runtime: "队列与运行限制" };
  function parseList(value) { return [...new Set(String(value).split(/[,，\n\r]+/).map((part) => part.trim()).filter(Boolean))]; }
  function fieldType(field) {
    const raw = String(field.type || "str").toLowerCase();
    return ["boolean", "bool"].includes(raw) ? "boolean" : ["integer", "int", "number", "float"].includes(raw) ? "number" : ["list", "array", "string_list"].includes(raw) || raw.startsWith("list[") ? "list" : raw === "object" || raw === "json" ? "json" : "string";
  }
  function schemaFields(value) { return Array.isArray(value) ? value : arrayFrom(value, "fields"); }
  function makeSettingField(field, value, prefix, controls) {
    const wrap = node("div", "config-field"), type = fieldType(field), id = `${prefix}-${field.key}`;
    const label = node("label", "", display(field.label, field.key)); label.htmlFor = id;
    let input;
    if (Array.isArray(field.options) && field.options.length) {
      input = node("select");
      field.options.forEach((option) => {
        const item = obj(option), v = has(item, "value") ? item.value : option;
        const choice = node("option", "", display(item.label, String(v))); choice.value = String(v); input.append(choice);
      });
    } else if (type === "boolean") { input = node("input", "switch"); input.type = "checkbox"; }
    else if (type === "number") { input = node("input"); input.type = "number"; input.step = ["float", "number"].includes(field.type) ? "any" : "1"; }
    else if (type === "list" || type === "json" || field.key === "persona" || field.multiline || (typeof value === "string" && value.includes("\n")) || (number(field.max_length) ?? number(field.max) ?? 0) > 1000) {
      input = node("textarea"); input.rows = field.key === "persona" ? 12 : type === "list" ? 3 : 4;
    } else { input = node("input"); input.type = "text"; }
    input.id = id; input.name = field.key;
    if (type === "number") { if (number(field.min) !== null) input.min = field.min; if (number(field.max) !== null) input.max = field.max; input.required = true; }
    else if (type === "string" && (number(field.max_length) ?? number(field.max)) !== null) input.maxLength = number(field.max_length) ?? number(field.max);
    if (field.description) { const help = node("p", "field-help", field.description); help.id = `${id}-help`; input.setAttribute("aria-describedby", help.id); wrap.append(help); }
    if (field.restart_required) wrap.append(node("p", "field-help", "此项变更需要重启相关服务。"));
    if (type === "boolean") { label.className = "switch-row"; const title = node("span"); title.append(node("strong", "", display(field.label, field.key))); label.replaceChildren(title, input); wrap.prepend(label); }
    else { wrap.prepend(label, input); }
    setFieldValue(input, field, value);
    controls.set(field.key, { input, field });
    return wrap;
  }
  function setFieldValue(input, field, value) {
    const type = fieldType(field);
    if (type === "boolean") input.checked = value === true;
    else input.value = type === "list" ? Array.isArray(value) ? value.join("\n") : display(value, "") : type === "json" ? safeStringify(value ?? {}) : display(value, "");
  }
  function readControls(controls) {
    const result = {};
    for (const [key, { input, field }] of controls) {
      if (input.disabled) continue;
      const type = fieldType(field);
      if (type === "json") { try { result[key] = JSON.parse(input.value || "{}"); } catch { throw errorOf(`${field.label || key} 必须是有效的 JSON。`); } }
      else result[key] = type === "boolean" ? input.checked : type === "list" ? parseList(input.value) : type === "number" ? Number(input.value) : input.value;
    }
    return result;
  }
  function renderConfiguration(data) {
    const groups = new Map();
    settingsUI.schema.forEach((field) => { const section = field.section || "general"; if (!groups.has(section)) groups.set(section, []); groups.get(section).push(field); });
    for (const view of ["settings", "strategy"]) {
      if (settingsUI.dirty.has(view)) continue;
      const container = $(view === "settings" ? "persona-fields" : "strategy-fields"); container.replaceChildren();
      const controls = new Map();
      for (const [section, fields] of groups) {
        if ((["general", "persona"].includes(section) ? "settings" : "strategy") !== view) continue;
        const card = node("article", "card configuration-card"), heading = node("div", "card-heading");
        heading.append(node("h2", "", sectionNames[section] || section)); card.append(heading);
        const grid = node("div", "config-fields-grid");
        fields.forEach((field) => {
          if (!has(data, field.key)) return;
          const wrap = makeSettingField(field, data[field.key], "config", controls);
          if (["persona", "trigger_names", "group_allowlist", "private_allowlist", "group_blocklist", "user_blocklist"].includes(field.key)) wrap.classList.add("field-wide");
          grid.append(wrap);
        });
        if (grid.children.length) { card.append(grid); container.append(card); }
      }
      if (!controls.size) empty(container, "内核未提供此页的可编辑设置。请检查版本与设置定义。");
      settingsUI.controls.set(view, controls);
      $(`${view}-state`).textContent = "已与内核同步";
      $(`${view}-feedback`).textContent = "已读取服务器当前值，修改后保存生效。";
      $(`save-${view}`).disabled = !controls.size;
    }
    state.configDirty = settingsUI.dirty.size > 0;
    if (data.name && state.status) $("instance-name").textContent = String(data.name);
  }
  async function loadConfig() {
    const request = ++state.settingsRequest;
    const [data, schema] = await Promise.all([get("/api/config"), get("/api/settings/schema")]);
    if (!state.authenticated || request !== state.settingsRequest) return;
    state.config = obj(data); settingsUI.schema = schemaFields(schema);
    if (!settingsUI.schema.length) throw errorOf("内核没有返回设置定义，暂时无法编辑。请确认前后端版本一致。");
    renderConfiguration(state.config);
  }
  for (const view of ["settings", "strategy"]) {
    $(`${view}-form`).addEventListener("input", () => {
      settingsUI.dirty.add(view); settingsUI.revisions[view] += 1; state.configDirty = true;
      $(`${view}-state`).textContent = "有未保存的修改"; $(`${view}-feedback`).textContent = "当前修改尚未生效，保存后应用。";
    });
    $(`${view}-form`).addEventListener("submit", async (event) => {
      event.preventDefault(); const revision = settingsUI.revisions[view]; $(`save-${view}`).disabled = true; clearError();
      try {
        const patch = readControls(settingsUI.controls.get(view) || new Map());
        await api("/api/config", { method: "PUT", body: patch });
        state.config = obj(await get("/api/config"));
        if (revision === settingsUI.revisions[view]) settingsUI.dirty.delete(view);
        renderConfiguration(state.config); toast("设置已保存，后续消息将使用新设置。");
        if (settingsUI.dirty.has(view)) $(`${view}-feedback`).textContent = "上一版已保存；保存过程中新增的修改仍未保存。";
      } catch (error) { showError(error, "保存设置失败："); $(`${view}-feedback`).textContent = "当前编辑已保留，请检查错误后重试。"; }
      finally { $(`save-${view}`).disabled = !state.authenticated; }
    });
  }

  const providerTypes = { chatgpt_web: "ChatGPT 网页桥接", openai_compatible: "OpenAI 兼容", openai_responses: "OpenAI Responses", anthropic: "Anthropic", gemini: "Google Gemini", ollama: "Ollama" };
  async function loadProviders() {
    const request = ++modelsUI.request, data = obj(await get("/api/providers"));
    if (!state.authenticated || request !== modelsUI.request) return;
    modelsUI.profiles = arrayFrom(data, "profiles"); modelsUI.types = arrayFrom(data, "types"); modelsUI.defaultID = String(data.default_id || ""); renderProviders(); renderSessionProvider();
  }
  function providerName(id) { const profile = modelsUI.profiles.find((p) => p.id === id); return profile ? `${profile.name || profile.id}${profile.model ? ` · ${profile.model}` : ""}` : id; }
  function renderProviders() {
    const list = $("provider-list");
    if (!modelsUI.profiles.length) { empty(list, "尚未配置模型服务。点击“添加模型服务”连接你的第一个模型。"); return; }
    list.replaceChildren(...modelsUI.profiles.map((profile) => {
      const card = node("article", "card provider-card"), header = node("div", "card-heading"), title = node("div");
      title.append(node("h2", "", display(profile.name, profile.id)), node("p", "card-kicker provider-kind", display(providerTypes[profile.type], profile.type)));
      const badges = node("div", "feature-tags"); if (profile.id === modelsUI.defaultID) badges.append(pill("默认模型", "good")); badges.append(pill(profile.enabled ? "已启用" : "已停用", profile.enabled ? "good" : "neutral"));
      if (!profile.model) badges.append(pill("待选择模型", "warning"));
      header.append(title, badges); card.append(header);
      const details = node("dl", "profile-details");
      for (const [label, value] of [["模型", profile.model || (profile.type === "chatgpt_web" ? "网页当前所选模型" : "未设置")], ["地址", profile.base_url], ["凭据", profile.has_api_key || profile.key_configured ? "密钥 / 桥接令牌已保存" : "未保存密钥"], ["超时", `${display(profile.timeout, "120")} 秒`]]) details.append(node("dt", "", label), node("dd", "", display(value, "未设置")));
      card.append(details);
      const result = node("div", "provider-test-result"); result.hidden = true; result.setAttribute("role", "status");
      const actions = node("div", "provider-actions");
      const edit = node("button", "button secondary small", "编辑配置"), test = node("button", "button quiet small", "测试连接"), use = node("button", "button quiet small", profile.id === modelsUI.defaultID ? "当前默认" : "设为默认"), remove = node("button", "button danger small", "删除");
      [edit, test, use, remove].forEach((button) => { button.type = "button"; }); use.disabled = !profile.enabled || !profile.model || profile.id === modelsUI.defaultID;
      edit.addEventListener("click", () => openProviderEditor(profile));
      test.addEventListener("click", async () => {
        test.disabled = true; result.hidden = false; result.textContent = "正在请求真实模型，请稍候…"; result.classList.remove("error-text");
        try { const data = (await api(`/api/providers/${encodeURIComponent(profile.id)}/test`, { method: "POST", body: { text: "请只回复：连接正常" }, timeout: Math.max(150000, ((Number(profile.timeout) || 120) + 20) * 1000) })).data; result.textContent = typeof data === "string" ? data : safeStringify(data); }
        catch (error) { result.textContent = error.message; result.classList.add("error-text"); }
        finally { test.disabled = false; }
      });
      use.addEventListener("click", async () => { use.disabled = true; try { await api(`/api/providers/${encodeURIComponent(profile.id)}/default`, { method: "POST", body: {} }); await loadProviders(); toast("默认模型已更新。"); } catch (error) { use.disabled = false; showError(error, "更改默认模型失败："); } });
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        try { await api(`/api/providers/${encodeURIComponent(profile.id)}`, { method: "DELETE" }); await loadProviders(); toast("模型配置已删除。"); }
        catch (error) { remove.disabled = false; showError(error, "删除模型配置失败："); }
      });
      actions.append(edit, test, use, remove); card.append(actions, result); return card;
    }));
  }
  function updateProviderType() {
    const web = $("provider-type").value === "chatgpt_web";
    $("provider-model").required = false;
    const selected = modelsUI.types.find((type) => type.id === $("provider-type").value), previous = modelsUI.types.find((type) => type.id === modelsUI.formType);
    if (!$("provider-base").value || $("provider-base").value === previous?.base_url) $("provider-base").value = selected?.base_url || "";
    if (web && !$("provider-model").value) $("provider-model").value = selected?.model || "chatgpt-web";
    $("provider-temperature").max = $("provider-type").value === "anthropic" ? "1" : "2";
    modelsUI.formType = $("provider-type").value;
    $("provider-base-help").textContent = web ? "填写网页桥接服务地址，例如 http://chatgpt-web:17656；账号在服务器桌面登录。" : "填写服务根地址，包含提供方要求的 /v1 等路径。保存后可读取模型列表。";
    $("fetch-models").disabled = !modelsUI.editing || web;
  }
  function openProviderEditor(profile = null) {
    modelsUI.editing = profile ? profile.id : null; modelsUI.formType = profile?.type || ""; $("provider-form").reset(); $("provider-editor-error").hidden = true;
    $("provider-editor-title").textContent = profile ? "编辑模型服务" : "添加模型服务";
    const defaults = { id: "", name: "", type: "openai_compatible", base_url: "", model: "", temperature: "", top_p: "", max_tokens: "", timeout: 120, extra_body: {}, enabled: true, ...(profile || {}) };
    for (const key of ["id", "name", "type", "base_url", "model", "temperature", "top_p", "max_tokens", "timeout", "extra_body", "enabled"]) {
      const ids = { base_url: "base", max_tokens: "max-tokens", top_p: "top-p", extra_body: "extra" }, input = $(`provider-${ids[key] || key}`);
      if (key === "enabled") input.checked = defaults[key] === true;
      else input.value = key === "extra_body" ? safeStringify(defaults[key] || {}) : display(defaults[key], "");
    }
    $("provider-id").readOnly = !!profile; $("provider-key").value = ""; $("provider-clear-key").checked = false;
    $("provider-key-state").textContent = profile?.has_api_key || profile?.key_configured ? "已保存密钥 / 桥接令牌；留空保持不变。" : "尚未保存 API 密钥；无需密钥的本地服务可以留空。";
    $("provider-model-options").replaceChildren(); $("provider-models-feedback").textContent = profile ? "读取列表使用服务器已保存的地址和凭据；新修改请先保存。" : "首次添加请先保存，再读取该服务的模型列表。";
    $("provider-feedback").textContent = "保存后可测试真实连通性。"; $("save-provider").disabled = false; updateProviderType(); $("provider-editor").showModal();
  }
  $("add-provider").addEventListener("click", () => openProviderEditor());
  $("provider-type").addEventListener("change", updateProviderType);
  for (const id of ["provider-editor-close", "provider-editor-cancel"]) $(id).addEventListener("click", () => $("provider-editor").close());
  $("provider-editor").addEventListener("close", () => { $("provider-key").value = ""; });
  $("provider-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("provider-editor-error").hidden = true; $("save-provider").disabled = true;
    try {
      const extra = JSON.parse($("provider-extra").value || "{}");
      if (!extra || Array.isArray(extra) || typeof extra !== "object") throw errorOf("额外请求参数必须是 JSON 对象。");
      const profile = { id: $("provider-id").value.trim(), name: $("provider-name").value.trim(), type: $("provider-type").value, base_url: $("provider-base").value.trim(), model: $("provider-model").value.trim(), api_key: $("provider-key").value.trim(), clear_api_key: $("provider-clear-key").checked, enabled: $("provider-enabled").checked, timeout: Number($("provider-timeout").value), extra_body: extra };
      for (const [key, id] of [["temperature", "provider-temperature"], ["top_p", "provider-top-p"], ["max_tokens", "provider-max-tokens"]]) profile[key] = $(id).value === "" ? null : Number($(id).value);
      await api("/api/providers", { method: "POST", body: profile }); $("provider-key").value = "";
      $("provider-editor").close(); await loadProviders(); toast("模型服务已保存。");
    } catch (error) { $("provider-editor-error").textContent = error instanceof SyntaxError ? "额外请求参数不是有效的 JSON，请检查引号和逗号。" : error.message; $("provider-editor-error").hidden = false; }
    finally { $("save-provider").disabled = false; }
  });
  $("fetch-models").addEventListener("click", async () => {
    if (!modelsUI.editing) return; $("fetch-models").disabled = true; $("provider-models-feedback").textContent = "正在读取服务提供的模型列表…";
    try {
      const data = (await api(`/api/providers/${encodeURIComponent(modelsUI.editing)}/models`, { timeout: 450000 })).data, models = Array.isArray(data) ? data : arrayFrom(data, "models").length ? data.models : arrayFrom(data, "data");
      const ids = models.map((model) => typeof model === "string" ? model : String(model.id || model.name || "")).filter(Boolean);
      $("provider-model-options").replaceChildren(...ids.map((id) => { const option = node("option"); option.value = id; return option; }));
      $("provider-models-feedback").textContent = ids.length ? `已读取 ${ids.length} 个模型，在模型名称输入框中选择或手动填写。` : "服务未返回模型列表；请按提供方文档手动填写模型名称。";
    } catch (error) { $("provider-models-feedback").textContent = `读取失败：${error.message}`; }
    finally { $("fetch-models").disabled = false; }
  });
  function renderSessionProvider() {
    const select = $("session-provider"), session = state.sessions.find((s) => sessionID(s) === state.selectedSession), current = session?.provider_id || "";
    const options = [node("option", "", `使用默认模型${modelsUI.defaultID ? ` · ${providerName(modelsUI.defaultID)}` : ""}`)]; options[0].value = "";
    modelsUI.profiles.filter((p) => p.enabled || p.id === current).forEach((profile) => { const option = node("option", "", `${providerName(profile.id)}${profile.enabled ? "" : "（已停用）"}`); option.value = profile.id; options.push(option); });
    if (current && !modelsUI.profiles.some((p) => p.id === current)) { const missing = node("option", "", `当前配置不可用 · ${current}`); missing.value = current; options.push(missing); }
    select.replaceChildren(...options); select.value = current; select.disabled = !state.selectedSession; $("save-session-provider").disabled = !state.selectedSession;
  }
  $("save-session-provider").addEventListener("click", async () => {
    if (!state.selectedSession) return; const id = state.selectedSession; $("save-session-provider").disabled = true;
    try { await api(`/api/sessions/${encodeURIComponent(id)}/provider`, { method: "PUT", body: { provider_id: $("session-provider").value } }); await loadSessions(); toast("此会话的模型设置已保存。"); }
    catch (error) { showError(error, "保存会话模型失败："); }
    finally { $("save-session-provider").disabled = !state.selectedSession; }
  });

  async function loadConnections() {
    const data = obj(await get("/api/connections")); connectionsUI.data = data;
    if (connectionsUI.dirty) return;
    for (const [key, id] of [["onebot_url", "url"], ["self_id", "self"], ["owner_ids", "owners"], ["action_timeout", "action-timeout"], ["reconnect_min", "reconnect-min"], ["reconnect_max", "reconnect-max"]]) $("connection-" + id).value = Array.isArray(data[key]) ? data[key].join("\n") : display(data[key], "");
    $("connection-receive").checked = data.receive_enabled === true; $("connection-token").value = ""; $("connection-clear-token").checked = false;
    $("connection-token-state").textContent = data.token_configured ? "令牌已配置" : "未配置令牌"; $("connections-state").textContent = "已与内核同步"; $("save-connections").disabled = false;
  }
  $("connections-form").addEventListener("input", () => { connectionsUI.dirty = true; connectionsUI.revision += 1; $("connections-state").textContent = "有未保存的修改"; });
  $("connections-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const revision = connectionsUI.revision; $("save-connections").disabled = true;
    try {
      const body = { onebot_url: $("connection-url").value.trim(), self_id: $("connection-self").value.trim(), owner_ids: parseList($("connection-owners").value), onebot_token: $("connection-token").value.trim(), clear_token: $("connection-clear-token").checked, receive_enabled: $("connection-receive").checked, action_timeout: Number($("connection-action-timeout").value), reconnect_min: Number($("connection-reconnect-min").value), reconnect_max: Number($("connection-reconnect-max").value) };
      await api("/api/connections", { method: "PUT", body }); $("connection-token").value = "";
      if (revision === connectionsUI.revision) connectionsUI.dirty = false;
      await loadConnections(); $("connections-feedback").textContent = "平台参数已保存，连接状态请在运行概览查看。"; toast("平台连接已保存。");
    } catch (error) { showError(error, "保存平台连接失败："); $("connections-feedback").textContent = "未确认保存成功，请检查错误信息。"; }
    finally { $("save-connections").disabled = false; }
  });
  $("reconnect-platform").addEventListener("click", async () => {
    $("reconnect-platform").disabled = true;
    try { await api("/api/connections/reconnect", { method: "POST", body: {} }); toast("已请求重建平台连接。"); await loadStatus(); }
    catch (error) { showError(error, "重新连接失败："); }
    finally { $("reconnect-platform").disabled = false; }
  });

  function downloadJSON(data, name) { const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
  async function loadSystem() {
    const [info, diagnostic] = await Promise.all([get("/api/system/info"), get("/api/system/diagnostics")]);
    const rows = [["内核版本", info.version], ["配置格式版本", info.config_schema_version], ["Python", info.python], ["运行环境", info.platform], ["运行时长", duration(info.uptime)]];
    const details = node("dl", "profile-details"); rows.forEach(([label, value]) => details.append(node("dt", "", label), node("dd", "", display(value, "未提供"))));
    $("system-info").replaceChildren(details); $("system-diagnostics").textContent = safeStringify(diagnostic);
    const features = Array.isArray(info.features) ? info.features : Object.entries(obj(info.features)).filter(([, enabled]) => enabled).map(([name]) => name);
    const featureLabels = { multi_provider: "多个模型服务", per_session_provider: "会话独立模型", onebot_v11: "OneBot 11 消息连接", live_settings: "设置即时生效", resource_editor: "资源编辑与图片上传", plugin_settings: "插件参数管理", redacted_export: "脱敏配置导出" };
    $("system-features").replaceChildren(...features.map((feature) => pill(typeof feature === "string" ? featureLabels[feature] || feature : display(feature.name, safeStringify(feature)), "neutral")));
    if (info.paths && Object.keys(info.paths).length) { const paths = node("details", "configuration-details"); paths.append(node("summary", "", "数据目录"), node("pre", "json-result", safeStringify(info.paths))); $("system-info").append(paths); }
    if (info.release && Object.keys(info.release).length) { const release = node("details", "configuration-details"); release.append(node("summary", "", "发行信息与插件兼容范围"), node("pre", "json-result", safeStringify(info.release))); $("system-info").append(release); }
  }
  $("refresh-diagnostics").addEventListener("click", async () => { $("refresh-diagnostics").disabled = true; try { await loadSystem(); toast("诊断已更新。"); } catch (error) { showError(error, "检查失败："); } finally { $("refresh-diagnostics").disabled = false; } });
  $("export-config").addEventListener("click", async () => { $("export-config").disabled = true; try { downloadJSON(await get("/api/config/export"), `yukino-core-config-${new Date().toISOString().slice(0, 10)}.json`); toast("脱敏配置已导出，请另行备份凭据。"); } catch (error) { showError(error, "导出失败："); } finally { $("export-config").disabled = false; } });
  $("import-config-file").addEventListener("change", async () => {
    const file = $("import-config-file").files?.[0]; if (!file) return;
    try { if (file.size > 1024 * 1024) throw errorOf("配置文件不能超过 1 MB。"); $("import-config-json").value = await file.text(); }
    catch (error) { showError(error, "读取配置文件失败："); }
  });
  function importPayload() {
    let data; try { data = JSON.parse($("import-config-json").value); } catch { throw errorOf("配置内容不是有效的 JSON，请检查文件或输入。"); }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw errorOf("配置文件必须是 JSON 对象。");
    return data;
  }
  $("validate-config").addEventListener("click", async () => {
    $("validate-config").disabled = true; const result = $("import-config-result"); result.hidden = false;
    try { const data = importPayload(); const response = await api("/api/config/validate", { method: "POST", body: { config: data.config || data } }); result.textContent = `对话配置校验结果：\n${safeStringify(response.data)}\n模型和平台设置在应用导入时另行校验。`; }
    catch (error) { result.textContent = `校验失败：${error.message}`; }
    finally { $("validate-config").disabled = false; }
  });
  $("import-config-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("import-config").disabled = true; const result = $("import-config-result"); result.hidden = false;
    try { const response = await api("/api/config/import", { method: "POST", body: importPayload() }); result.textContent = `导入已应用：\n${safeStringify(response.data)}`; settingsUI.dirty.clear(); connectionsUI.dirty = false; await Promise.all([loadConfig(), loadProviders(), loadConnections()]); toast("导入配置已应用。"); }
    catch (error) { result.textContent = `导入失败：${error.message}\n请检查服务端状态后再决定是否重试。`; }
    finally { $("import-config").disabled = false; }
  });

  async function openPluginConfig(plugin) {
    const request = ++pluginUI.request; pluginUI.id = String(plugin.id || plugin.name); pluginUI.controls = new Map();
    $("plugin-config-title").textContent = `${plugin.display_name || plugin.name || plugin.id} · 参数`;
    empty($("plugin-config-fields"), "正在读取可编辑参数…"); $("plugin-config-error").hidden = true; $("save-plugin-config").disabled = true; $("plugin-config-feedback").textContent = "正在读取参数…";
    $("plugin-config-editor").showModal();
    try {
      const data = obj(await get(`/api/plugins/${encodeURIComponent(pluginUI.id)}/config`)); if (request !== pluginUI.request) return;
      const fields = schemaFields(data.schema), values = obj(data.values); $("plugin-config-fields").replaceChildren();
      if (!fields.length) { empty($("plugin-config-fields"), display(data.message, "此插件没有提供可编辑参数。启停仍可在插件列表完成。")); $("plugin-config-feedback").textContent = "暂无可编辑设置。"; return; }
      fields.forEach((field) => $("plugin-config-fields").append(makeSettingField(field, values[field.key], "plugin-setting", pluginUI.controls)));
      $("save-plugin-config").disabled = false; $("plugin-config-feedback").textContent = "修改仅在保存后生效。";
    } catch (error) { $("plugin-config-error").textContent = error.message; $("plugin-config-error").hidden = false; $("plugin-config-feedback").textContent = "无法读取参数，请确认插件与内核版本。"; }
  }
  for (const id of ["plugin-config-close", "plugin-config-cancel"]) $(id).addEventListener("click", () => $("plugin-config-editor").close());
  $("plugin-config-editor").addEventListener("close", () => { pluginUI.request += 1; });
  $("plugin-config-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("save-plugin-config").disabled = true; $("plugin-config-error").hidden = true;
    try { await api(`/api/plugins/${encodeURIComponent(pluginUI.id)}/config`, { method: "PUT", body: { values: readControls(pluginUI.controls) } }); $("plugin-config-editor").close(); toast("插件参数已保存。"); await loadPlugins(); }
    catch (error) { $("plugin-config-error").textContent = error.message; $("plugin-config-error").hidden = false; }
    finally { $("save-plugin-config").disabled = false; }
  });

  $("scope-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const scope = $("memory-scope").value.trim();
    if (!/^[pg]:[0-9]+$/.test(scope)) return;
    if (state.memoryEditing && state.memoryEditing.scope !== scope) state.memoryEditing = null;
    state.memoryScope = scope;
    updateMemoryEditor();
    clearError();
    void loadMemories(scope).catch((error) => showError(error, "记忆读取失败："));
  });
  async function loadMemories(scope) {
    if (!/^[pg]:[0-9]+$/.test(scope)) {
      $("create-memory").disabled = true;
      empty($("memory-list"), "输入作用域，查看这个会话的记忆。");
      return;
    }
    const request = ++state.memoriesRequest;
    $("create-memory").disabled = true;
    empty($("memory-list"), "正在读取记忆…");
    try {
      const data = await get(`/api/memories?scope=${encodeURIComponent(scope)}`);
      if (request !== state.memoriesRequest || scope !== state.memoryScope || !state.authenticated) return;
      updateMemoryEditor();
      const memories = arrayFrom(data, "memories");
      $("memory-count").textContent = `${memories.length} 条`;
      if (!memories.length) { empty($("memory-list"), "这个作用域还没有记忆"); return; }
      $("memory-list").replaceChildren(...memories.map((memory) => {
        const id = pick(memory.id, memory.memory_id), item = node("article", "memory-item"), footer = node("div", "memory-item-footer");
        item.append(node("p", "", display(pick(memory.text, memory.content), "（空记忆）")));
        footer.append(node("span", "", `${id !== undefined ? `#${id} · ` : ""}${formatTime(pick(memory.created_at, memory.time), "时间未知")}`));
        if (id !== undefined && id !== null) {
          const actions = node("div", "memory-item-actions"), edit = node("button", "text-button", "编辑"); edit.type = "button";
          edit.setAttribute("aria-label", `编辑记忆 ${id}`);
          edit.addEventListener("click", () => {
            state.memoryEditing = { id, scope };
            $("memory-text").value = display(pick(memory.text, memory.content), "");
            updateMemoryEditor(); $("memory-text").focus();
          });
          const button = node("button", "text-button", "忘记这条"); button.type = "button";
          button.addEventListener("click", async () => {
            button.disabled = true; clearError();
            try {
              await api(`/api/memories/${encodeURIComponent(id)}?scope=${encodeURIComponent(scope)}`, { method: "DELETE" });
              if (state.memoryEditing?.id === id && state.memoryEditing?.scope === scope) { state.memoryEditing = null; updateMemoryEditor(); }
              toast("记忆已停用。");
              if (state.memoryScope === scope) await loadMemories(scope);
            } catch (error) { showError(error, "忘记失败："); button.disabled = false; }
          });
          actions.append(edit, button); footer.append(actions);
        }
        item.append(footer); return item;
      }));
    } catch (error) {
      if (request === state.memoriesRequest) { empty($("memory-list"), "记忆暂不可用，请重新读取。"); $("memory-count").textContent = "不可用"; }
      throw error;
    } finally { if (request === state.memoriesRequest) $("create-memory").disabled = !state.authenticated; }
  }
  function updateMemoryEditor() {
    const editing = state.memoryEditing;
    $("memory-form-title").textContent = editing ? "编辑这条记忆" : "新增一条记忆";
    $("create-memory").textContent = editing ? "保存修改 ↗" : "保存记忆 ＋";
    $("cancel-memory-edit").hidden = !editing;
    $("memory-target").textContent = editing ? `正在编辑 #${editing.id} · ${editing.scope}` : state.memoryScope ? `将保存到：${state.memoryScope}` : "请先读取一个会话作用域";
  }
  $("cancel-memory-edit").addEventListener("click", () => { state.memoryEditing = null; $("memory-text").value = ""; updateMemoryEditor(); });
  $("memory-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("memory-text").value.trim(), editing = state.memoryEditing, scope = editing?.scope || state.memoryScope;
    if (!text || !/^[pg]:[0-9]+$/.test(scope)) return;
    $("create-memory").disabled = true; $("cancel-memory-edit").disabled = true; clearError();
    try {
      const result = await api(editing ? `/api/memories/${encodeURIComponent(editing.id)}?scope=${encodeURIComponent(scope)}` : "/api/memories", { method: editing ? "PATCH" : "POST", body: editing ? { text } : { scope, text } });
      if (editing && result.data?.updated === false) throw errorOf("这条记忆已不存在或已被忘记，修改未保存。");
      if ($("memory-text").value.trim() === text && state.memoryEditing === editing) { $("memory-text").value = ""; state.memoryEditing = null; updateMemoryEditor(); }
      toast(editing ? "记忆修改已保存。" : `记忆已保存到 ${scope}。`);
      if (state.memoryScope === scope) await loadMemories(scope);
    } catch (error) { showError(error, "保存记忆失败："); }
    finally { $("create-memory").disabled = !state.authenticated; $("cancel-memory-edit").disabled = false; }
  });

  async function loadPlugins() {
    const data = await get("/api/plugins");
    if (!state.authenticated) return;
    state.plugins = arrayFrom(data, "plugins");
    $("enable-all-plugins").disabled = !state.plugins.length;
    renderPlugins();
  }
  function renderPlugins() {
    const query = $("plugin-search").value.trim().toLocaleLowerCase(), status = $("plugin-status-filter").value, kind = $("plugin-kind-filter").value;
    const visible = state.plugins.filter((plugin) => {
      const searchable = [plugin.name, plugin.id, plugin.description, ...(Array.isArray(plugin.commands) ? plugin.commands : [])].join(" ").toLocaleLowerCase();
      return (!query || searchable.includes(query)) && (kind === "all" || plugin.type === kind) &&
        (status === "all" || (status === "enabled" && plugin.enabled === true) || (status === "disabled" && plugin.enabled === false) ||
          (status === "failed" && (plugin.state === "failed" || Boolean(plugin.error || plugin.last_error))));
    });
    $("plugin-filter-count").textContent = `显示 ${visible.length} / ${state.plugins.length} 个插件 · 已启用 ${state.plugins.filter((p) => p.enabled === true).length} 个`;
    if (!state.plugins.length) { empty($("plugin-list"), "当前没有已安装的插件"); return; }
    if (!visible.length) { empty($("plugin-list"), "没有符合当前搜索和筛选条件的插件。"); return; }
    $("plugin-list").replaceChildren(...visible.map((plugin) => {
      const id = pick(plugin.id, plugin.name), enabled = plugin.enabled === true;
      const builtin = plugin.builtin === true || ["utilities", "sessions", "memory", "emoji", "knowledge", "dice", "calculator"].includes(String(id));
      const card = node("article", "card plugin-card"), header = node("div", "plugin-card-header"), title = node("div", "plugin-title");
      card.dataset.pluginId = String(id);
      title.append(node("h2", "", display(pick(plugin.display_name, plugin.name, plugin.id), "未命名插件")), node("p", "mono", display(plugin.id, "ID 未提供")));
      header.append(node("span", "plugin-symbol", "⊞"), title, pill(plugin.enabled === undefined ? "状态未知" : enabled ? "已启用" : "已停用", plugin.enabled === undefined ? "neutral" : enabled ? "good" : "neutral"));
      card.append(header, node("p", "plugin-description", display(plugin.description, "此插件没有提供说明。")));
      if (Array.isArray(plugin.commands) && plugin.commands.length) card.append(node("p", "plugin-commands", `指令：${plugin.commands.join(" · ")}`));
      const error = pick(plugin.last_error, plugin.error);
      if (error) card.append(node("p", "plugin-error", display(error)));
      const footer = node("div", "plugin-foot"), toggleLabel = node("label", "plugin-toggle"), toggle = node("input", "switch");
      toggle.type = "checkbox"; toggle.checked = enabled; toggle.disabled = id === undefined || plugin.enabled === undefined;
      toggle.setAttribute("aria-label", `${enabled ? "停用" : "启用"} ${display(plugin.name, id)}`);
      toggleLabel.append(node("span", "", "启用插件"), toggle);
      footer.append(node("small", "", `${builtin ? "内置" : plugin.type === "process" ? "外部进程" : "插件"} · ${display(pick(plugin.version, plugin.kind, plugin.type), "版本未提供")}`), toggleLabel);
      toggle.addEventListener("change", async () => {
        const desired = toggle.checked;
        toggle.disabled = true; clearError();
        try {
          await api("/api/plugins/toggle", { method: "POST", body: { id: String(id), enabled: desired } });
          toast(desired ? "插件已启用。" : "插件已停用。");
          await loadPlugins();
        } catch (error) { toggle.checked = !desired; toggle.disabled = false; showError(error, "插件切换失败："); }
      });
      card.append(footer);
      const configure = node("button", "button secondary small plugin-config-button", "配置参数"); configure.type = "button";
      configure.addEventListener("click", () => { void openPluginConfig(plugin); });
      const management = node("div", "plugin-management-actions"); management.append(configure);
      if (!builtin && plugin.type === "process") {
        const remove = node("button", "button danger small", "移除插件"); remove.type = "button";
        remove.addEventListener("click", () => openPluginRemoval(plugin)); management.append(remove);
      }
      card.append(management);
      if (id === "video_parser") {
        const section = node("details", "plugin-description"), summary = node("summary", "", "抖音站点会话（选填）");
        const hint = node("p", "", "仅当原站要求登录时使用。手动填写的会话仅保存在你的 VPS，保存后不会回显，7 天后自动停止使用。B 站公开视频通常无需填写。");
        const label = node("label", "", "抖音 Cookie"), input = node("input", ""), actions = node("div", "plugin-foot");
        input.type = "password"; input.maxLength = 16384; input.autocomplete = "off"; input.placeholder = "在此填写你自己的站点会话";
        label.append(input);
        const save = node("button", "", "保存会话"), clear = node("button", "", "清除会话"), message = node("p", "", "正在读取会话状态…");
        const describe = (data) => { message.textContent = data.configured ? "已保存；是否仍有效由原站决定。" : "尚未保存有效期内的会话。"; };
        const update = async (value) => {
          save.disabled = clear.disabled = true;
          try { describe((await api("/api/plugins/video/session", { method: "POST", body: { cookie: value } })).data); input.value = ""; toast(value ? "会话已保存。" : "会话已清除。"); }
          catch (error) { showError(error, "保存站点会话失败："); }
          finally { save.disabled = clear.disabled = false; }
        };
        save.addEventListener("click", () => { if (input.value.trim()) update(input.value); else toast("请先填写站点会话。"); });
        clear.addEventListener("click", () => update(""));
        actions.append(save, clear); section.append(summary, hint, label, actions, message); card.append(section);
        get("/api/plugins/video/session").then(describe).catch(() => { message.textContent = "暂时无法读取会话状态。"; });
      }
      return card;
    }));
  }
  $("plugin-search").addEventListener("input", renderPlugins);
  for (const id of ["plugin-status-filter", "plugin-kind-filter"]) $(id).addEventListener("change", renderPlugins);
  $("enable-all-plugins").addEventListener("click", async () => {
    $("enable-all-plugins").disabled = true; clearError();
    try { await api("/api/plugins/batch", { method: "POST", body: { enabled: true } }); toast("已提交全部插件启用，状态已刷新。"); await loadPlugins(); }
    catch (error) { showError(error, "批量启用失败："); }
    finally { $("enable-all-plugins").disabled = !state.authenticated || !state.plugins.length; }
  });

  const pluginZipLimit = 2_000_000;
  function installResult(message, failed = false) {
    const result = $("plugin-install-result"); result.textContent = message; result.hidden = false; result.classList.toggle("error-text", failed);
  }
  async function refreshPluginsAfterChange() {
    try { await loadPlugins(); return true; }
    catch (error) { showError(error, "操作已完成，但最新插件清单读取失败："); return false; }
  }
  function selectedPluginArchive() {
    const file = $("plugin-install-file").files?.[0];
    if (!file) throw errorOf("请先选择本地插件 ZIP。");
    if (!/\.zip$/i.test(file.name)) throw errorOf("请选择 .zip 插件安装包。");
    if (!file.size || file.size > pluginZipLimit) throw errorOf("安装包须为非空 ZIP，且不能超过 2 MB。");
    return file;
  }
  $("plugin-install-file").addEventListener("change", () => {
    try { const file = selectedPluginArchive(); $("plugin-install-selected").textContent = `${file.name} · ${(file.size / 1000).toFixed(1)} KB；点击安装后仍保持停用。`; $("install-plugin").disabled = pluginUI.installBusy; }
    catch (error) { $("plugin-install-selected").textContent = error.message; $("install-plugin").disabled = true; }
  });
  $("plugin-install-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (pluginUI.installBusy) return;
    pluginUI.installBusy = true; $("install-plugin").disabled = true; $("plugin-install-file").disabled = true;
    try {
      const file = selectedPluginArchive(), body = new FormData(); body.append("file", file, file.name);
      installResult("正在上传并校验插件包…");
      const result = (await api("/api/plugins/install", { method: "POST", body, timeout: 60000 })).data;
      const id = result?.id || result?.plugin?.id || result?.installed?.id;
      installResult(`插件${id ? ` ${id}` : ""}已安装，默认停用。请先检查参数，再在插件列表启用。${result?.message ? `\n${result.message}` : ""}`);
      $("plugin-install-file").value = ""; $("plugin-install-selected").textContent = "安装包已处理。可继续选择另一个插件 ZIP。";
      await refreshPluginsAfterChange(); toast("插件已安装，当前保持停用。");
    } catch (error) { installResult(`安装未确认成功：${error.message}`, true); }
    finally { pluginUI.installBusy = false; $("plugin-install-file").disabled = false; $("install-plugin").disabled = !$("plugin-install-file").files?.length; }
  });
  $("reload-plugins").addEventListener("click", async () => {
    $("reload-plugins").disabled = true;
    try { await api("/api/plugins/reload", { method: "POST", body: {} }); const refreshed = await refreshPluginsAfterChange(); installResult(refreshed ? "插件清单与源码已重新加载。启用状态以列表为准。" : "重新加载已完成；最新清单读取失败，请稍后刷新核对。"); toast("插件已重新加载。"); }
    catch (error) { installResult(`重新加载失败：${error.message}`, true); }
    finally { $("reload-plugins").disabled = false; }
  });
  function openPluginRemoval(plugin) {
    pluginUI.removing = { id: String(plugin.id), name: display(plugin.display_name || plugin.name, plugin.id) }; pluginUI.removeBusy = false;
    $("plugin-remove-name").textContent = `${pluginUI.removing.name}（${pluginUI.removing.id}）`;
    $("plugin-remove-error").hidden = true; $("plugin-remove-feedback").textContent = "源码文件将保留。"; $("confirm-plugin-remove").disabled = false;
    $("plugin-remove-dialog").showModal();
  }
  for (const id of ["plugin-remove-close", "plugin-remove-cancel"]) $(id).addEventListener("click", () => { if (!pluginUI.removeBusy) $("plugin-remove-dialog").close(); });
  $("plugin-remove-dialog").addEventListener("cancel", (event) => { if (pluginUI.removeBusy) event.preventDefault(); });
  $("confirm-plugin-remove").addEventListener("click", async () => {
    if (!pluginUI.removing || pluginUI.removeBusy) return;
    pluginUI.removeBusy = true; $("confirm-plugin-remove").disabled = true; $("plugin-remove-error").hidden = true; $("plugin-remove-feedback").textContent = "正在移出插件清单…";
    const target = pluginUI.removing;
    try {
      const result = (await api(`/api/plugins/${encodeURIComponent(target.id)}`, { method: "DELETE" })).data;
      if (result?.removed !== true) throw errorOf("服务未确认插件已移除，请刷新清单核对。");
      $("plugin-remove-dialog").close(); await refreshPluginsAfterChange();
      installResult(result.files_retained === true ? `${target.name} 已移除，源码文件保留在服务器，供之后恢复。` : `${target.name} 已移除；服务未返回文件保留状态，请检查服务器文件。`);
      toast("插件已移出清单。");
    } catch (error) { $("plugin-remove-error").textContent = error.message; $("plugin-remove-error").hidden = false; $("plugin-remove-feedback").textContent = "移除未确认成功，可刷新清单后核对。"; }
    finally { pluginUI.removeBusy = false; $("confirm-plugin-remove").disabled = false; }
  });

  const resourceKinds = {
    images: { name: "表情图片", single: "图片", icon: "▧", fields: [{ key: "description", label: "图片描述", rows: 3, max: 500, placeholder: "描述图片内容与适用的聊天情景…" }, { key: "tags", label: "检索标签", max: 2480, placeholder: "例如：猫猫, 无语, 开心", hint: "逗号或换行分隔，最多 40 个标签，每个最多 60 字。", array: true, maxItems: 40, maxItemLength: 60 }] },
    jargon: { name: "用语与梗", single: "用语", icon: "⌘", fields: [{ key: "text", label: "用语", max: 100, required: true, placeholder: "例如：绷不住了" }, { key: "meaning", label: "含义与使用边界", rows: 6, max: 600, required: true, placeholder: "解释含义、语境和不适合使用的场合…" }] },
    expressions: { name: "表达方式", single: "表达方式", icon: "◇", fields: [{ key: "situation", label: "适用情景", rows: 2, max: 200, required: true, placeholder: "什么情景下适合这样回应？" }, { key: "style", label: "表达风格", rows: 4, max: 300, required: true, placeholder: "描述语气、措辞与表达方式…" }, { key: "examples", label: "表达示例", rows: 4, max: 803, placeholder: "每行一条示例，可留空", hint: "每行一条，最多 4 条，每条最多 200 字。", array: true, lines: true, maxItems: 4, maxItemLength: 200 }] },
    knowledge: { name: "知识", single: "知识", icon: "▤", fields: [{ key: "title", label: "知识标题", max: 120, placeholder: "一个清晰、易检索的标题，可留空" }, { key: "text", label: "知识内容", rows: 9, max: 1200, required: true, placeholder: "写下准确、有帮助的参考内容…" }] },
  };
  const imageUploadLimit = 5_000_000;
  const imageUploadTypes = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);
  function resourceCount(value) {
    if (Array.isArray(value)) return value.length;
    if (typeof value === "object" && value !== null) return pick(value.count, value.total, value.entries, value.items, value.loaded_count);
    return value;
  }
  function resourceTotal(data) {
    const declared = number(pick(data.total_count, data.total));
    if (declared !== null) return declared;
    if (data.total && typeof data.total === "object") {
      const totals = [data.total.images, data.total.jargon, data.total.expressions, data.total.knowledge];
      if (totals.every((value) => number(value) !== null)) return totals.reduce((sum, value) => sum + Number(value), 0);
    }
    const categories = [pick(data.jargon, data.jargons), data.knowledge, data.expressions, data.images].map(resourceCount);
    if (categories.some((value) => number(value) === null)) return undefined;
    return categories.reduce((sum, value) => sum + Number(value), 0);
  }
  async function loadResources() {
    const results = await Promise.allSettled([loadResourceSummary(), loadResourceList()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure) throw failure.reason;
  }
  async function loadResourceSummary() {
    const request = ++library.summaryRequest;
    const data = obj(await get("/api/resources"));
    if (!state.authenticated || request !== library.summaryRequest) return;
    const counts = obj(pick(data.counts, data.statistics));
    const active = obj(data.active);
    const jargon = resourceCount(pick(active.jargon, data.jargon_count, counts.jargon, data.jargon));
    const knowledge = resourceCount(pick(active.knowledge, data.knowledge_count, counts.knowledge, data.knowledge));
    const expressions = resourceCount(pick(active.expressions, data.expression_count, counts.expressions, data.expressions));
    const emoji = resourceCount(pick(active.images, data.emoji_count, data.image_count, counts.emoji, counts.images, data.emoji, data.images));
    const resourceErrors = Array.isArray(data.errors) && data.errors.length > 0;
    const readyText = data.ready === true ? resourceErrors ? "已就绪 · 部分资源异常" : "已就绪" : data.ready === false ? "资源库未就绪" : "就绪状态未知";
    const readyTone = data.ready === false ? "bad" : resourceErrors ? "warning" : data.ready === true ? "good" : "neutral";
    $("resource-state").textContent = readyText;
    $("resource-summary").replaceChildren(
      metric("可用表情", count(emoji), "已验证可读取的图片", "▧", readyTone), metric("可用用语", count(jargon), "用于当前回复检索", "⌘", readyTone),
      metric("表达方式", count(expressions), "用于当前回复检索", "◇", readyTone), metric("知识条目", count(knowledge), "用于当前回复检索", "▤", readyTone)
    );
  }
  function resourceError(error) {
    if (error?.code === "auth_expired") return;
    $("resource-list-error").textContent = error?.message || "资源读取失败，请刷新重试。";
    $("resource-list-error").hidden = false;
  }
  function resourcePath(kind, id) { return `/api/resources/${kind}${id === undefined ? "" : `/${encodeURIComponent(String(id))}`}`; }
  function resourceImageURL(item) {
    const id = pick(item?.resource_id, item?.id);
    if (/^media_[a-f0-9]{32}$/.test(String(id))) return `/api/plugin-media/${id}`;
    if (id !== undefined && id !== null && String(id).trim()) return `${resourcePath("images", id)}/file`;
    if (typeof item?.preview_url !== "string") return "";
    try {
      const url = new URL(item.preview_url, window.location.origin);
      if (url.origin !== window.location.origin || url.username || url.password || url.search || url.hash || !/^\/api\/resources\/images\/[^/]+\/file$/.test(url.pathname)) return "";
      return url.pathname;
    } catch { return ""; }
  }
  function managedFile(item) {
    const box = node("div", "chat-file"), id = String(item.resource_id || "");
    if (!/^media_[a-f0-9]{32}$/.test(id)) return node("span", "", "附件暂不可用");
    const url = `/api/plugin-media/${id}`;
    if (item.type === "audio") {
      const audio = node("audio", "chat-audio");
      audio.controls = true; audio.preload = "none"; audio.src = url;
      box.append(audio);
    }
    const link = node("a", "", display(item.filename || item.description, "下载附件"));
    link.href = url; link.download = item.filename || "attachment"; box.append(link);
    return box;
  }
  function managedImageButton(item, className = "resource-thumbnail") {
    const description = display(item.description, "图片资源"), url = resourceImageURL(item);
    const button = node("button", className), fallback = node("span", "image-fallback", "图片加载中…");
    button.type = "button";
    button.setAttribute("aria-label", `查看大图：${description}`);
    if (url) {
      const image = node("img");
      image.alt = description; image.loading = "lazy"; image.decoding = "async"; image.referrerPolicy = "no-referrer";
      image.addEventListener("load", () => { fallback.hidden = true; });
      image.addEventListener("error", () => { image.hidden = true; fallback.textContent = "图片暂不可用"; fallback.hidden = false; });
      image.src = url;
      button.append(image, fallback);
      button.addEventListener("click", () => openImagePreview(item));
    } else { fallback.textContent = "图片附件暂不可用"; button.append(fallback); button.disabled = true; }
    return button;
  }
  function openImagePreview(item) {
    const url = resourceImageURL(item);
    if (!url) return;
    const description = display(item.description, "图片预览"), image = $("image-preview-file");
    $("image-preview-title").textContent = "图片预览";
    $("image-preview-description").textContent = description;
    $("image-preview-error").hidden = true;
    image.hidden = false; image.alt = description;
    image.onload = () => { image.hidden = false; $("image-preview-error").hidden = true; };
    image.onerror = () => { image.hidden = true; $("image-preview-error").textContent = "这张图片暂时无法读取。请检查资源状态，或刷新后重试。"; $("image-preview-error").hidden = false; };
    image.src = url;
    if (!$("image-preview").open) $("image-preview").showModal();
  }
  $("image-preview-close").addEventListener("click", () => $("image-preview").close());
  $("image-preview").addEventListener("close", () => {
    $("image-preview-file").onload = null; $("image-preview-file").onerror = null; $("image-preview-file").removeAttribute("src");
  });
  function updateResourceControls() {
    for (const tab of document.querySelectorAll("[data-resource-kind]")) {
      const active = tab.dataset.resourceKind === library.kind;
      tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); tab.tabIndex = active ? 0 : -1;
    }
    $("resource-results").setAttribute("aria-labelledby", `resource-tab-${library.kind}`);
    $("resource-results").className = `resource-results ${library.kind === "images" ? "image-resources" : "text-resources"}`;
    $("add-resource").textContent = library.kind === "images" ? "上传图片 ＋" : `新增${resourceKinds[library.kind].single} ＋`;
    $("resource-prev").disabled = library.loading || library.offset <= 0;
    $("resource-next").disabled = library.loading || library.total === null || library.offset + library.limit >= library.total;
  }
  async function loadResourceList() {
    const request = ++library.request;
    const current = { kind: library.kind, query: library.query, offset: library.offset, limit: library.limit, disabled: library.includeDisabled };
    library.loading = true;
    $("resource-list-error").hidden = true;
    $("resource-results").setAttribute("aria-busy", "true");
    $("resource-page-info").textContent = "正在加载…";
    empty($("resource-results"), "正在读取资源…");
    updateResourceControls();
    const params = new URLSearchParams({ q: current.query, offset: String(current.offset), limit: String(current.limit), include_disabled: String(current.disabled) });
    try {
      const data = obj(await get(`${resourcePath(current.kind)}?${params}`));
      if (request !== library.request || !state.authenticated) return;
      const total = number(data.total);
      if (total !== null && current.offset > 0 && current.offset >= total) {
        library.offset = total > 0 ? Math.floor((total - 1) / current.limit) * current.limit : 0;
        return await loadResourceList();
      }
      library.items = Array.isArray(data.items) ? data.items : [];
      library.total = total;
      renderResourceList();
      const first = library.items.length ? library.offset + 1 : 0, last = library.offset + library.items.length;
      $("resource-page-info").textContent = total === null ? `本页 ${library.items.length} 条 · 总数未知` : `共 ${count(total)} 条${total ? ` · ${first}–${last}` : ""}`;
    } catch (error) {
      if (request !== library.request) return;
      library.items = []; library.total = null;
      empty($("resource-results"), "资源暂不可用，请刷新重试。");
      $("resource-page-info").textContent = "未能加载";
      resourceError(error); throw error;
    } finally {
      if (request === library.request) { library.loading = false; $("resource-results").setAttribute("aria-busy", "false"); updateResourceControls(); }
    }
  }
  function renderResourceList() {
    const kind = library.kind, definition = resourceKinds[kind];
    if (!library.items.length) {
      const blank = node("div", "resource-empty-state");
      blank.append(node("span", "", definition.icon), node("h3", "", library.query ? "没有找到匹配的资源" : `还没有${definition.name}`), node("p", "", library.query ? "换个关键词，或打开“显示已停用”再试一次。" : kind === "images" ? "上传一张图片，让回复多一点表情。" : "点击右上角的新增按钮，写下第一条资源。"));
      $("resource-results").replaceChildren(blank); return;
    }
    $("resource-results").replaceChildren(...library.items.map((item) => {
      const card = node("article", `resource-item${item.enabled === false ? " disabled-resource" : ""}`);
      if (kind === "images") card.append(managedImageButton(item));
      const body = node("div", "resource-item-body");
      const title = kind === "images" ? display(item.description, "未填写描述") : kind === "jargon" ? display(item.text, "未命名用语") : kind === "expressions" ? display(item.situation, "未填写情景") : display(item.title, "未命名知识");
      body.append(node("h3", "resource-item-title", title));
      if (kind === "images") {
        const tags = node("div", "resource-tags");
        if (Array.isArray(item.tags) && item.tags.length) {
          tags.append(...item.tags.slice(0, 5).map((tag) => node("span", "", display(tag, ""))));
          if (item.tags.length > 5) tags.append(node("span", "", `+${item.tags.length - 5}`));
        } else tags.append(node("span", "tag-empty", "暂无标签"));
        body.append(tags);
      } else {
        const content = kind === "jargon" ? item.meaning : kind === "expressions" ? item.style : item.text;
        body.append(node("p", "resource-item-content", display(content, "暂无内容")));
        if (kind === "expressions" && Array.isArray(item.examples) && item.examples.length) {
          const examples = node("div", "resource-examples");
          examples.append(...item.examples.slice(0, 2).map((example) => node("p", "", `“${display(example, "")}”`)));
          if (item.examples.length > 2) examples.append(node("small", "", `另有 ${item.examples.length - 2} 条示例`));
          body.append(examples);
        }
      }
      const footer = node("div", "resource-item-footer"), actions = node("div", "resource-item-actions");
      footer.append(pill(item.enabled === true ? "已启用" : item.enabled === false ? "已停用" : "状态未知", item.enabled === true ? "good" : "neutral"));
      const edit = node("button", "text-button", "编辑"); edit.type = "button"; edit.setAttribute("aria-label", `编辑${definition.single}：${title}`);
      edit.addEventListener("click", () => openResourceEditor(kind, item)); actions.append(edit);
      if (item.id !== undefined && typeof item.enabled === "boolean") {
        const toggle = node("button", `text-button ${item.enabled ? "resource-disable" : "resource-restore"}`, item.enabled ? "停用" : "恢复");
        toggle.type = "button"; toggle.setAttribute("aria-label", `${item.enabled ? "停用" : "恢复"}${definition.single}：${title}`);
        toggle.addEventListener("click", async () => {
          toggle.disabled = true; edit.disabled = true;
          try {
            await api(resourcePath(kind, item.id), item.enabled ? { method: "DELETE" } : { method: "PATCH", body: { enabled: true } });
            toast(item.enabled ? "资源已停用，可在“显示已停用”中恢复。" : "资源已恢复，即刻用于后续回复。");
            await refreshResourcesAfterMutation();
          } catch (error) { resourceError(error); toggle.disabled = false; edit.disabled = false; }
        });
        actions.append(toggle);
      }
      footer.append(actions); body.append(footer); card.append(body); return card;
    }));
  }
  function selectResourceKind(kind, { focus = false } = {}) {
    if (!has(resourceKinds, kind)) return;
    library.kind = kind; library.offset = 0;
    updateResourceControls();
    if (focus) $(`resource-tab-${kind}`).focus();
    void loadResourceList().catch(resourceError);
  }
  for (const tab of document.querySelectorAll("[data-resource-kind]")) {
    tab.addEventListener("click", () => selectResourceKind(tab.dataset.resourceKind));
    tab.addEventListener("keydown", (event) => {
      const kinds = Object.keys(resourceKinds), current = kinds.indexOf(library.kind);
      let next;
      if (event.key === "ArrowRight") next = (current + 1) % kinds.length;
      if (event.key === "ArrowLeft") next = (current - 1 + kinds.length) % kinds.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = kinds.length - 1;
      if (next !== undefined) { event.preventDefault(); selectResourceKind(kinds[next], { focus: true }); }
    });
  }
  $("resource-search-form").addEventListener("submit", (event) => { event.preventDefault(); library.query = $("resource-search").value.trim(); library.offset = 0; void loadResourceList().catch(resourceError); });
  $("resource-search").addEventListener("search", () => { if (!$("resource-search").value) { library.query = ""; library.offset = 0; void loadResourceList().catch(resourceError); } });
  $("resource-show-disabled").addEventListener("change", () => { library.includeDisabled = $("resource-show-disabled").checked; library.offset = 0; void loadResourceList().catch(resourceError); });
  $("resource-prev").addEventListener("click", () => { if (library.loading) return; library.offset = Math.max(0, library.offset - library.limit); void loadResourceList().catch(resourceError); });
  $("resource-next").addEventListener("click", () => { if (library.loading || library.total === null || library.offset + library.limit >= library.total) return; library.offset += library.limit; void loadResourceList().catch(resourceError); });
  $("add-resource").addEventListener("click", () => openResourceEditor(library.kind));
  async function refreshResourcesAfterMutation() {
    const results = await Promise.allSettled([loadResourceSummary(), loadResourceList()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure) resourceError(errorOf(`资源已保存，但列表刷新失败：${failure.reason?.message || "请点击刷新重试。"}`));
  }
  function editorError(error) { $("resource-editor-error").textContent = error?.message || "保存失败，请检查输入。"; $("resource-editor-error").hidden = false; }
  function openResourceEditor(kind, item = null) {
    if (!has(resourceKinds, kind) || $("resource-editor").open) return;
    const definition = resourceKinds[kind], uploading = kind === "images" && !item;
    const editor = { kind, item, uploading, busy: false, dirty: false };
    library.editor = editor;
    $("resource-edit-form").reset();
    $("resource-editor-error").hidden = true;
    $("resource-editor-title").textContent = `${item ? "编辑" : uploading ? "上传" : "新增"}${definition.single}`;
    $("resource-editor-subtitle").textContent = item?.enabled === false ? "此资源已停用。编辑内容后，仍需在列表中恢复才会用于回复。" : "保存后即刻用于后续回复，无需重启。";
    $("resource-editor-feedback").textContent = "修改仅在保存后生效。";
    const fields = [];
    if (kind === "images") {
      if (uploading) {
        const group = node("div", "resource-field"), label = node("label", "", "选择图片"), file = node("input", "resource-file-input");
        label.htmlFor = "resource-file"; file.id = "resource-file"; file.type = "file"; file.accept = "image/jpeg,image/png,image/gif,image/webp"; file.required = true;
        const hint = node("p", "field-help", "支持 JPG、PNG、GIF、WebP，单张最大 5 MB（5,000,000 字节）。"), selected = node("p", "resource-selected-file"); selected.id = "resource-selected-file";
        const preview = node("img", "upload-image-preview"); preview.id = "resource-upload-preview"; preview.hidden = true; preview.alt = "待上传图片预览";
        file.addEventListener("change", () => {
          preview.hidden = true; preview.removeAttribute("src"); selected.textContent = "";
          const chosen = file.files?.[0];
          if (!chosen) return;
          try { validateImageFile(chosen); } catch (error) { file.value = ""; editorError(error); return; }
          $("resource-editor-error").hidden = true;
          selected.textContent = `${chosen.name} · ${(chosen.size / 1_000_000).toFixed(2)} MB`;
          const reader = new FileReader();
          reader.onload = () => { if (library.editor === editor && file.files?.[0] === chosen) { preview.src = String(reader.result); preview.hidden = false; } };
          reader.readAsDataURL(chosen);
        });
        group.append(label, file, hint, selected, preview); fields.push(group);
      } else fields.push(managedImageButton(item, "editor-image-preview"));
    }
    for (const field of definition.fields) {
      const group = node("div", "resource-field"), label = node("label", "", field.label), input = node(field.rows ? "textarea" : "input");
      input.id = `resource-field-${field.key}`; input.name = field.key; label.htmlFor = input.id;
      if (field.rows) input.rows = field.rows; else input.type = "text";
      input.required = field.required === true; input.maxLength = field.max; input.placeholder = field.placeholder || "";
      input.value = Array.isArray(item?.[field.key]) ? item[field.key].join(field.lines ? "\n" : ", ") : display(item?.[field.key], "");
      group.append(label, input);
      if (field.hint) group.append(node("p", "field-help", field.hint));
      fields.push(group);
    }
    $("resource-editor-fields").replaceChildren(...fields);
    setEditorBusy(false);
    $("resource-editor").showModal();
  }
  function validateImageFile(file) {
    if (!file) throw errorOf("请先选择一张图片。");
    if (!imageUploadTypes.has(file.type)) throw errorOf("请选择 JPG、PNG、GIF 或 WebP 图片。");
    if (file.size > imageUploadLimit) throw errorOf("图片超过 5 MB，请选择不超过 5,000,000 字节的图片。");
    if (!file.size) throw errorOf("图片文件为空，请重新选择。");
  }
  function setEditorBusy(busy) {
    if (!library.editor) return;
    library.editor.busy = busy;
    for (const input of $("resource-editor-fields").querySelectorAll("input,textarea,button")) input.disabled = busy;
    $("resource-editor-close").disabled = busy; $("resource-editor-cancel").disabled = busy; $("resource-editor-save").disabled = busy;
    $("resource-editor-save").textContent = busy ? library.editor.uploading ? "正在上传…" : "正在保存…" : library.editor.uploading ? "上传并保存 ↗" : "保存资源 ↗";
  }
  $("resource-edit-form").addEventListener("input", () => {
    if (!library.editor) return;
    library.editor.dirty = true;
    $("resource-editor-feedback").textContent = "有未保存的修改；关闭编辑器将放弃本次编辑。";
  });
  function closeResourceEditor() { if (!library.editor?.busy) $("resource-editor").close(); }
  $("resource-editor-close").addEventListener("click", closeResourceEditor);
  $("resource-editor-cancel").addEventListener("click", closeResourceEditor);
  $("resource-editor").addEventListener("cancel", (event) => { if (library.editor?.busy) event.preventDefault(); });
  $("resource-editor").addEventListener("close", () => { if ($("resource-editor").open) return; library.editor = null; $("resource-editor-fields").replaceChildren(); $("resource-edit-form").reset(); });
  $("resource-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const editor = library.editor;
    if (!editor || editor.busy) return;
    const fields = {};
    for (const field of resourceKinds[editor.kind].fields) {
      const value = $(`resource-field-${field.key}`).value.trim();
      fields[field.key] = field.array ? field.lines ? [...new Set(value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))] : parseList(value) : value;
      if (field.array && (fields[field.key].length > field.maxItems || fields[field.key].some((entry) => entry.length > field.maxItemLength))) {
        editorError(errorOf(`${field.label}最多 ${field.maxItems} 条，每条最多 ${field.maxItemLength} 字。`)); return;
      }
    }
    let body = fields;
    if (editor.uploading) {
      const file = $("resource-file").files?.[0];
      try { validateImageFile(file); } catch (error) { editorError(error); return; }
      body = new FormData(); body.append("file", file); body.append("description", fields.description); body.append("tags", JSON.stringify(fields.tags));
    }
    $("resource-editor-error").hidden = true; setEditorBusy(true);
    $("resource-editor-feedback").textContent = editor.uploading ? "正在上传图片并保存描述…" : "正在保存资源…";
    try {
      const path = editor.uploading ? "/api/resources/images/upload" : resourcePath(editor.kind, editor.item?.id);
      await api(path, { method: editor.item ? "PATCH" : "POST", body, timeout: editor.uploading ? 60000 : 20000 });
      if (library.editor !== editor || !state.authenticated) return;
      $("resource-editor").close();
      toast(editor.item ? "资源修改已保存，已即时生效。" : editor.uploading ? "图片已上传，已即时加入资源库。" : "资源已新增，即刻用于后续回复。");
      await refreshResourcesAfterMutation();
    } catch (error) {
      if (library.editor === editor) { editorError(error); $("resource-editor-feedback").textContent = "尚未确认保存成功，当前编辑已保留。"; }
    } finally { if (library.editor === editor) setEditorBusy(false); }
  });

  $("test-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("test-text").value.trim();
    if (!text) return;
    $("run-test").disabled = true; $("run-test").textContent = "模型正在回应…"; clearError();
    $("test-output").className = "test-output loading"; $("test-output").textContent = "正在调用真实模型，请稍候…";
    $("test-latency").textContent = "生成中"; $("test-latency").className = "pill warning";
    $("test-details").hidden = true;
    try {
      const timeoutSeconds = number(state.config?.provider_timeout) || 120;
      const result = await api("/api/test", { method: "POST", body: { text }, timeout: Math.max(140000, (timeoutSeconds + 20) * 1000) });
      if (!state.authenticated) return;
      const data = typeof result.data === "string" ? { text: result.data } : obj(result.data);
      $("test-output").className = "test-output";
      $("test-output").textContent = display(pick(data.text, data.response, data.content), "模型返回了空内容。");
      $("test-latency").textContent = number(data.latency_ms) !== null ? latency(data.latency_ms) : "已完成";
      $("test-latency").className = "pill good";
      const modelCalls = number(data.model_calls);
      $("test-details").textContent = `${modelCalls !== null ? `模型调用 ${modelCalls} 次 · ` : ""}${data.qq_sent === false ? "QQ 发送：未发送（服务端已确认）" : "QQ 发送状态未返回，请检查服务端隔离实现。"}`;
      $("test-details").hidden = false;
      if (data.qq_sent !== false) { $("test-latency").textContent = "隔离状态待确认"; $("test-latency").className = "pill warning"; }
    } catch (error) {
      $("test-output").className = "test-output error-text"; $("test-output").textContent = error.message;
      $("test-latency").textContent = "测试失败"; $("test-latency").className = "pill bad";
    } finally { $("run-test").disabled = !state.authenticated; $("run-test").textContent = "运行测试 ▷"; }
  });
  void restore();
})();
