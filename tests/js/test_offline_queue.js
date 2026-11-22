"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { sortAndDedupe, syncRecords, validateStoredRecord, OfflineDataError } =
  require("../../web/assets/offline-queue.js");
const { requirements, statusLabel, validateInput } =
  require("../../web/assets/delivery-form.js");

test("deduplica pelo mesmo event_id e preserva ordem", () => {
  const records = sortAndDedupe([
    { id: "event-2", created_at: "2026-11-18T10:02:00Z" },
    { id: "event-1", created_at: "2026-11-18T10:01:00Z" },
    { id: "event-1", created_at: "2026-11-18T10:03:00Z" }
  ]);
  assert.deepEqual(records.map(record => record.id), ["event-1", "event-2"]);
});

test("início precede a baixa da mesma NF mesmo se o relógio do aparelho retroceder", () => {
  const records = sortAndDedupe([
    { id: "baixa-1", kind: "delivery", note_id: 7, created_at: "2026-11-20T10:00:00Z" },
    { id: "inicio-1", kind: "start_delivery", note_id: 7, created_at: "2026-11-20T10:05:00Z" },
    { id: "fechar-1", kind: "close_trip", created_at: "2026-11-20T09:00:00Z" }
  ]);

  assert.deepEqual(records.map(record => record.id), ["inicio-1", "baixa-1", "fechar-1"]);
});

test("falha de rede interrompe lote sem perder operações posteriores", async () => {
  const records = [{ id: "event-1" }, { id: "event-2" }];
  const error = new Error("offline");
  error.status = 0;
  const outcome = await syncRecords(records, async () => { throw error; });
  assert.equal(outcome.synced.length, 0);
  assert.deepEqual(outcome.pending.map(record => record.id), ["event-1", "event-2"]);
});

test("reenvio com resposta idempotente confirma uma única operação", async () => {
  const records = [{ id: "event-1", body: { event_id: "event-1" } }];
  const outcome = await syncRecords(records, async record => ({
    event_id: record.body.event_id,
    duplicate: true,
    status: "delivered"
  }));
  assert.equal(outcome.synced.length, 1);
  assert.equal(outcome.synced[0].result.duplicate, true);
  assert.equal(outcome.pending.length, 0);
});

test("erro de validação bloqueia somente a operação inválida", async () => {
  const records = [{ id: "bad" }, { id: "good" }];
  const outcome = await syncRecords(records, async record => {
    if (record.id === "bad") {
      const error = new Error("inválida");
      error.status = 400;
      throw error;
    }
    return { ok: true };
  });
  assert.deepEqual(outcome.blocked.map(item => item.record.id), ["bad"]);
  assert.deepEqual(outcome.synced.map(item => item.record.id), ["good"]);
});

test("registro offline truncado é reconhecido antes da sincronização", () => {
  assert.equal(validateStoredRecord({ id: "x", kind: "delivery", created_at: "2026-11-18" }), false);
  assert.equal(validateStoredRecord({
    id: "x", kind: "delivery", created_at: "2026-11-18", sealed: { iv: "abc", data: "def" }
  }), true);
});

test("corrupção local bloqueia apenas a operação afetada e permite a seguinte", async () => {
  const records = [{ id: "corrupt" }, { id: "valid" }];
  const outcome = await syncRecords(records, async record => {
    if (record.id === "corrupt") throw new OfflineDataError("Registro local danificado.");
    return { ok: true };
  });
  assert.deepEqual(outcome.blocked.map(item => item.record.id), ["corrupt"]);
  assert.deepEqual(outcome.synced.map(item => item.record.id), ["valid"]);
  assert.equal(outcome.pending.length, 0);
});

test("sessão expirada preserva toda a fila para novo login", async () => {
  const error = new Error("Sessão expirada");
  error.status = 401;
  const outcome = await syncRecords([{ id: "one" }, { id: "two" }], async () => { throw error; });
  assert.deepEqual(outcome.pending.map(item => item.id), ["one", "two"]);
  assert.equal(outcome.synced.length, 0);
});

test("entrega concluída exige canhoto antes do envio", () => {
  assert.throws(() => validateInput({
    status: "delivered", started: true, hasPhoto: false, receiverName: "João"
  }), /Fotografe o canhoto/);
  assert.deepEqual(validateInput({
    status: "delivered", started: true, hasPhoto: true, observation: "Canhoto legível", receiverName: "João"
  }), {
    status: "delivered", reason: "", observation: "Canhoto legível", receiverName: "João", occurrenceType: "",
    itemCode: "", expectedQuantity: "", receivedQuantity: ""
  });
  assert.throws(() => validateInput({
    status: "delivered", started: true, hasPhoto: true, receiverName: "João da Silva"
  }), /primeiro nome/);
  assert.throws(() => validateInput({
    status: "delivered", started: true, hasPhoto: true, receiverName: "João", observation: "x".repeat(941)
  }), /940 caracteres/);
});

test("recusa aceita somente os dois motivos operacionais", () => {
  assert.throws(() => validateInput({
    status: "refused", started: true, refusalReason: "Cliente fechado"
  }), /Pedido Duplicado ou Sem Pedido/);
  assert.equal(validateInput({
    status: "refused", started: true, refusalReason: "Sem Pedido"
  }).reason, "Sem Pedido");
});

test("ocorrência geral e falta parcial são validadas separadamente", () => {
  assert.throws(() => validateInput({
    status: "occurrence", started: true, occurrenceType: "general", reason: ""
  }), /Descreva o motivo/);
  assert.throws(() => validateInput({
    status: "occurrence", started: true, occurrenceType: "partial_shortage",
    itemCode: "1940", expectedQuantity: "12", receivedQuantity: "12"
  }), /deve ser menor/);
  const partial = validateInput({
    status: "occurrence", started: true, occurrenceType: "partial_shortage",
    itemCode: "1940", expectedQuantity: "12", receivedQuantity: "11"
  });
  assert.equal(partial.reason, "Falta parcial");
  assert.equal(partial.itemCode, "1940");
});

test("situação inválida e ausência do início são bloqueadas", () => {
  assert.throws(() => validateInput({ status: "pending", started: true }), /situação válida/);
  assert.throws(() => validateInput({ status: "delivered", hasPhoto: true }), /Iniciar descarregamento/);
  assert.equal(statusLabel("refused"), "Recusa");
  assert.equal(requirements("partial"), null);
});
