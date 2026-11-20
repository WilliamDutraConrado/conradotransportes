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

async function refresh() {
  const [identity, drivers, trips, summary, productivity] = await Promise.all([
    api("/api/admin/me"), api("/api/drivers"), api("/api/trips"), api("/api/summary"), api("/api/productivity")
  ]);
  currentUser = identity;
  applyPermissions();
  renderSummary(summary);
  renderProductivity(productivity);
  renderDrivers(drivers);
  renderTrips(trips);
  await loadAdminUsers();
  await loadDiagnostics();
}

async function enterWorkspace() {
  await refresh();
  $("workspace").hidden = false;
  $("accessBox").hidden = true;
  $("sessionHeader").hidden = false;
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
  if (password.length < 12) { feedback("A senha precisa ter pelo menos 12 caracteres.", true); return; }
  try {
    await api(`/api/drivers/${$("passwordDriverId").value}/password`, { password });
    $("passwordDialog").close();
    feedback("Senha atualizada; a sessão anterior do motorista foi revogada.");
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
