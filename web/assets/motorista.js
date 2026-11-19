"use strict";

let session = sessionStorage.getItem("conradoDriverSession") || "";
let currentTrip = null;
let lastOccurrence = "";
let installPrompt = null;
let serverReachable = navigator.onLine;
let reloadingForUpdate = false;
let pendingEventIds = {};
try { pendingEventIds = JSON.parse(sessionStorage.getItem("conradoPendingEvents") || "{}"); } catch (_) {}

const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, character => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
})[character]);

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.userMessage = message;
  }
}

function eventIdFor(noteId) {
  if (!pendingEventIds[noteId]) {
    pendingEventIds[noteId] = crypto.randomUUID();
    sessionStorage.setItem("conradoPendingEvents", JSON.stringify(pendingEventIds));
  }
  return pendingEventIds[noteId];
}

function confirmEvent(noteId) {
  delete pendingEventIds[noteId];
  sessionStorage.setItem("conradoPendingEvents", JSON.stringify(pendingEventIds));
}

function setSession(value) {
  session = value || "";
  if (session) sessionStorage.setItem("conradoDriverSession", session);
  else sessionStorage.removeItem("conradoDriverSession");
}

function notify(text, error = false) {
  $("msg").innerHTML = `<p class="${error ? "error" : "ok"}"${error ? ' role="alert"' : ""}>${esc(text)}</p>`;
}

function setBusy(element, busy) {
  element.disabled = busy;
  element.setAttribute("aria-busy", String(busy));
}

function updateNetworkStatus() {
  const online = navigator.onLine && serverReachable;
  $("networkStatus").className = `chip ${online ? "online" : "offline"}`;
  $("networkStatus").textContent = online ? "Servidor disponível" : "Modo offline";
}

async function request(url, body, timeout = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Authorization": `Bearer ${session}`, "Content-Type": "application/json" },
      signal: controller.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    serverReachable = true;
    updateNetworkStatus();
    let value = {};
    try { value = await response.json(); } catch (_) {}
    if (!response.ok) throw new ApiError(value.erro || "O servidor recusou a operação.", response.status);
    return value;
  } catch (error) {
    if (error.name === "AbortError") {
      serverReachable = false;
      updateNetworkStatus();
      throw new ApiError("Sem confirmação do servidor. A operação será preservada.", 408);
    }
    if (error instanceof ApiError) throw error;
    serverReachable = false;
    updateNetworkStatus();
    throw new ApiError("Sem conexão com o servidor.", 0);
  } finally {
    clearTimeout(timer);
  }
}

async function blobBase64(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let value = "";
  for (let index = 0; index < bytes.length; index += 8192) {
    value += String.fromCharCode(...bytes.slice(index, index + 8192));
  }
  return btoa(value);
}

async function preparePhoto(file) {
  if (!file) return undefined;
  if (!["image/jpeg", "image/png"].includes(file.type)) throw new Error("Use uma fotografia JPEG ou PNG.");
  if (file.size <= 5000000) return blobBase64(file);
  let bitmap;
  try { bitmap = await createImageBitmap(file); } catch (_) { throw new Error("Não foi possível ler a fotografia."); }
  const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const compressed = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .84));
  if (!compressed || compressed.size > 5000000) throw new Error("Não foi possível reduzir a foto abaixo de 5 MB.");
  return blobBase64(compressed);
}

function itemText(item) {
  return `${item.descricao || "Produto"}${item.codigo ? ` • CÓD.: ${item.codigo}` : ""}`;
}

function itemLabel(item) {
  return `${itemText(item)}${item.quantidade ? ` • Qtd.: ${item.quantidade} ${item.unidade || ""}` : ""}`;
}

function statusLabel(status) {
  return {
    pending: "Pendente", delivered: "Entregue", occurrence: "Ocorrência",
    partial: "Parcial", refused: "Recusada"
  }[status] || status;
}

async function cacheCurrentTrip() {
  if (currentTrip) await ConradoOffline.saveTrip(currentTrip);
  else await ConradoOffline.clearTrip();
}

function renderTrip() {
  if (!currentTrip) {
    $("trip").innerHTML = '<section class="card empty">Nenhuma viagem liberada para você neste momento.</section>';
    $("trip").hidden = false;
    return;
  }
  const time = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const header = `<section class="card">
    <div class="card-header"><div><span class="eyebrow">Viagem atual</span><h2>${esc(currentTrip.label)}</h2>
    <p class="muted">Placa ${esc(currentTrip.plate)} • ${currentTrip.notes.length === 1 ? "1 nota" : `${currentTrip.notes.length} notas`}</p></div>
    <span class="tag">${navigator.onLine && serverReachable ? "Rota online" : "Rota offline"}</span></div>
    <div class="actions"><button data-action="close-trip" data-trip-id="${currentTrip.id}">Encerrar viagem</button>
    <button class="secondary" data-action="logout">Sair</button></div>
  </section>`;
  const notes = currentTrip.notes.map(note => {
    const options = (note.items || []).map(item =>
      `<option value="${esc(itemText(item))}">${esc(itemLabel(item))}</option>`
    ).join("");
    return `<section class="card note-card" data-note-card="${note.id}">
      <div class="note-summary"><div><strong>${esc(note.recipient)}</strong><p class="muted">${esc(note.address)}</p>
      <p>NF-e ${esc(note.number)} • ${esc(note.city)}</p></div><span class="tag">${esc(statusLabel(note.status))}</span></div>
      <label>Situação<select id="status-${note.id}">
        <option value="delivered">Entregue (exige canhoto)</option><option value="occurrence">Ocorrência geral</option>
        <option value="partial">Entrega parcial</option><option value="refused">Recusa</option>
      </select></label>
      <label>Chegada ao cliente<input type="time" id="arrival-${note.id}" value="${time}" required></label>
      <label>Produto afetado<select id="item-${note.id}"><option value="">Nenhum item selecionado</option>${options}</select></label>
      <label>Quantidade afetada<input id="quantity-${note.id}" maxlength="80" placeholder="Ex.: 01 (uma) caixa"></label>
      <label>Vendedor<input id="seller-${note.id}" maxlength="100" value="${esc(note.seller)}" placeholder="Não localizado no XML"></label>
      <label>Fotografia do canhoto ou da ocorrência<input type="file" id="photo-${note.id}" accept="image/jpeg,image/png" capture="environment"></label>
      <label>Motivo ou observação<textarea id="reason-${note.id}" maxlength="1000" placeholder="Ex.: Falta de produto"></textarea></label>
      <button id="save-${note.id}" data-action="save-note" data-note-id="${note.id}">Salvar situação</button>
    </section>`;
  }).join("");
  $("trip").innerHTML = header + notes;
  $("trip").hidden = false;
}

async function refreshTrip(allowCache = true) {
  try {
    const data = await request("/api/driver/trip");
    currentTrip = data.trip;
    await cacheCurrentTrip();
    renderTrip();
    return true;
  } catch (error) {
    if (!allowCache || !ConradoOffline.retryableError(error)) throw error;
    currentTrip = await ConradoOffline.loadTrip();
    if (!currentTrip) throw error;
    renderTrip();
    notify("Rota carregada do aparelho. As novas baixas serão sincronizadas quando a conexão voltar.");
    return false;
  }
}

function applyLocalStatus(noteId, status) {
  if (!currentTrip) return;
  const note = currentTrip.notes.find(item => Number(item.id) === Number(noteId));
  if (note) note.status = status;
}

function showOccurrence(text) {
  lastOccurrence = text;
  $("msg").innerHTML = `<section class="card"><span class="eyebrow">Texto preparado</span>
    <h2>Ocorrência pronta para compartilhar</h2><div class="share-text">${esc(text)}</div>
    <div class="actions"><button data-message-action="copy">Copiar texto</button>
    <button class="secondary" data-message-action="share">Compartilhar</button></div>
    <p class="hint">Revise antes de enviar. O aplicativo não envia automaticamente.</p></section>`;
}

async function queueDelivery(noteId, payload) {
  await ConradoOffline.queueOperation({
    id: payload.event_id,
    kind: "delivery",
    note_id: noteId,
    trip_id: currentTrip && currentTrip.id,
    label: "Baixa de entrega pendente",
    url: `/api/driver/notes/${noteId}/status`,
    body: payload
  });
  applyLocalStatus(noteId, payload.status);
  await cacheCurrentTrip();
  confirmEvent(noteId);
  renderTrip();
  await renderQueue();
}

async function saveDelivery(noteId) {
  const button = $(`save-${noteId}`);
  setBusy(button, true);
  try {
    const status = $(`status-${noteId}`).value;
    const photoBase64 = await preparePhoto($(`photo-${noteId}`).files[0]);
    if (status === "delivered" && !photoBase64) throw new Error("Fotografe o canhoto assinado.");
    const affectedItem = [$(`quantity-${noteId}`).value.trim(), $(`item-${noteId}`).value].filter(Boolean).join(" - ");
    const payload = {
      event_id: eventIdFor(noteId), status, reason: $(`reason-${noteId}`).value,
      photo_base64: photoBase64, arrival_time: $(`arrival-${noteId}`).value,
      affected_item: affectedItem, seller_name: $(`seller-${noteId}`).value
    };
    if (!session || !navigator.onLine) {
      await queueDelivery(noteId, payload);
      notify("Situação salva com segurança no aparelho e aguardando sincronização.");
      return;
    }
    try {
      const result = await request(`/api/driver/notes/${noteId}/status`, payload);
      confirmEvent(noteId);
      await refreshTrip();
      if (result.share_text) showOccurrence(result.share_text);
      else notify(result.duplicate ? "Situação já havia sido registrada." : "Situação confirmada pelo servidor.");
    } catch (error) {
      if (!ConradoOffline.retryableError(error)) throw error;
      await queueDelivery(noteId, payload);
      if (error.status === 401) {
        setSession("");
        $("loginBox").hidden = false;
        notify("A sessão expirou. A baixa foi preservada; entre novamente para sincronizar.", true);
      } else {
        notify("O servidor não confirmou a baixa. Ela foi preservada para sincronização.");
      }
    }
  } catch (error) {
    setBusy(button, false);
    notify(error.message, true);
  }
}

async function renderQueue() {
  try {
    const records = await ConradoOffline.listOperations();
    $("pendingCount").textContent = records.length === 0 ? "Nenhuma pendência" : records.length === 1 ? "1 pendência" : `${records.length} pendências`;
    $("queueBox").hidden = records.length === 0;
    $("queueList").innerHTML = records.map(record => `<div class="queue-item ${record.state === "blocked" ? "blocked" : ""}">
      <p><strong>${esc(record.label)}</strong></p><p class="hint">${esc(record.state === "blocked" ? "Requer revisão" : "Aguardando envio")} • ${esc(new Date(record.created_at).toLocaleString("pt-BR"))}</p>
      ${record.last_error ? `<p class="hint">${esc(record.last_error)}</p>` : ""}
      ${record.state === "blocked" ? `<div class="actions"><button class="ghost" data-queue-action="retry" data-id="${esc(record.id)}">Tentar novamente</button><button class="danger" data-queue-action="discard" data-id="${esc(record.id)}">Descartar</button></div>` : ""}
    </div>`).join("");
    return records;
  } catch (_) {
    $("pendingCount").textContent = "Armazenamento offline indisponível";
    return [];
  }
}

async function syncNow(options = {}) {
  if (!session) {
    if (!options.quiet) notify("Entre novamente quando houver internet para sincronizar.", true);
    return null;
  }
  if (!navigator.onLine) {
    if (!options.quiet) notify("Sem internet. As operações continuam protegidas no aparelho.");
    return null;
  }
  setBusy($("syncButton"), true);
  try {
    const outcome = await ConradoOffline.syncStored(operation => request(operation.url, operation.body));
    await renderQueue();
    if (outcome.authRequired) {
      setSession("");
      $("loginBox").hidden = false;
      notify("A sessão expirou. Nada foi perdido; entre novamente para continuar a sincronização.", true);
    } else if (outcome.synced.length) {
      const occurrence = [...outcome.synced].reverse().find(item => item.result && item.result.share_text);
      await refreshTrip(true);
      if (occurrence) showOccurrence(occurrence.result.share_text);
      else if (!options.quiet) notify(outcome.synced.length === 1 ? "1 operação sincronizada." : `${outcome.synced.length} operações sincronizadas.`);
    } else if (outcome.blocked.length && !options.quiet) {
      notify("Há uma operação que precisa de revisão antes de novo envio.", true);
    } else if (outcome.pending.length && !options.quiet) {
      notify("A sincronização ainda não foi confirmada. Tentaremos novamente.");
    } else if (!options.quiet) {
      notify("Tudo sincronizado.");
    }
    return outcome;
  } finally {
    setBusy($("syncButton"), false);
  }
}

async function closeTrip(tripId) {
  if (!confirm("Encerrar a viagem? Cada nota precisa ter baixa ou ocorrência.")) return;
  await syncNow({ quiet: true });
  const operation = {
    id: `close-trip-${tripId}`,
    kind: "close_trip",
    trip_id: tripId,
    label: "Encerramento de viagem pendente",
    url: `/api/driver/trips/${tripId}/close`,
    body: {}
  };
  if (!session || !navigator.onLine) {
    await ConradoOffline.queueOperation(operation);
    await renderQueue();
    notify("Pedido de encerramento salvo e aguardando sincronização.");
    return;
  }
  try {
    await request(operation.url, operation.body);
    currentTrip = null;
    await cacheCurrentTrip();
    renderTrip();
    notify("Viagem encerrada e confirmada pelo servidor.");
  } catch (error) {
    if (!ConradoOffline.retryableError(error)) {
      notify(error.message, true);
      return;
    }
    await ConradoOffline.queueOperation(operation);
    await renderQueue();
    notify("Encerramento preservado para sincronização.");
  }
}

async function logout() {
  const queued = await ConradoOffline.listOperations();
  if (queued.length && !confirm("Há operações pendentes. Elas permanecerão no aparelho e exigirão novo login para sincronizar. Sair mesmo assim?")) return;
  try { if (session && navigator.onLine) await request("/api/driver/logout", {}); } catch (_) {}
  setSession("");
  $("trip").hidden = true;
  $("loginBox").hidden = false;
  notify("Sessão encerrada.");
}

$("login").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  try {
    const result = await request("/api/driver/login", values);
    setSession(result.token);
    $("loginBox").hidden = true;
    await refreshTrip(false);
    await syncNow({ quiet: true });
    notify("Login realizado. A rota está disponível para uso online e offline.");
  } catch (error) {
    setSession("");
    notify(error.message, true);
  }
});

$("offlineResume").addEventListener("click", async () => {
  try {
    currentTrip = await ConradoOffline.loadTrip();
    if (!currentTrip) return;
    $("loginBox").hidden = true;
    renderTrip();
    notify("Rota aberta offline. Entre novamente quando a internet voltar para sincronizar.");
  } catch (_) {
    $("offlineResume").hidden = true;
    notify("A rota salva estava danificada e foi removida. Conecte-se para carregá-la novamente.", true);
  }
});

$("trip").addEventListener("click", event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.action === "save-note") saveDelivery(Number(button.dataset.noteId));
  if (button.dataset.action === "close-trip") closeTrip(Number(button.dataset.tripId));
  if (button.dataset.action === "logout") logout();
});

$("queueList").addEventListener("click", async event => {
  const button = event.target.closest("button[data-queue-action]");
  if (!button) return;
  if (button.dataset.queueAction === "retry") {
    await ConradoOffline.retryOperation(button.dataset.id);
    await renderQueue();
    await syncNow();
  } else if (button.dataset.queueAction === "discard" && confirm("Descartar definitivamente esta operação local?")) {
    await ConradoOffline.deleteOperation(button.dataset.id);
    await renderQueue();
    notify("Operação local descartada.");
  }
});

$("msg").addEventListener("click", async event => {
  const button = event.target.closest("button[data-message-action]");
  if (!button) return;
  if (button.dataset.messageAction === "copy") {
    try { await navigator.clipboard.writeText(lastOccurrence); notify("Texto copiado."); }
    catch (_) { notify("Não foi possível copiar automaticamente.", true); }
  } else if (button.dataset.messageAction === "share") {
    try {
      if (!navigator.share) throw new Error();
      await navigator.share({ title: "Ocorrência • DutraLog", text: lastOccurrence });
    } catch (error) {
      if (error.name !== "AbortError") notify("Use “Copiar texto” para compartilhar manualmente.", true);
    }
  }
});

$("syncButton").addEventListener("click", () => syncNow());
$("installHelpButton").addEventListener("click", () => {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const steps = standalone
    ? ["O aplicativo já está instalado neste aparelho.", "Use o ícone DutraLog na tela inicial para abrir a rota."]
    : ios
      ? ["Abra esta página no Safari.", "Toque em Compartilhar.", "Escolha “Adicionar à Tela de Início” e confirme."]
      : ["Use o botão “Instalar automaticamente” quando disponível.", "Se ele não aparecer, abra o menu do navegador.", "Escolha “Instalar app” ou “Adicionar à tela inicial”."];
  $("installSteps").innerHTML = steps.map(step => `<li>${esc(step)}</li>`).join("");
  $("installButton").hidden = !installPrompt || standalone;
  $("installDialog").showModal();
});
$("installButton").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("installButton").hidden = true;
  $("installDialog").close();
});

window.addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  installPrompt = event;
  if ($("installDialog").open) $("installButton").hidden = false;
});
window.addEventListener("appinstalled", () => { $("installButton").hidden = true; });
$("applyUpdate").addEventListener("click", async () => {
  if (!("serviceWorker" in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration("/");
  if (registration && registration.waiting) {
    setBusy($("applyUpdate"), true);
    registration.waiting.postMessage({ type: "SKIP_WAITING" });
  }
});
window.addEventListener("online", async () => {
  updateNetworkStatus();
  if (session) await syncNow({ quiet: true });
});
window.addEventListener("offline", updateNetworkStatus);

async function start() {
  updateNetworkStatus();
  await renderQueue();
  try {
    const cached = await ConradoOffline.loadTrip();
    $("offlineResume").hidden = !cached;
  } catch (error) {
    $("offlineResume").hidden = true;
    if (error && error.name === "OfflineDataError") {
      notify("A rota offline estava danificada e foi removida. Conecte-se para baixá-la novamente.", true);
    }
  }
  if ("serviceWorker" in navigator) {
    try {
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      const showUpdate = () => { if (navigator.serviceWorker.controller) $("updateBox").hidden = false; };
      if (registration.waiting) showUpdate();
      registration.addEventListener("updatefound", () => {
        const installing = registration.installing;
        if (!installing) return;
        installing.addEventListener("statechange", () => {
          if (installing.state === "installed") showUpdate();
        });
      });
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (reloadingForUpdate) return;
        reloadingForUpdate = true;
        location.reload();
      });
    } catch (_) {
      notify("Não foi possível preparar o uso offline neste navegador.", true);
    }
  }
  if (session) {
    $("loginBox").hidden = true;
    try {
      await refreshTrip(true);
      if (navigator.onLine) await syncNow({ quiet: true });
    } catch (error) {
      setSession("");
      $("loginBox").hidden = false;
      notify(error.message, true);
    }
  }
}

start();
