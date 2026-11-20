(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ConradoOffline = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DB_NAME = "conrado-entregas-driver";
  const DB_VERSION = 1;
  const OPERATIONS = "operations";
  const META = "meta";
  const KEY_ID = "offline-encryption-key";
  const TRIP_ID = "cached-trip";
  let keyPromise = null;

  class OfflineDataError extends Error {
    constructor(message = "Dados offline corrompidos.") {
      super(message);
      this.name = "OfflineDataError";
      this.status = 422;
      this.userMessage = message;
    }
  }

  function validateStoredRecord(record) {
    if (!record || typeof record !== "object") return false;
    if (!record.id || !record.kind || !record.created_at) return false;
    return Boolean(record.sealed && typeof record.sealed.iv === "string" &&
      typeof record.sealed.data === "string" && record.sealed.iv && record.sealed.data);
  }

  function sortAndDedupe(records) {
    const unique = new Map();
    for (const record of records || []) {
      if (!record || !record.id || unique.has(record.id)) continue;
      unique.set(record.id, record);
    }
    const priority = record => record.kind === "start_delivery" ? 0 : record.kind === "close_trip" ? 2 : 1;
    return [...unique.values()].sort((a, b) =>
      priority(a) - priority(b) ||
      String(a.created_at || "").localeCompare(String(b.created_at || ""))
    );
  }

  function retryableError(error) {
    const status = Number(error && error.status);
    return !status || status === 401 || status === 408 || status === 429 || status >= 500;
  }

  async function syncRecords(records, send) {
    const ordered = sortAndDedupe(records);
    const synced = [];
    const blocked = [];
    const pending = [];
    for (let index = 0; index < ordered.length; index += 1) {
      const record = ordered[index];
      try {
        const result = await send(record);
        synced.push({ record, result });
      } catch (error) {
        if (retryableError(error)) {
          pending.push(record, ...ordered.slice(index + 1));
          break;
        }
        blocked.push({ record, error });
      }
    }
    return { synced, blocked, pending };
  }

  function requireBrowserStorage() {
    if (typeof indexedDB === "undefined" || !globalThis.crypto || !crypto.subtle) {
      throw new Error("Armazenamento offline seguro indisponível neste navegador.");
    }
  }

  function openDatabase() {
    requireBrowserStorage();
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(OPERATIONS)) {
          const operations = db.createObjectStore(OPERATIONS, { keyPath: "id" });
          operations.createIndex("created_at", "created_at");
          operations.createIndex("state", "state");
        }
        if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: "id" });
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new Error("Não foi possível abrir a fila offline."));
    });
  }

  async function transact(storeName, mode, action) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(storeName, mode);
      const store = transaction.objectStore(storeName);
      let value;
      try {
        value = action(store);
      } catch (error) {
        db.close();
        reject(error);
        return;
      }
      transaction.oncomplete = () => {
        db.close();
        resolve(value);
      };
      transaction.onerror = () => {
        db.close();
        reject(new Error("Falha ao atualizar a fila offline."));
      };
      transaction.onabort = transaction.onerror;
    });
  }

  async function getRecord(storeName, id) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(storeName, "readonly");
      const request = transaction.objectStore(storeName).get(id);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(new Error("Falha ao consultar os dados offline."));
      transaction.oncomplete = () => db.close();
      transaction.onabort = () => db.close();
    });
  }

  async function allRecords(storeName) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(storeName, "readonly");
      const request = transaction.objectStore(storeName).getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(new Error("Falha ao consultar a fila offline."));
      transaction.oncomplete = () => db.close();
      transaction.onabort = () => db.close();
    });
  }

  function encryptionKey() {
    if (!keyPromise) {
      keyPromise = (async () => {
        const saved = await getRecord(META, KEY_ID);
        if (saved && saved.value) return saved.value;
        const value = await crypto.subtle.generateKey(
          { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]
        );
        await transact(META, "readwrite", store => store.put({ id: KEY_ID, value }));
        return value;
      })().catch(error => {
        keyPromise = null;
        throw error;
      });
    }
    return keyPromise;
  }

  function bytesToBase64(bytes) {
    let value = "";
    for (let index = 0; index < bytes.length; index += 8192) {
      value += String.fromCharCode(...bytes.slice(index, index + 8192));
    }
    return btoa(value);
  }

  function base64ToBytes(value) {
    const decoded = atob(value);
    const bytes = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index);
    return bytes;
  }

  async function seal(value) {
    const key = await encryptionKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plain = new TextEncoder().encode(JSON.stringify(value));
    const encrypted = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain);
    return { iv: bytesToBase64(iv), data: bytesToBase64(new Uint8Array(encrypted)) };
  }

  async function unseal(value) {
    if (!value || typeof value.iv !== "string" || typeof value.data !== "string") {
      throw new OfflineDataError();
    }
    try {
      const key = await encryptionKey();
      const plain = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: base64ToBytes(value.iv) }, key, base64ToBytes(value.data)
      );
      const result = JSON.parse(new TextDecoder().decode(plain));
      if (!result || typeof result !== "object") throw new Error("payload inválido");
      return result;
    } catch (error) {
      if (error instanceof OfflineDataError) throw error;
      throw new OfflineDataError();
    }
  }

  async function queueOperation(operation) {
    if (!operation || !operation.id || !operation.url || !operation.kind) {
      throw new Error("Operação offline inválida.");
    }
    const existing = await getRecord(OPERATIONS, operation.id);
    if (existing && validateStoredRecord(existing)) return existing;
    const record = {
      id: operation.id,
      kind: operation.kind,
      note_id: operation.note_id || null,
      trip_id: operation.trip_id || null,
      label: String(operation.label || "Operação pendente").slice(0, 160),
      created_at: operation.created_at || new Date().toISOString(),
      attempts: 0,
      state: "pending",
      last_error: "",
      sealed: await seal({ url: operation.url, body: operation.body || {} }),
    };
    await transact(OPERATIONS, "readwrite", store => store.put(record));
    return record;
  }

  async function listOperations() {
    return sortAndDedupe(await allRecords(OPERATIONS));
  }

  async function deleteOperation(id) {
    await transact(OPERATIONS, "readwrite", store => store.delete(id));
  }

  async function updateOperation(record) {
    await transact(OPERATIONS, "readwrite", store => store.put(record));
  }

  async function syncStored(send) {
    const records = await listOperations();
    const outcome = { synced: [], blocked: [], pending: [], authRequired: false };
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      if (record.state === "blocked") {
        outcome.blocked.push(record);
        continue;
      }
      try {
        if (!validateStoredRecord(record)) throw new OfflineDataError();
        const request = await unseal(record.sealed);
        const result = await send({ ...record, ...request });
        await deleteOperation(record.id);
        outcome.synced.push({ record, result });
      } catch (error) {
        if (Number(error && error.status) === 401) outcome.authRequired = true;
        record.attempts = Number(record.attempts || 0) + 1;
        record.last_error = String(error && error.userMessage || "Aguardando nova tentativa").slice(0, 180);
        if (retryableError(error)) {
          record.state = "pending";
          await updateOperation(record);
          outcome.pending.push(record, ...records.slice(index + 1));
          break;
        }
        record.state = "blocked";
        await updateOperation(record);
        outcome.blocked.push(record);
      }
    }
    return outcome;
  }

  async function retryOperation(id) {
    const record = await getRecord(OPERATIONS, id);
    if (!record) return false;
    record.state = "pending";
    record.last_error = "";
    await updateOperation(record);
    return true;
  }

  async function saveTrip(trip) {
    const sealed = await seal(trip);
    await transact(META, "readwrite", store => store.put({ id: TRIP_ID, sealed, updated_at: new Date().toISOString() }));
  }

  async function loadTrip() {
    const cached = await getRecord(META, TRIP_ID);
    if (!cached) return null;
    try {
      return cached.sealed ? await unseal(cached.sealed) : null;
    } catch (error) {
      if (error instanceof OfflineDataError) await clearTrip();
      throw error;
    }
  }

  async function clearTrip() {
    await transact(META, "readwrite", store => store.delete(TRIP_ID));
  }

  return {
    retryableError,
    validateStoredRecord,
    OfflineDataError,
    sortAndDedupe,
    syncRecords,
    queueOperation,
    listOperations,
    deleteOperation,
    retryOperation,
    syncStored,
    saveTrip,
    loadTrip,
    clearTrip,
  };
});
