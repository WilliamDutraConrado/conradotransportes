"use strict";

let session = sessionStorage.getItem("conradoDriverSession") || "";
let currentTrip = null;
let lastOccurrence = "";
let occurrenceTexts = {};
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

function notifyNote(noteId, text, state = "") {
  const target = $(`note-msg-${noteId}`);
  if (!target) return;
  if (!text) {
    target.innerHTML = "";
    return;
  }
  const className = state === "error" ? "error" : state === "success" ? "ok" : "note-progress";
  target.innerHTML = `<p class="${className}"${state === "error" ? ' role="alert"' : ""}>${esc(text)}</p>`;
}

function setBusy(element, busy) {
  element.disabled = busy;
  element.setAttribute("aria-busy", String(busy));
}

function setNoteBusy(noteId, busy) {
  const button = $(`save-${noteId}`);
  if (!button) return;
  if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
  setBusy(button, busy);
  button.textContent = busy ? "Salvando…" : button.dataset.idleLabel;
  const card = document.querySelector(`[data-note-card="${noteId}"]`);
  if (card) card.classList.toggle("is-saving", busy);
}

function nextPaint() {
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

function updateNetworkStatus() {
  const online = navigator.onLine && serverReachable;
  $("networkStatus").className = `chip ${online ? "online" : "offline"}`;
  $("networkStatus").textContent = online ? "Servidor disponível" : "Modo offline";
}

async function strengthenOfflineStorage() {
  try {
    if (!navigator.storage?.persisted || !navigator.storage?.persist) return false;
    if (await navigator.storage.persisted()) return true;
    return await navigator.storage.persist();
  } catch (_) {
    return false;
  }
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

async function decodePhoto(file) {
  if (typeof createImageBitmap === "function") {
    try {
      const source = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source, release: () => source.close() };
    } catch (_) {}
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.decoding = "async";
    image.src = url;
    await image.decode();
    return { source: image, release: () => URL.revokeObjectURL(url) };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

function canvasBlob(canvas, quality) {
  return new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
}

async function preparePhoto(file) {
  if (!file) return undefined;
  if (file.size > 20_000_000) throw new Error("A fotografia original ultrapassa 20 MB.");
  if (file.type && !file.type.startsWith("image/")) throw new Error("Selecione uma fotografia válida.");
  let decoded;
  try { decoded = await decodePhoto(file); } catch (_) {
    throw new Error("Não foi possível ler a foto. Use a câmera do aparelho ou selecione JPEG/PNG.");
  }
  const bitmap = decoded.source;
  const sourceWidth = bitmap.width || bitmap.naturalWidth;
  const sourceHeight = bitmap.height || bitmap.naturalHeight;
  const scale = Math.min(1, 2200 / Math.max(sourceWidth, sourceHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(sourceWidth * scale));
  canvas.height = Math.max(1, Math.round(sourceHeight * scale));
  try {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("canvas indisponível");
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  } catch (_) {
    throw new Error("Não foi possível processar a foto neste aparelho. Tente fotografar novamente.");
  } finally {
    decoded.release();
  }
  let compressed = null;
  for (const quality of [.9, .82, .74, .66]) {
    compressed = await canvasBlob(canvas, quality);
    if (compressed && compressed.size <= 1_800_000) break;
  }
  if (!compressed || compressed.size > 5_000_000) {
    throw new Error("Não foi possível preparar a foto. Tente fotografar novamente com boa iluminação.");
  }
  return blobBase64(compressed);
}

function itemText(item) {
  return `${item.descricao || "Produto"}${item.codigo ? ` • CÓD.: ${item.codigo}` : ""}`;
}

function itemLabel(item) {
  return `${itemText(item)}${item.quantidade ? ` • Qtd.: ${item.quantidade} ${item.unidade || ""}` : ""}`;
}

function statusLabel(status) {
  return DutraDelivery.statusLabel(status);
}

function invoiceSort(left, right) {
  return String(left.number || "").localeCompare(String(right.number || ""), "pt-BR", {
    numeric: true, sensitivity: "base"
  }) || Number(left.id) - Number(right.id);
}

function navigationUrls(note) {
  const destination = [note.address, note.city, "Brasil"].filter(Boolean).join(", ");
  const encoded = encodeURIComponent(destination);
  return {
    waze: `https://waze.com/ul?q=${encoded}&navigate=yes&utm_source=dutralog`,
    maps: `https://www.google.com/maps/dir/?api=1&destination=${encoded}&travelmode=driving&dir_action=navigate`
  };
}

function elapsedLabel(startedAt) {
  const started = new Date(startedAt);
  if (Number.isNaN(started.getTime())) return "Descarregamento iniciado";
  const minutes = Math.max(0, Math.floor((Date.now() - started.getTime()) / 60000));
  return minutes < 1 ? "Iniciado agora" : `${minutes} min em atendimento`;
}

async function cacheCurrentTrip() {
  if (currentTrip) await ConradoOffline.saveTrip(currentTrip);
  else await ConradoOffline.clearTrip();
}

function renderTrip() {
  updateLocationSharing();
  if (!currentTrip) {
    $("trip").innerHTML = '<section class="card empty">Nenhuma viagem liberada para você neste momento.</section>';
    $("trip").hidden = false;
    return;
  }
  currentTrip.notes = (currentTrip.notes || []).filter(note => note.status === "pending").sort(invoiceSort);
  const pending = currentTrip.notes.length;
  const total = Number(currentTrip.total_notes || pending);
  const header = `<section class="card">
    <div class="card-header"><div><span class="eyebrow">Viagem atual</span><h2>${esc(currentTrip.label)}</h2>
    <p class="muted">Placa ${esc(currentTrip.plate)} • ${pending === 1 ? "1 entrega pendente" : `${pending} entregas pendentes`} de ${total}</p></div>
    <span class="tag">${navigator.onLine && serverReachable ? "Rota online" : "Rota offline"}</span></div>
    <div class="actions"><button data-action="close-trip" data-trip-id="${currentTrip.id}">Encerrar viagem</button>
    <button class="secondary" data-action="logout">Sair</button></div>
  </section>`;
  if (!pending) {
    $("trip").innerHTML = header + `<section class="card route-complete">
      <span class="route-complete-icon" aria-hidden="true">✓</span>
      <div><span class="eyebrow">Rota limpa</span><h2>Todas as entregas receberam baixa</h2>
      <p class="muted">Confira se não há sincronizações pendentes e encerre a viagem.</p></div>
    </section>`;
    $("trip").hidden = false;
    return;
  }
  const notes = currentTrip.notes.map((note, index) => {
    const selectedStatus = "delivered";
    const statusOptions = [
      ["delivered", "Entregue"], ["refused", "Recusa"], ["occurrence", "Ocorrência geral"]
    ].map(([value, label]) => `<option value="${value}"${selectedStatus === value ? " selected" : ""}>${label}</option>`).join("");
    const rule = DutraDelivery.requirements(selectedStatus);
    const options = (note.items || []).map((item, itemIndex) =>
      `<option value="__index_${itemIndex}" data-quantity="${esc(item.quantidade || "")}" data-unit="${esc(item.unidade || "")}">${esc(itemLabel(item))}</option>`
    ).join("");
    const routes = navigationUrls(note);
    const start = note.start;
    const startedText = start ? new Date(start.started_at).toLocaleString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "";
    return `<details class="card note-card" data-note-card="${note.id}"${start || index === 0 ? " open" : ""}>
      <summary class="delivery-summary">
        <span class="delivery-order">${index + 1}</span>
        <span class="delivery-copy"><strong>${esc(note.recipient)}</strong><span>NF-e ${esc(note.number)} • ${esc(note.city)}</span></span>
        <span class="tag">Pendente</span>
      </summary>
      <div class="delivery-body">
        <p class="delivery-address">${esc(note.address)}</p>
        <div class="navigation-actions" aria-label="Abrir rota no GPS">
          <a class="button navigation-link" href="${routes.waze}" target="_blank" rel="noopener noreferrer" aria-label="Abrir no Waze" title="Waze"><img src="/assets/waze-official.png" alt="" aria-hidden="true"></a>
          <a class="button navigation-link" href="${routes.maps}" target="_blank" rel="noopener noreferrer" aria-label="Abrir no Google Maps" title="Google Maps"><img src="/assets/google-maps-official.png" alt="" aria-hidden="true"></a>
        </div>
        <div class="arrival-panel ${start ? "is-started" : ""}" id="arrival-panel-${note.id}">
          ${start ? `<div><strong>Descarregamento iniciado às ${esc(startedText)}</strong><span id="elapsed-${note.id}">${esc(elapsedLabel(start.started_at))}${start.latitude != null ? " • localização registrada" : ""}</span></div>` :
            `<div><strong>Chegou ao cliente?</strong><span>O toque registra o início. A localização é pontual e opcional.</span></div>
            <button type="button" data-action="start-note" data-note-id="${note.id}">Iniciar descarregamento</button>`}
        </div>
        <div class="delivery-form" id="delivery-form-${note.id}"${start ? "" : " hidden"}>
          <label>Situação<select id="status-${note.id}" aria-describedby="requirements-${note.id}">${statusOptions}</select></label>
          <div id="refusal-group-${note.id}" hidden>
            <label>Motivo da recusa<select id="refusal-${note.id}"><option value="">Selecione</option><option>Pedido Duplicado</option><option>Sem Pedido</option></select></label>
          </div>
          <div id="occurrence-group-${note.id}" hidden>
            <label>Tipo da ocorrência<select id="occurrence-type-${note.id}"><option value="general">Ocorrência geral</option><option value="partial_shortage">Falta parcial</option></select></label>
            <div id="general-group-${note.id}">
              <label>Motivo da ocorrência<textarea id="reason-${note.id}" maxlength="1000" placeholder="Descreva o que precisa de tratativa junto à LPA"></textarea></label>
            </div>
            <div id="partial-group-${note.id}" hidden>
              <label>Item em falta<select id="item-${note.id}"><option value="">Selecione o item da NF-e</option>${options}</select></label>
              <div class="quantity-grid">
                <label>Quantidade total na NF-e<input id="expected-${note.id}" readonly aria-readonly="true"></label>
                <label>Quantidade física recebida<input id="received-${note.id}" inputmode="decimal" placeholder="0" maxlength="40"></label>
              </div>
            </div>
          </div>
          <label>Foto do canhoto ou evidência<span class="required-marker" id="photo-required-${note.id}">${rule.photoRequired ? " • obrigatória" : " • opcional"}</span>
            <input type="file" id="photo-${note.id}" accept="image/*" capture="environment"${rule.photoRequired ? " required" : ""} aria-describedby="requirements-${note.id}"></label>
          <div id="receiver-group-${note.id}">
            <label>Recebedor<span class="required-marker"> • primeiro nome</span>
              <input id="receiver-${note.id}" maxlength="40" autocomplete="off" placeholder="Primeiro nome" required></label>
          </div>
          <label>Observações<span class="required-marker"> • opcional</span>
            <textarea id="observation-${note.id}" maxlength="940" placeholder=""></textarea></label>
          <p class="hint note-requirements" id="requirements-${note.id}">${esc(rule.hint)}</p>
          <button id="save-${note.id}" data-action="save-note" data-note-id="${note.id}">Concluir baixa</button>
        </div>
        <div class="note-feedback" id="note-msg-${note.id}" role="status" aria-live="polite" aria-atomic="true"></div>
      </div>
    </details>`;
  }).join("");
  $("trip").innerHTML = header + notes;
  $("trip").hidden = false;
  currentTrip.notes.forEach(note => updateNoteRequirements(note.id));
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

function currentNote(noteId) {
  return currentTrip?.notes.find(item => Number(item.id) === Number(noteId)) || null;
}

async function removeCompletedNote(noteId) {
  if (!currentTrip) return;
  currentTrip.notes = currentTrip.notes.filter(item => Number(item.id) !== Number(noteId));
  await cacheCurrentTrip();
  renderTrip();
}

function updateNoteRequirements(noteId) {
  const status = $(`status-${noteId}`);
  const photo = $(`photo-${noteId}`);
  if (!status || !photo) return;
  const rule = DutraDelivery.requirements(status.value);
  if (!rule) return;
  photo.required = rule.photoRequired;
  $(`photo-required-${noteId}`).textContent = rule.photoRequired ? " • obrigatória" : " • opcional";
  $(`requirements-${noteId}`).textContent = rule.hint;
  $(`refusal-group-${noteId}`).hidden = status.value !== "refused";
  $(`occurrence-group-${noteId}`).hidden = status.value !== "occurrence";
  const receiverGroup = $(`receiver-group-${noteId}`);
  const receiver = $(`receiver-${noteId}`);
  receiverGroup.hidden = status.value !== "delivered";
  receiver.required = status.value === "delivered";
  const occurrenceType = $(`occurrence-type-${noteId}`)?.value || "general";
  $(`general-group-${noteId}`).hidden = status.value !== "occurrence" || occurrenceType !== "general";
  $(`partial-group-${noteId}`).hidden = status.value !== "occurrence" || occurrenceType !== "partial_shortage";
}

function updateExpectedQuantity(noteId) {
  const select = $(`item-${noteId}`);
  const selected = select?.selectedOptions[0];
  if (!$(`expected-${noteId}`)) return;
  $(`expected-${noteId}`).value = selected?.dataset.quantity || "";
}

function showOccurrence(text, noteId = null) {
  lastOccurrence = text;
  if (noteId !== null && $(`note-msg-${noteId}`)) {
    occurrenceTexts[noteId] = text;
    $(`note-msg-${noteId}`).innerHTML = `<div class="note-result">
      <p class="ok">Ocorrência confirmada pelo servidor.</p>
      <div class="share-text">${esc(text)}</div>
      <div class="actions"><button type="button" data-message-action="copy" data-note-id="${noteId}">Copiar texto</button>
      <button type="button" class="secondary" data-message-action="share" data-note-id="${noteId}">Compartilhar</button></div>
      <p class="hint note-action-status">Revise antes de enviar. O aplicativo não envia automaticamente.</p>
    </div>`;
    return;
  }
  $("msg").innerHTML = `<section class="card"><span class="eyebrow">Texto preparado</span>
    <h2>Ocorrência pronta para compartilhar</h2><div class="share-text">${esc(text)}</div>
    <div class="actions"><button data-message-action="copy">Copiar texto</button>
    <button class="secondary" data-message-action="share">Compartilhar</button></div>
    <p class="hint">Revise antes de enviar. O aplicativo não envia automaticamente.</p></section>`;
}

function currentPosition() {
  if (!navigator.geolocation) return Promise.resolve(null);
  return new Promise(resolve => navigator.geolocation.getCurrentPosition(
    position => resolve({
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy_m: position.coords.accuracy
    }),
    () => resolve(null),
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
  ));
}

// Torre de controle: a posição é compartilhada somente com viagem liberada e
// sessão ativa. Envio no máximo a cada minuto (ou antes, se o veículo andou
// mais de 150 m), sem fila offline: posição antiga não tem valor operacional.
const LOCATION_INTERVAL_MS = 60000;
const LOCATION_MIN_INTERVAL_MS = 20000;
const LOCATION_MIN_DISTANCE_M = 150;
let locationWatch = null;
let lastFix = null;
let lastSent = null;
let sendingLocation = false;
let locationTimer = null;

function setGpsStatus(text, state) {
  const element = $("gpsStatus");
  if (!element) return;
  element.hidden = !text;
  element.textContent = text || "";
  element.className = `tag gps-${state || "idle"}`;
}

function distanceMeters(left, right) {
  const radius = 6371000;
  const rad = value => value * Math.PI / 180;
  const dLat = rad(right.latitude - left.latitude);
  const dLng = rad(right.longitude - left.longitude);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(left.latitude)) * Math.cos(rad(right.latitude)) * Math.sin(dLng / 2) ** 2;
  return 2 * radius * Math.asin(Math.sqrt(a));
}

async function sendLocation(force = false) {
  if (!lastFix || sendingLocation || !session || !currentTrip || !navigator.onLine) return;
  const elapsed = lastSent ? Date.now() - lastSent.at : Infinity;
  const moved = lastSent ? distanceMeters(lastSent, lastFix) : Infinity;
  if (!force && elapsed < LOCATION_INTERVAL_MS && !(elapsed >= LOCATION_MIN_INTERVAL_MS && moved >= LOCATION_MIN_DISTANCE_M)) return;
  sendingLocation = true;
  try {
    const result = await request("/api/driver/position", lastFix, 15000);
    if (result.recorded || result.reason === "throttled") {
      lastSent = { ...lastFix, at: Date.now() };
      setGpsStatus("Localização compartilhada", "on");
    } else if (result.reason === "no_active_trip") {
      setGpsStatus("", "idle");
    }
  } catch (_) {
    // Falha de rede: a próxima leitura do GPS tenta de novo.
  } finally {
    sendingLocation = false;
  }
}

function startLocationSharing() {
  if (locationWatch !== null) return;
  if (!navigator.geolocation) { setGpsStatus("GPS indisponível", "off"); return; }
  setGpsStatus("Ativando localização", "idle");
  locationWatch = navigator.geolocation.watchPosition(position => {
    lastFix = {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy_m: Math.min(10000, position.coords.accuracy),
      speed: Number.isFinite(position.coords.speed) && position.coords.speed >= 0 ? position.coords.speed : null,
      heading: Number.isFinite(position.coords.heading) ? position.coords.heading : null,
      recorded_at: new Date(position.timestamp || Date.now()).toISOString()
    };
    sendLocation();
  }, error => {
    if (error.code === error.PERMISSION_DENIED) {
      setGpsStatus("Localização bloqueada", "off");
      stopLocationSharing(false);
    }
  }, { enableHighAccuracy: true, maximumAge: 30000, timeout: 60000 });
  locationTimer = setInterval(() => sendLocation(), LOCATION_INTERVAL_MS);
}

function stopLocationSharing(clearStatus = true) {
  if (locationWatch !== null && navigator.geolocation) navigator.geolocation.clearWatch(locationWatch);
  locationWatch = null;
  if (locationTimer) clearInterval(locationTimer);
  locationTimer = null;
  lastFix = null;
  if (clearStatus) setGpsStatus("", "idle");
}

function updateLocationSharing() {
  if (session && currentTrip) startLocationSharing();
  else stopLocationSharing();
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") sendLocation(true);
});

async function queueStart(noteId, note, body) {
  await ConradoOffline.queueOperation({
    id: body.start_id,
    kind: "start_delivery",
    note_id: noteId,
    trip_id: currentTrip?.id,
    label: `Início do descarregamento • NF-e ${note.number}`,
    url: `/api/driver/notes/${noteId}/start`,
    body
  });
  note.start = {
    id: body.start_id, started_at: body.started_at,
    latitude: body.latitude ?? null, longitude: body.longitude ?? null,
    accuracy_m: body.accuracy_m ?? null
  };
  await cacheCurrentTrip();
  await renderQueue();
}

async function startDelivery(noteId, button) {
  const note = currentNote(noteId);
  if (!note || note.start) return;
  const startedAt = new Date().toISOString();
  setBusy(button, true);
  button.textContent = "Registrando…";
  notifyNote(noteId, "Registrando o início e tentando obter a localização pontual…");
  try {
    const location = await currentPosition();
    const body = { start_id: crypto.randomUUID(), started_at: startedAt, ...(location || {}) };
    if (!session || !navigator.onLine) {
      await queueStart(noteId, note, body);
      renderTrip();
      notifyNote(noteId, location
        ? "Início e localização salvos no aparelho para sincronização."
        : "Início salvo no aparelho sem localização; isso não impede a entrega.", "success");
      return;
    }
    try {
      const result = await request(`/api/driver/notes/${noteId}/start`, body, 30000);
      note.start = {
        id: result.start_id, started_at: result.started_at,
        latitude: location?.latitude ?? null, longitude: location?.longitude ?? null,
        accuracy_m: location?.accuracy_m ?? null
      };
      await cacheCurrentTrip();
      renderTrip();
      notifyNote(noteId, result.location_recorded
        ? "Descarregamento iniciado com localização registrada."
        : "Descarregamento iniciado sem localização; o cronômetro está ativo.", "success");
    } catch (error) {
      if (!ConradoOffline.retryableError(error)) throw error;
      await queueStart(noteId, note, body);
      if (error.status === 401) {
        setSession("");
        $("loginBox").hidden = false;
      }
      renderTrip();
      notifyNote(noteId, "Início preservado no aparelho para sincronização.", "success");
    }
  } catch (error) {
    notifyNote(noteId, error.message, "error");
    setBusy(button, false);
    button.textContent = "Iniciar descarregamento";
  }
}

async function queueDelivery(noteId, payload) {
  const note = currentNote(noteId);
  await ConradoOffline.queueOperation({
    id: payload.event_id,
    kind: "delivery",
    note_id: noteId,
    trip_id: currentTrip && currentTrip.id,
    label: `Baixa pendente • NF-e ${note?.number || noteId}`,
    url: `/api/driver/notes/${noteId}/status`,
    body: payload
  });
  confirmEvent(noteId);
  await renderQueue();
  await removeCompletedNote(noteId);
}

async function saveDelivery(noteId) {
  setNoteBusy(noteId, true);
  notifyNote(noteId, "Preparando e salvando a situação…");
  await nextPaint();
  try {
    const note = currentNote(noteId);
    if (!note) throw new Error("Esta entrega não está mais pendente.");
    const file = $(`photo-${noteId}`).files[0];
    const values = DutraDelivery.validateInput({
      status: $(`status-${noteId}`).value,
      started: Boolean(note.start),
      refusalReason: $(`refusal-${noteId}`).value,
      occurrenceType: $(`occurrence-type-${noteId}`).value,
      reason: $(`reason-${noteId}`).value,
      itemCode: $(`item-${noteId}`).value,
      expectedQuantity: $(`expected-${noteId}`).value,
      receivedQuantity: $(`received-${noteId}`).value,
      observation: $(`observation-${noteId}`).value,
      receiverName: $(`receiver-${noteId}`).value,
      hasPhoto: Boolean(file)
    });
    notifyNote(noteId, file ? "Otimizando a foto e enviando a baixa…" : "Enviando a baixa…");
    const photoBase64 = await preparePhoto(file);
    const payload = {
      event_id: eventIdFor(noteId), status: values.status, reason: values.reason,
      photo_base64: photoBase64, start_id: note.start.id,
      completed_at: new Date().toISOString(), occurrence_type: values.occurrenceType,
      affected_item: values.itemCode, expected_quantity: values.expectedQuantity,
      received_quantity: values.receivedQuantity, observation: values.observation,
      receiver_name: values.receiverName,
      seller_name: note.seller || ""
    };
    if (!session || !navigator.onLine) {
      await queueDelivery(noteId, payload);
      notify("Baixa salva com segurança no aparelho. A entrega saiu da lista e aguarda sincronização.");
      return;
    }
    try {
      const result = await request(`/api/driver/notes/${noteId}/status`, payload, 90000);
      confirmEvent(noteId);
      await removeCompletedNote(noteId);
      if (result.share_text) showOccurrence(result.share_text);
      else notify(result.duplicate ? "A baixa já havia sido registrada e foi confirmada." : "Entrega confirmada e canhoto preservado.");
    } catch (error) {
      if (!ConradoOffline.retryableError(error)) throw error;
      await queueDelivery(noteId, payload);
      if (error.status === 401) {
        setSession("");
        $("loginBox").hidden = false;
        notify("A sessão expirou. A baixa foi preservada; entre novamente para sincronizar.", true);
      } else {
        notify("Sem confirmação do servidor. A baixa foi preservada no aparelho para sincronização.");
      }
    }
  } catch (error) {
    notifyNote(noteId, error.message, "error");
  } finally {
    setNoteBusy(noteId, false);
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
    } else if (outcome.blocked.length) {
      // Se o servidor recusou a baixa, a NF-e continua pendente e precisa
      // reaparecer para correção em vez de ficar oculta apenas no aparelho.
      await refreshTrip(true);
      if (!options.quiet) notify("Há uma baixa que precisa de revisão. A entrega voltou para a rota.", true);
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
  stopLocationSharing();
  $("trip").hidden = true;
  $("loginBox").hidden = false;
  notify("Sessão encerrada.");
}

$("login").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  const button = event.target.querySelector("button");
  const label = button.textContent;
  setBusy(button, true);
  button.textContent = "Conectando…";
  notify("Conectando ao servidor…");
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
  } finally {
    setBusy(button, false);
    button.textContent = label;
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

$("trip").addEventListener("click", async event => {
  const messageButton = event.target.closest("button[data-message-action]");
  if (messageButton) {
    handleMessageAction(messageButton);
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  if (button.dataset.action === "start-note") await startDelivery(Number(button.dataset.noteId), button);
  if (button.dataset.action === "save-note") await saveDelivery(Number(button.dataset.noteId));
  if (button.dataset.action === "close-trip") await closeTrip(Number(button.dataset.tripId));
  if (button.dataset.action === "logout") await logout();
});

$("trip").addEventListener("change", event => {
  const target = event.target;
  let noteId = null;
  if (target.matches('select[id^="status-"]')) noteId = Number(target.id.replace("status-", ""));
  if (target.matches('select[id^="occurrence-type-"]')) noteId = Number(target.id.replace("occurrence-type-", ""));
  if (target.matches('select[id^="item-"]')) {
    noteId = Number(target.id.replace("item-", ""));
    updateExpectedQuantity(noteId);
  }
  if (noteId === null) return;
  updateNoteRequirements(noteId);
  notifyNote(noteId, "");
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
    if (session && navigator.onLine) await refreshTrip(true);
    notify("Operação local descartada.");
  }
});

async function handleMessageAction(button) {
  const noteId = button.dataset.noteId || "";
  const text = noteId ? occurrenceTexts[noteId] : lastOccurrence;
  const actionStatus = noteId ? $(`note-msg-${noteId}`).querySelector(".note-action-status") : null;
  const report = (message, error = false) => {
    if (actionStatus) {
      actionStatus.textContent = message;
      actionStatus.classList.toggle("action-error", error);
    } else {
      notify(message, error);
    }
  };
  if (button.dataset.messageAction === "copy") {
    try { await navigator.clipboard.writeText(text); report("Texto copiado."); }
    catch (_) { report("Não foi possível copiar automaticamente.", true); }
  } else if (button.dataset.messageAction === "share") {
    try {
      if (!navigator.share) throw new Error();
      await navigator.share({ title: "Ocorrência • DutraLog", text });
    } catch (error) {
      if (error.name !== "AbortError") report("Use “Copiar texto” para compartilhar manualmente.", true);
    }
  }
}

$("msg").addEventListener("click", async event => {
  const button = event.target.closest("button[data-message-action]");
  if (!button) return;
  await handleMessageAction(button);
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
  await strengthenOfflineStorage();
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

setInterval(() => {
  for (const note of currentTrip?.notes || []) {
    if (!note.start) continue;
    const target = $(`elapsed-${note.id}`);
    if (target) target.textContent = `${elapsedLabel(note.start.started_at)}${note.start.latitude != null ? " • localização registrada" : ""}`;
  }
}, 30000);
