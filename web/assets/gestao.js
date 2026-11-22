"use strict";

let token = sessionStorage.getItem("conradoAdminSession") || "";
let currentUser = null;
let driversCache = [];
let serverReachable = navigator.onLine;

const $ = id => document.getElementById(id);
const safe = value => String(value ?? "").replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
})[character]);

function feedback(message, error = false) {
  $("feedback").innerHTML = `<div class="${error ? "error" : "ok"}"${error ? ' role="alert"' : ""}>${safe(message)}</div>`;
}

function setBusy(element, busy) {
  element.disabled = busy;
  element.setAttribute("aria-busy", String(busy));
}

function setToken(value) {
  token = value || "";
  if (token) sessionStorage.setItem("conradoAdminSession", token);
  else sessionStorage.removeItem("conradoAdminSession");
}

async function api(path, data) {
  let response;
  try {
    response = await fetch(path, {
      method: data === undefined ? "GET" : "POST",
      headers: { "Authorization": `Bearer ${token}`, "Content-Type": "application/json" },
      ...(data === undefined ? {} : { body: JSON.stringify(data) })
    });
    serverReachable = true;
    updateNetworkStatus();
  } catch (_) {
    serverReachable = false;
    updateNetworkStatus();
    throw new Error("Sem conexão com o servidor.");
  }
  let value = {};
  try { value = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(value.erro || "Não foi possível concluir a operação.");
  return value;
}

function updateNetworkStatus() {
  const online = navigator.onLine && serverReachable;
  $("networkStatus").className = `chip ${online ? "online" : "offline"}`;
  $("networkStatus").textContent = online ? "Servidor disponível" : "Sem conexão";
}

function roleLabel(role) {
  return { admin: "Administrador", operator: "Operador", auditor: "Auditor" }[role] || role;
}

function statusLabel(status) {
  return {
    draft: "Rascunho", released: "Em rota", closed: "Encerrada",
    pending: "Pendente", delivered: "Entregue", occurrence: "Ocorrência geral",
    partial: "Falta parcial (registro anterior)", refused: "Recusa"
  }[status] || status;
}

function applyPermissions() {
  const canWrite = currentUser && ["admin", "operator"].includes(currentUser.role);
  const isAdmin = currentUser && currentUser.role === "admin";
  document.querySelectorAll(".requires-write").forEach(element => { element.hidden = !canWrite; });
  document.querySelectorAll(".admin-only").forEach(element => { element.hidden = !isAdmin; });
  document.querySelectorAll(".audit-only").forEach(element => {
    element.hidden = !currentUser || !["admin", "auditor"].includes(currentUser.role);
  });
  $("roleSummary").textContent = currentUser ? roleLabel(currentUser.role) : "";
  $("roleSummary").className = `tag role-${currentUser?.role || "auditor"}`;
  $("adminIdentity").textContent = currentUser ? `${currentUser.username} • ${roleLabel(currentUser.role)}` : "";
  $("adminIdentity").className = `tag role-${currentUser?.role || "auditor"}`;
}

function renderDiagnostics(data) {
  const items = [
    ["Versão", data.version], ["Banco", data.database === "ok" ? "Disponível" : "Indisponível"],
    ["Viagens em rota", data.active_trips], ["NF-e pendentes", data.pending_notes],
    ["Canhotos protegidos", data.evidence],
    ["Sessões vencidas", Number(data.expired_admin_sessions) + Number(data.expired_driver_sessions)],
    ["Evidências órfãs", data.orphan_evidence],
    ["Último evento", data.last_event_at ? new Date(data.last_event_at).toLocaleString("pt-BR") : "Nenhum"],
  ];
  $("diagnostics").innerHTML = items.map(([label, value]) =>
    `<div class="diagnostic-item"><strong>${safe(value)}</strong><span>${safe(label)}</span></div>`
  ).join("");
}

async function loadDiagnostics() {
  if (!currentUser || !["admin", "auditor"].includes(currentUser.role)) return;
  renderDiagnostics(await api("/api/admin/diagnostics"));
}

async function downloadAuthenticated(path, filename, button) {
  setBusy(button, true);
  try {
    const response = await fetch(path, { headers: { "Authorization": `Bearer ${token}` } });
    if (!response.ok) {
      let message = "Não foi possível gerar o relatório.";
      try { message = (await response.json()).erro || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    feedback("Relatório gerado. Confira a pasta de downloads do navegador.");
  } finally {
    setBusy(button, false);
  }
}

function renderSummary(summary) {
  const delivered = summary.notes.delivered;
  const total = Object.values(summary.notes).reduce((sum, value) => sum + value, 0);
  const cards = [
    ["Viagens em rota", summary.trips.released],
    ["Notas pendentes", summary.notes.pending],
    ["Notas entregues", delivered],
    ["Ocorrências e recusas", summary.notes.occurrence + summary.notes.partial + summary.notes.refused]
  ];
  const importedLabel = total === 1 ? "1 nota fiscal importada" : `${total} notas fiscais importadas`;
  $("summary").innerHTML = cards.map(([label, value]) =>
    `<div class="kpi"><strong>${value}</strong><span>${safe(label)}</span></div>`
  ).join("") + `<p class="hint">${importedLabel} • ${total ? Math.round(100 * delivered / total) : 0}% com baixa de entrega</p>`;
}

function renderProductivity(productivity) {
  if (!productivity.length) {
    $("productivity").innerHTML = '<div class="empty">Nenhum motorista cadastrado.</div>';
    return;
  }
  $("productivity").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Motorista</th><th>Placa</th><th>Viagens</th><th>Em rota</th><th>Concluídas</th><th>NF-e</th><th>Entregues</th><th>Pendentes</th><th>Ocorrências</th><th>Índice</th></tr></thead><tbody>${productivity.map(metric =>
    `<tr><td>${safe(metric.name)}</td><td>${safe(metric.plate)}</td><td>${metric.trips}</td><td>${metric.in_route}</td><td>${metric.closed_trips}</td><td>${metric.notes}</td><td>${metric.delivered}</td><td>${metric.pending}</td><td>${metric.issues}</td><td><strong>${Number(metric.delivery_rate).toLocaleString("pt-BR")}%</strong></td></tr>`
  ).join("")}</tbody></table></div>`;
}

function renderDrivers(drivers) {
  driversCache = drivers;
  $("vehicle").innerHTML = drivers.map(driver =>
    `<option value="${driver.vehicle_id}">${safe(driver.name)} • ${safe(driver.plate)}</option>`
  ).join("");
  if (!drivers.length) {
    $("drivers").innerHTML = '<div class="empty">Nenhum motorista cadastrado.</div>';
    return;
  }
  const passwordHeader = currentUser.role === "admin" ? "<th>Acesso</th>" : "";
  $("drivers").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Motorista</th><th>Placa</th><th>Tipo</th>${passwordHeader}</tr></thead><tbody>${drivers.map(driver =>
    `<tr><td>${safe(driver.name)}</td><td>${safe(driver.plate)}</td><td>${safe(driver.vehicle_type)}</td>${currentUser.role === "admin" ? `<td><button class="ghost" data-action="driver-password" data-driver-id="${driver.driver_id}">Definir senha</button></td>` : ""}</tr>`
  ).join("")}</tbody></table></div>`;
}

function renderTrips(trips) {
  if (!trips.length) {
    $("trips").innerHTML = '<div class="empty">Nenhuma viagem criada.</div>';
    return;
  }
  const canWrite = ["admin", "operator"].includes(currentUser.role);
  $("trips").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Viagem</th><th>Motorista</th><th>Placa</th><th>Notas</th><th>Status</th><th>Ações</th></tr></thead><tbody>${trips.map(trip => {
    const draftActions = canWrite && trip.status === "draft" ? `<div class="stack"><input type="file" id="file-${trip.id}" multiple accept=".xml,text/xml,application/xml"><div class="actions"><button data-action="import" data-trip-id="${trip.id}">Importar XML</button><button class="secondary" data-action="release" data-trip-id="${trip.id}">Liberar</button></div></div>` : "";
    return `<tr><td>${safe(trip.label)}</td><td>${safe(trip.driver)}</td><td>${safe(trip.plate)}</td><td>${trip.notes}</td><td><span class="tag">${safe(statusLabel(trip.status))}</span></td><td><div class="actions"><button class="ghost" data-action="detail" data-trip-id="${trip.id}">Ver notas</button>${draftActions}</div></td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function durationLabel(seconds) {
  if (seconds == null) return "";
  const minutes = Math.max(0, Math.round(Number(seconds) / 60));
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}min`;
}

function activeService(start) {
  if (!start) return "";
  const started = new Date(start.started_at);
  if (Number.isNaN(started.getTime())) return '<div class="warning compact-status">Descarregamento em andamento</div>';
  const seconds = Math.max(0, (Date.now() - started.getTime()) / 1000);
  return `<div class="warning compact-status"><strong>Em atendimento</strong><small>desde ${safe(started.toLocaleString("pt-BR"))} • ${safe(durationLabel(seconds))}</small></div>`;
}

function eventHistory(events, reversals = []) {
  const movements = [
    ...events.map(event => ({ kind: "event", at: event.completed_at || event.created_at, value: event })),
    ...reversals.map(reversal => ({ kind: "reversal", at: reversal.happened_at, value: reversal }))
  ].sort((left, right) => new Date(left.at) - new Date(right.at));
  if (!movements.length) return '<span class="hint">Sem movimentação</span>';
  const movementLabel = movements.length === 1 ? "1 movimentação" : `${movements.length} movimentações`;
  return `<details><summary>${movementLabel}</summary><ol class="history">${movements.map(movement => {
    const whenDate = new Date(movement.at);
    const when = Number.isNaN(whenDate.getTime()) ? movement.at : whenDate.toLocaleString("pt-BR");
    if (movement.kind === "reversal") {
      const payload = movement.value.payload || {};
      return `<li class="history-reversal"><strong>Baixa estornada</strong><small>${safe(movement.value.actor)} • ${safe(when)}</small><div>${safe(payload.reason || "Motivo não informado")}</div></li>`;
    }
    const event = movement.value;
    const mapLink = event.latitude != null && event.longitude != null
      ? ` <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${event.latitude},${event.longitude}`)}" target="_blank" rel="noopener noreferrer">Ver localização</a>` : "";
    const timing = event.started_at
      ? `<div><b>Atendimento:</b> ${safe(new Date(event.started_at).toLocaleString("pt-BR"))}${event.duration_seconds != null ? ` • ${safe(durationLabel(event.duration_seconds))}` : ""}${mapLink}</div>` : "";
    return `<li><strong>${safe(statusLabel(event.status))}</strong>${event.has_evidence ? ` <button class="ghost" data-action="evidence" data-event-id="${safe(event.id)}">Ver foto</button>` : ""}<small>${safe(event.driver)} • ${safe(when)}${event.arrival_time ? ` • chegada ${safe(event.arrival_time)}` : ""}</small>${timing}${event.reason ? `<div><b>Motivo:</b> ${safe(event.reason)}</div>` : ""}${event.affected_item ? `<div><b>Item:</b> ${safe(event.affected_item)}</div>` : ""}${event.observation ? `<div><b>Observação:</b> ${safe(event.observation)}</div>` : ""}${event.seller_name ? `<div><b>Vendedor:</b> ${safe(event.seller_name)}</div>` : ""}</li>`;
  }).join("")}</ol></details>`;
}

async function showTrip(id) {
  const trip = await api(`/api/trips/${id}/detail`);
  $("detailTitle").textContent = `${trip.label} • ${trip.plate} • ${trip.driver}`;
  $("detail").dataset.tripId = id;
  $("detail").innerHTML = trip.notes.length ? `<div class="table-wrap"><table><thead><tr><th>NF-e</th><th>Destinatário</th><th>Endereço</th><th>Volumes</th><th>Peso (kg)</th><th>Situação</th><th>Histórico</th></tr></thead><tbody>${trip.notes.map(note =>
    `<tr><td><strong>${safe(note.number)}</strong></td><td>${safe(note.recipient)}</td><td>${safe(note.address)}</td><td>${note.volumes}</td><td>${Number(note.gross_weight).toLocaleString("pt-BR")}</td><td><span class="tag">${safe(statusLabel(note.status))}</span>${activeService(note.active_start)}${currentUser.role === "admin" && note.status !== "pending" ? `<button class="ghost compact-action" data-action="reopen-note" data-note-id="${note.id}">Estornar baixa</button>` : ""}</td><td>${eventHistory(note.events, note.reversals)}</td></tr>`
  ).join("")}</tbody></table></div>` : '<div class="empty">Nenhuma nota fiscal importada.</div>';
  $("detailBox").hidden = false;
  $("detailBox").scrollIntoView({ behavior: "smooth" });
}

async function viewEvidence(eventId) {
  const access = await api(`/api/evidence/${encodeURIComponent(eventId)}/access`);
  const target = access.url || access.data_url;
  if (!target || (!target.startsWith("https://") && !target.startsWith("data:image/"))) throw new Error("Endereço de evidência inválido.");
  const box = document.createElement("div");
  box.className = "ok";
  box.textContent = access.expires_in ? "Acesso liberado por cinco minutos. " : "Canhoto local disponível. ";
  const link = document.createElement("a");
  link.href = target;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Abrir fotografia";
  box.appendChild(link);
  $("feedback").replaceChildren(box);
}

async function loadAdminUsers() {
  if (currentUser.role !== "admin") return;
  const users = await api("/api/admin/users");
  $("adminUsers").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Usuário</th><th>Perfil</th><th>Ativo</th><th>Ação</th></tr></thead><tbody>${users.map(user =>
    `<tr><td>${safe(user.username)}</td><td><select id="admin-role-${user.admin_id}"><option value="admin" ${user.role === "admin" ? "selected" : ""}>Administrador</option><option value="operator" ${user.role === "operator" ? "selected" : ""}>Operador</option><option value="auditor" ${user.role === "auditor" ? "selected" : ""}>Auditor</option></select></td><td><input id="admin-active-${user.admin_id}" type="checkbox" ${user.active ? "checked" : ""} aria-label="Conta ativa"></td><td><button class="ghost" data-action="admin-access" data-admin-id="${user.admin_id}">Salvar acesso</button></td></tr>`
  ).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Torre de controle
// ---------------------------------------------------------------------------
const TOWER_REFRESH_MS = 30000;
const DEFAULT_CENTER = [-20.14, -44.88];
const STATUS_TEXT = {
  in_route: "Em rota", in_service: "Em atendimento", no_signal: "Sem sinal",
  finished: "Finalizou", available: "Disponível"
};
const POINT_COLORS = { delivered: "#1f9d55", issue: "#d64545" };

const tower = {
  data: null, map: null, markers: new Map(), pointsLayer: null, trailLayer: null, accuracyLayer: null,
  selected: null, filter: "all", search: "", showPoints: true, fitted: false, timer: null, loading: false
};

function timeLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function ageLabel(seconds) {
  if (seconds == null) return "sem sinal hoje";
  if (seconds < 60) return "agora";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  return `há ${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}min`;
}

function rateLabel(value) {
  return value == null ? "—" : `${Number(value).toLocaleString("pt-BR")}%`;
}

function rateClass(value) {
  if (value == null) return "";
  if (value >= 95) return "rate-good";
  if (value >= 85) return "rate-mid";
  return "rate-bad";
}

function initials(name) {
  return String(name || "?").trim().split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase();
}

function setLiveStatus(state, text) {
  const element = $("liveStatus");
  element.className = `live-indicator${state === "paused" ? " is-paused" : ""}${state === "error" ? " is-error" : ""}`;
  element.textContent = text;
}

function renderTowerKpis(totals) {
  const tiles = [
    [totals.in_route, "Motoristas em rota", `${totals.drivers} cadastrados`, ""],
    [`${totals.with_signal}/${totals.in_route}`, "Com sinal ao vivo", totals.no_signal ? `${totals.no_signal} sem sinal` : "todos rastreados", totals.no_signal ? "tone-bad" : "tone-ok"],
    [totals.in_service, "Em atendimento agora", "descarregando no cliente", totals.in_service ? "tone-warn" : ""],
    [totals.delivered_today, "Entregas hoje", "NF-e com baixa de entrega", "tone-ok"],
    [totals.issues_today, "Ocorrências hoje", "recusas, faltas e ocorrências", totals.issues_today ? "tone-bad" : ""],
    [totals.pending_in_route, "Pendentes em rota", `de ${totals.notes_in_route} NF-e liberadas`, totals.pending_in_route ? "tone-warn" : "tone-ok"],
    [rateLabel(totals.success_rate), "Taxa de sucesso do dia", "entregas ÷ baixas", totals.success_rate == null ? "" : totals.success_rate >= 95 ? "tone-ok" : totals.success_rate >= 85 ? "tone-warn" : "tone-bad"],
    [totals.avg_service_seconds == null ? "—" : durationLabel(totals.avg_service_seconds), "Tempo médio no cliente", "início → conclusão", ""]
  ];
  $("towerKpis").innerHTML = tiles.map(([value, label, hint, tone]) =>
    `<div class="tower-kpi ${tone}"><strong>${safe(value)}</strong><span>${safe(label)}</span><small>${safe(hint)}</small></div>`
  ).join("");
}

function driverMatches(driver) {
  if (tower.filter !== "all" && driver.status !== tower.filter) return false;
  if (!tower.search) return true;
  const text = `${driver.name} ${driver.plate}`.toLocaleLowerCase("pt-BR");
  return text.includes(tower.search);
}

function signalText(driver) {
  if (!driver.position) return driver.trip ? "sem localização hoje" : "";
  const source = driver.position.source === "gps" ? "sinal" : "última entrega";
  return `${source} ${ageLabel(driver.signal_age_seconds)}`;
}

function renderFleet(drivers) {
  const visible = drivers.filter(driverMatches);
  $("fleetCount").textContent = `${visible.length} de ${drivers.length}`;
  if (!visible.length) {
    $("fleetList").innerHTML = '<div class="empty">Nenhum motorista nesta situação.</div>';
    return;
  }
  $("fleetList").innerHTML = visible.map(driver => {
    const trip = driver.trip;
    const done = trip ? trip.notes - trip.pending : 0;
    const service = driver.active_service
      ? `<span class="fleet-sub">Descarregando NF-e ${safe(driver.active_service.number)} • ${safe(driver.active_service.recipient)} • ${safe(durationLabel(driver.active_service.elapsed_seconds))}</span>` : "";
    const progress = trip
      ? `<div class="fleet-progress"><progress class="bar" max="${trip.notes || 1}" value="${done}"></progress><span>${done}/${trip.notes} NF-e</span></div>` : "";
    return `<button type="button" class="fleet-item status-${safe(driver.status)}${tower.selected === driver.driver_id ? " is-selected" : ""}" data-driver-id="${driver.driver_id}">
      <div class="fleet-head"><strong>${safe(driver.name)}</strong><span class="plate">${safe(driver.plate)}</span></div>
      <span class="fleet-sub"><span class="status-pill status-${safe(driver.status)}">${safe(STATUS_TEXT[driver.status] || driver.status)}</span> ${safe(signalText(driver))}${driver.position?.speed_kmh != null ? ` • ${Math.round(driver.position.speed_kmh)} km/h` : ""}</span>
      ${trip ? `<span class="fleet-sub">${safe(trip.label)}</span>` : ""}
      ${progress}
      <div class="fleet-metrics"><span><b>${driver.today.delivered}</b> entregues hoje</span><span class="m-issue"><b>${driver.today.issues}</b> ocorrências</span>${trip ? `<span><b>${trip.pending}</b> pendentes</span>` : ""}<span>sucesso <b>${safe(rateLabel(driver.today.success_rate))}</b></span></div>
      ${service}
    </button>`;
  }).join("");
}

function renderAlerts(alerts) {
  $("alertCount").textContent = alerts.length ? `${alerts.length}` : "0";
  $("alertCount").className = `tag${alerts.some(alert => alert.severity === "critical") ? " st-issue" : ""}`;
  if (!alerts.length) {
    $("towerAlerts").innerHTML = '<div class="empty">Nenhum alerta no momento. Operação dentro do esperado.</div>';
    return;
  }
  const icons = { no_signal: "📡", long_service: "⏱", refused: "⛔", partial: "📦", occurrence: "⚠" };
  $("towerAlerts").innerHTML = alerts.map(alert =>
    `<button type="button" class="alert-item ${safe(alert.severity)}" data-driver-id="${alert.driver_id}">
      <span class="alert-icon" aria-hidden="true">${icons[alert.kind] || "⚠"}</span>
      <span><strong>${safe(alert.title)}</strong><small>${safe(alert.detail)}</small></span>
      <span class="alert-time">${safe(timeLabel(alert.at))}</span>
    </button>`
  ).join("");
}

function renderTimeline(items) {
  if (!items.length) {
    $("towerTimeline").innerHTML = '<li class="empty">Nenhuma baixa registrada hoje.</li>';
    return;
  }
  $("towerTimeline").innerHTML = items.map(item => {
    const issue = item.status !== "delivered";
    return `<li><time>${safe(timeLabel(item.at))}</time><div class="tl-body${item.reverted ? " reverted" : ""}">
      <span class="tag ${issue ? "st-issue" : "st-delivered"}">${safe(statusLabel(item.status))}</span>
      <strong>NF-e ${safe(item.number)}</strong> • ${safe(item.recipient)}
      <small>${safe(item.driver)} • ${safe(item.plate)}${item.city ? ` • ${safe(item.city)}` : ""}${item.reverted ? " • baixa estornada" : ""}</small>
    </div></li>`;
  }).join("");
}

function renderDailyPerformance(drivers) {
  if (!drivers.length) {
    $("dailyPerformance").innerHTML = '<div class="empty">Nenhum motorista cadastrado.</div>';
    return;
  }
  const rows = [...drivers].sort((left, right) => right.today.done - left.today.done || left.name.localeCompare(right.name, "pt-BR"));
  $("dailyPerformance").innerHTML = `<div class="table-wrap"><table class="perf-table"><thead><tr>
    <th>Motorista</th><th>Placa</th><th>Situação</th><th>Viagem</th><th class="num">Progresso</th>
    <th class="num">Entregues</th><th class="num">Ocorrências</th><th class="num">Pendentes</th>
    <th class="num">Sucesso</th><th class="num">Tempo médio</th><th>1ª baixa</th><th>Última baixa</th><th>Último sinal</th>
  </tr></thead><tbody>${rows.map(driver => {
    const trip = driver.trip;
    return `<tr data-driver-id="${driver.driver_id}">
      <td><strong>${safe(driver.name)}</strong></td><td><span class="plate">${safe(driver.plate)}</span></td>
      <td><span class="status-pill status-${safe(driver.status)}">${safe(STATUS_TEXT[driver.status] || driver.status)}</span></td>
      <td>${trip ? safe(trip.label) : '<span class="hint">—</span>'}</td>
      <td class="num">${trip ? `${Number(trip.progress).toLocaleString("pt-BR")}%` : "—"}</td>
      <td class="num">${driver.today.delivered}</td><td class="num">${driver.today.issues}</td>
      <td class="num">${trip ? trip.pending : "—"}</td>
      <td class="num ${rateClass(driver.today.success_rate)}">${safe(rateLabel(driver.today.success_rate))}</td>
      <td class="num">${driver.today.avg_service_seconds == null ? "—" : safe(durationLabel(driver.today.avg_service_seconds))}</td>
      <td>${safe(timeLabel(driver.today.first_at))}</td><td>${safe(timeLabel(driver.today.last_at))}</td>
      <td>${driver.position ? safe(ageLabel(driver.signal_age_seconds)) : "—"}</td>
    </tr>`;
  }).join("")}</tbody></table></div>`;
}

function popupContent(driver) {
  const trip = driver.trip;
  const position = driver.position;
  const service = driver.active_service
    ? `<p><b>Em atendimento:</b> NF-e ${safe(driver.active_service.number)} • ${safe(driver.active_service.recipient)} (${safe(durationLabel(driver.active_service.elapsed_seconds))})</p>` : "";
  return `<div class="popup"><h3>${safe(driver.name)}</h3>
    <p><span class="plate">${safe(driver.plate)}</span> ${safe(driver.vehicle_type)} • <span class="status-pill status-${safe(driver.status)}">${safe(STATUS_TEXT[driver.status] || driver.status)}</span></p>
    ${trip ? `<p>${safe(trip.label)} • ${trip.notes - trip.pending}/${trip.notes} NF-e (${Number(trip.progress).toLocaleString("pt-BR")}%)</p>` : ""}
    <div class="popup-stats"><div><strong>${driver.today.delivered}</strong><span>entregues</span></div><div><strong>${driver.today.issues}</strong><span>ocorrências</span></div><div><strong>${safe(rateLabel(driver.today.success_rate))}</strong><span>sucesso</span></div></div>
    ${service}
    <p class="hint">${position.source === "gps" ? "GPS" : "Local da última entrega"} ${safe(ageLabel(driver.signal_age_seconds))}${position.accuracy_m != null ? ` • precisão ±${Math.round(position.accuracy_m)} m` : ""}${position.speed_kmh != null ? ` • ${Math.round(position.speed_kmh)} km/h` : ""}</p>
  </div>`;
}

function ensureMap() {
  if (tower.map) return true;
  if (!window.L) {
    $("mapNotice").hidden = false;
    $("mapNotice").textContent = "Não foi possível carregar o mapa (biblioteca de mapas indisponível nesta rede). Os indicadores e a lista de motoristas continuam atualizados.";
    return false;
  }
  tower.map = L.map("towerMap", { zoomControl: true, worldCopyJump: true }).setView(DEFAULT_CENTER, 8);
  tower.map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>');
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    referrerPolicy: "strict-origin-when-cross-origin",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
  }).addTo(tower.map);
  tower.pointsLayer = L.layerGroup().addTo(tower.map);
  tower.trailLayer = L.layerGroup().addTo(tower.map);
  tower.accuracyLayer = L.layerGroup().addTo(tower.map);
  return true;
}

function markerIcon(driver) {
  const classes = ["truck-marker", `status-${driver.status}`];
  if (driver.position.source !== "gps") classes.push("is-approximate");
  if (tower.selected === driver.driver_id) classes.push("is-selected");
  return L.divIcon({
    className: classes.join(" "),
    html: `<span class="truck-pin"><b>${safe(initials(driver.name))}</b></span><span class="truck-label">${safe(driver.plate)}</span>`,
    iconSize: [38, 38], iconAnchor: [19, 38], popupAnchor: [0, -36]
  });
}

function renderMap(data) {
  if (!ensureMap()) return;
  const seen = new Set();
  for (const driver of data.drivers) {
    if (!driver.position) continue;
    seen.add(driver.driver_id);
    const latLng = [driver.position.latitude, driver.position.longitude];
    let marker = tower.markers.get(driver.driver_id);
    if (!marker) {
      marker = L.marker(latLng, { icon: markerIcon(driver), keyboard: true, title: `${driver.name} • ${driver.plate}` });
      marker.on("click", () => selectDriver(driver.driver_id, { fly: false }));
      marker.addTo(tower.map);
      tower.markers.set(driver.driver_id, marker);
    } else {
      marker.setLatLng(latLng);
      marker.setIcon(markerIcon(driver));
    }
    marker.setZIndexOffset(driver.status === "in_service" || driver.status === "no_signal" ? 500 : 0);
    if (marker.getPopup()) marker.setPopupContent(popupContent(driver));
    else marker.bindPopup(popupContent(driver), { maxWidth: 300 });
  }
  for (const [driverId, marker] of tower.markers) {
    if (!seen.has(driverId)) { marker.remove(); tower.markers.delete(driverId); }
  }

  tower.pointsLayer.clearLayers();
  if (tower.showPoints) {
    for (const point of data.delivery_points) {
      const color = point.status === "delivered" ? POINT_COLORS.delivered : POINT_COLORS.issue;
      L.circleMarker([point.latitude, point.longitude], {
        radius: 6, color: "#ffffff", weight: 2, fillColor: color, fillOpacity: 0.95
      }).bindTooltip(`NF-e ${safe(point.number)} • ${safe(point.recipient)} • ${safe(statusLabel(point.status))} às ${safe(timeLabel(point.at))}`)
        .addTo(tower.pointsLayer);
    }
  }
  if (!tower.fitted && tower.markers.size && $("towerMap").offsetWidth) {
    fitFleet();
    tower.fitted = true;
  }
  if (tower.selected) drawAccuracy(data.drivers.find(item => item.driver_id === tower.selected));
}

function fitFleet() {
  if (!tower.map) return;
  const points = [...tower.markers.values()].map(marker => marker.getLatLng());
  if (tower.showPoints) tower.pointsLayer.eachLayer(layer => points.push(layer.getLatLng()));
  if (!points.length) { tower.map.setView(DEFAULT_CENTER, 8); return; }
  if (points.length === 1) { tower.map.setView(points[0], 14); return; }
  tower.map.fitBounds(L.latLngBounds(points), { padding: [48, 48], maxZoom: 14 });
}

function drawAccuracy(driver) {
  tower.accuracyLayer.clearLayers();
  if (!driver?.position || driver.position.accuracy_m == null || driver.position.source !== "gps") return;
  L.circle([driver.position.latitude, driver.position.longitude], {
    radius: driver.position.accuracy_m, color: "#28658f", weight: 1, fillOpacity: 0.08, interactive: false
  }).addTo(tower.accuracyLayer);
}

async function loadTrail(driverId) {
  if (!tower.map) return;
  tower.trailLayer.clearLayers();
  try {
    const trail = await api(`/api/admin/tower/drivers/${driverId}/trail`);
    if (tower.selected !== driverId || trail.points.length < 2) return;
    L.polyline(trail.points.map(point => [point.latitude, point.longitude]), {
      color: "#28658f", weight: 4, opacity: 0.75, dashArray: "6 8", interactive: false
    }).addTo(tower.trailLayer);
  } catch (_) {
    // O trajeto é complementar; a falha não interrompe a torre.
  }
}

function selectDriver(driverId, { fly = true } = {}) {
  tower.selected = driverId;
  const driver = tower.data?.drivers.find(item => item.driver_id === driverId);
  renderFleet(tower.data?.drivers || []);
  if (!driver) return;
  if (!driver.position) {
    feedback(`${driver.name} ainda não enviou localização hoje.`, true);
    return;
  }
  if (!ensureMap()) return;
  renderMap(tower.data);
  const marker = tower.markers.get(driverId);
  if (fly) tower.map.flyTo(marker.getLatLng(), Math.max(tower.map.getZoom(), 14), { duration: 0.8 });
  marker.openPopup();
  loadTrail(driverId);
  if (window.matchMedia("(max-width: 1080px)").matches) $("mapCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderTower(data) {
  tower.data = data;
  renderTowerKpis(data.totals);
  renderFleet(data.drivers);
  renderAlerts(data.alerts);
  renderTimeline(data.timeline);
  renderDailyPerformance(data.drivers);
  renderMap(data);
  $("lastUpdate").textContent = `Atualizado às ${new Date(data.generated_at).toLocaleTimeString("pt-BR")}`;
}

async function loadTower() {
  if (tower.loading) return;
  tower.loading = true;
  try {
    renderTower(await api("/api/admin/tower"));
    setLiveStatus($("autoRefresh").checked ? "live" : "paused", $("autoRefresh").checked ? "Ao vivo" : "Pausado");
  } catch (error) {
    setLiveStatus("error", "Falha na atualização");
    throw error;
  } finally {
    tower.loading = false;
  }
}

function scheduleTower() {
  clearInterval(tower.timer);
  tower.timer = null;
  if (!currentUser || !$("autoRefresh").checked) {
    setLiveStatus("paused", "Pausado");
    return;
  }
  setLiveStatus("live", "Ao vivo");
  tower.timer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    loadTower().catch(() => {});
  }, TOWER_REFRESH_MS);
}

function showTab(name) {
  document.querySelectorAll(".tower-tabs .tab").forEach(tab => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("is-active", active);
    if (active) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  });
  document.querySelectorAll(".panel").forEach(panel => panel.classList.toggle("is-active", panel.dataset.panel === name));
  try { sessionStorage.setItem("conradoTowerTab", name); } catch (_) {}
  if (name === "tower" && tower.map) setTimeout(() => {
    tower.map.invalidateSize();
    if (!tower.fitted && tower.markers.size) { fitFleet(); tower.fitted = true; }
  }, 0);
}

document.querySelector(".tower-tabs").addEventListener("click", event => {
  const tab = event.target.closest(".tab");
  if (tab) showTab(tab.dataset.tab);
});

$("fleetList").addEventListener("click", event => {
  const item = event.target.closest("[data-driver-id]");
  if (item) selectDriver(Number(item.dataset.driverId));
});
$("towerAlerts").addEventListener("click", event => {
  const item = event.target.closest("[data-driver-id]");
  if (item) selectDriver(Number(item.dataset.driverId));
});
$("dailyPerformance").addEventListener("click", event => {
  const row = event.target.closest("[data-driver-id]");
  if (row) { selectDriver(Number(row.dataset.driverId)); $("mapCard").scrollIntoView({ behavior: "smooth", block: "start" }); }
});
$("fleetFilters").addEventListener("click", event => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  tower.filter = button.dataset.filter;
  document.querySelectorAll("#fleetFilters .filter").forEach(item => item.classList.toggle("is-active", item === button));
  renderFleet(tower.data?.drivers || []);
});
$("fleetSearch").addEventListener("input", event => {
  tower.search = event.target.value.trim().toLocaleLowerCase("pt-BR");
  renderFleet(tower.data?.drivers || []);
});
$("fitFleet").addEventListener("click", () => { tower.selected = null; tower.trailLayer?.clearLayers(); tower.accuracyLayer?.clearLayers(); if (tower.data) renderTower(tower.data); fitFleet(); });
$("toggleDeliveries").addEventListener("click", event => {
  tower.showPoints = !tower.showPoints;
  event.currentTarget.setAttribute("aria-pressed", String(tower.showPoints));
  if (tower.data) renderMap(tower.data);
});
$("expandMap").addEventListener("click", event => {
  const expanded = $("mapCard").classList.toggle("is-expanded");
  event.currentTarget.setAttribute("aria-pressed", String(expanded));
  event.currentTarget.textContent = expanded ? "Reduzir mapa" : "Ampliar mapa";
  if (tower.map) setTimeout(() => tower.map.invalidateSize(), 50);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && $("mapCard").classList.contains("is-expanded")) $("expandMap").click();
});
$("autoRefresh").addEventListener("change", scheduleTower);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && currentUser && $("autoRefresh").checked) loadTower().catch(() => {});
});

async function refresh() {
  const [identity, drivers, trips, summary, productivity, towerData] = await Promise.all([
    api("/api/admin/me"), api("/api/drivers"), api("/api/trips"), api("/api/summary"), api("/api/productivity"),
    api("/api/admin/tower")
  ]);
  currentUser = identity;
  applyPermissions();
  renderTower(towerData);
  renderSummary(summary);
  renderProductivity(productivity);
  renderDrivers(drivers);
  renderTrips(trips);
  await loadAdminUsers();
  await loadDiagnostics();
}

async function enterWorkspace() {
  $("workspace").hidden = false;
  let tab = "tower";
  try { tab = sessionStorage.getItem("conradoTowerTab") || "tower"; } catch (_) {}
  showTab(document.querySelector(`.tower-tabs .tab[data-tab="${tab}"]`) ? tab : "tower");
  try {
    await refresh();
  } catch (error) {
    $("workspace").hidden = true;
    throw error;
  }
  if (tab === "admin" && currentUser.role !== "admin") showTab("tower");
  $("accessBox").hidden = true;
  $("sessionHeader").hidden = false;
  scheduleTower();
  if (tower.map) tower.map.invalidateSize();
}

$("adminLoginForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const result = await api("/api/admin/login", Object.fromEntries(new FormData(event.target)));
    setToken(result.token);
    await enterWorkspace();
    event.target.reset();
    feedback("Acesso autorizado.");
  } catch (error) {
    setToken("");
    feedback(error.message, true);
  }
});

$("legacyLogin").addEventListener("click", async () => {
  setToken($("legacyToken").value);
  try {
    await enterWorkspace();
    $("legacyToken").value = "";
    feedback("Acesso local autorizado.");
  } catch (error) {
    setToken("");
    feedback(error.message, true);
  }
});

$("adminLogout").addEventListener("click", async () => {
  try { await api("/api/admin/logout", {}); } catch (_) {}
  setToken("");
  currentUser = null;
  scheduleTower();
  $("workspace").hidden = true;
  $("accessBox").hidden = false;
  $("sessionHeader").hidden = true;
  feedback("Sessão administrativa encerrada.");
});

$("refreshButton").addEventListener("click", async () => {
  setBusy($("refreshButton"), true);
  try { await refresh(); feedback("Indicadores atualizados."); } catch (error) { feedback(error.message, true); }
  finally { setBusy($("refreshButton"), false); }
});

$("refreshDiagnostics").addEventListener("click", async () => {
  setBusy($("refreshDiagnostics"), true);
  try { await loadDiagnostics(); feedback("Diagnóstico atualizado sem expor dados pessoais."); }
  catch (error) { feedback(error.message, true); }
  finally { setBusy($("refreshDiagnostics"), false); }
});

$("exportOccurrences").addEventListener("click", event => {
  downloadAuthenticated("/api/admin/export/occurrences.csv", "ocorrencias-dutratracking.csv", event.currentTarget)
    .catch(error => feedback(error.message, true));
});
$("exportAudit").addEventListener("click", event => {
  downloadAuthenticated("/api/admin/export/audit.csv", "auditoria-dutratracking.csv", event.currentTarget)
    .catch(error => feedback(error.message, true));
});

for (const [formId, path] of [["driverForm", "/api/drivers"], ["tripForm", "/api/trips"]]) {
  $(formId).addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api(path, Object.fromEntries(new FormData(event.target)));
      event.target.reset();
      await refresh();
      feedback("Cadastro registrado.");
    } catch (error) { feedback(error.message, true); }
  });
}

$("adminUserForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await api("/api/admin/users", Object.fromEntries(new FormData(event.target)));
    event.target.reset();
    await loadAdminUsers();
    feedback("Conta administrativa preparada e sessões anteriores revogadas.");
  } catch (error) { feedback(error.message, true); }
});

$("drivers").addEventListener("click", event => {
  const button = event.target.closest('button[data-action="driver-password"]');
  if (!button) return;
  $("passwordDriverId").value = button.dataset.driverId;
  $("driverPassword").value = "";
  $("passwordDialog").showModal();
});

$("confirmDriverPassword").addEventListener("click", async event => {
  event.preventDefault();
  const password = $("driverPassword").value;
  if (!/^[0-9]{5}$/.test(password)) { feedback("O PIN deve conter exatamente 5 dígitos.", true); return; }
  try {
    await api(`/api/drivers/${$("passwordDriverId").value}/password`, { password });
    $("passwordDialog").close();
    feedback("PIN atualizado; a sessão anterior do motorista foi revogada.");
  } catch (error) { feedback(error.message, true); }
});

$("trips").addEventListener("click", async event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = Number(button.dataset.tripId);
  try {
    if (button.dataset.action === "detail") await showTrip(id);
    if (button.dataset.action === "release") {
      if (!confirm("Liberar esta viagem após conferir os XMLs?")) return;
      await api(`/api/trips/${id}/release`, {});
      await refresh();
      feedback("Viagem liberada.");
    }
    if (button.dataset.action === "import") {
      const files = [...$(`file-${id}`).files];
      if (!files.length) throw new Error("Selecione pelo menos um XML.");
      const arquivos = await Promise.all(files.map(async file => {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let value = "";
        for (let index = 0; index < bytes.length; index += 8192) value += String.fromCharCode(...bytes.slice(index, index + 8192));
        return { name: file.name, base64: btoa(value) };
      }));
      const result = await api(`/api/trips/${id}/import`, { arquivos });
      await refresh();
      feedback(result.imported === 1 ? "1 nota importada." : `${result.imported} notas importadas.`);
    }
  } catch (error) { feedback(error.message, true); }
});

$("detail").addEventListener("click", async event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  try {
    if (button.dataset.action === "evidence") await viewEvidence(button.dataset.eventId);
    if (button.dataset.action === "reopen-note") {
      const reason = prompt("Informe o motivo do estorno. O histórico anterior será preservado:");
      if (reason === null) return;
      setBusy(button, true);
      await api(`/api/notes/${button.dataset.noteId}/reopen`, { reason });
      await showTrip(Number($("detail").dataset.tripId));
      await refresh();
      feedback("Baixa estornada. A NF-e voltou para a lista do motorista e o histórico foi preservado.");
    }
  } catch (error) { feedback(error.message, true); }
});

$("adminUsers").addEventListener("click", async event => {
  const button = event.target.closest('button[data-action="admin-access"]');
  if (!button) return;
  const id = button.dataset.adminId;
  try {
    await api(`/api/admin/users/${id}/access`, {
      role: $(`admin-role-${id}`).value,
      active: $(`admin-active-${id}`).checked
    });
    await loadAdminUsers();
    feedback("Permissões atualizadas.");
  } catch (error) { feedback(error.message, true); }
});

$("closeDetail").addEventListener("click", () => { $("detailBox").hidden = true; });
window.addEventListener("online", updateNetworkStatus);
window.addEventListener("offline", updateNetworkStatus);

async function start() {
  updateNetworkStatus();
  if (!token) return;
  try { await enterWorkspace(); } catch (_) { setToken(""); }
}

start();
