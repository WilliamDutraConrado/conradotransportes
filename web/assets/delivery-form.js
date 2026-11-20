"use strict";

(function exposeDeliveryForm(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DutraDelivery = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDeliveryForm() {
  const STATUS_LABELS = Object.freeze({
    pending: "Pendente",
    delivered: "Entregue",
    occurrence: "Ocorrência geral",
    partial: "Falta parcial (registro anterior)",
    refused: "Recusa"
  });

  const RULES = Object.freeze({
    delivered: Object.freeze({
      photoRequired: true,
      hint: "Fotografe o canhoto assinado. A baixa só será concluída após a foto ser preservada."
    }),
    refused: Object.freeze({
      photoRequired: false,
      hint: "Selecione exclusivamente Pedido Duplicado ou Sem Pedido."
    }),
    occurrence: Object.freeze({
      photoRequired: false,
      hint: "Descreva a ocorrência. Em falta parcial, selecione o item e informe a quantidade física."
    })
  });

  const REFUSAL_REASONS = Object.freeze(["Pedido Duplicado", "Sem Pedido"]);
  const OCCURRENCE_TYPES = Object.freeze(["general", "partial_shortage"]);

  function statusLabel(status) {
    return STATUS_LABELS[status] || String(status || "");
  }

  function requirements(status) {
    return RULES[status] || null;
  }

  function numberValue(value, label) {
    const text = String(value ?? "").trim().replace(",", ".");
    if (!/^\d+(?:\.\d{1,4})?$/.test(text)) throw new Error(`Informe ${label} válida.`);
    const number = Number(text);
    if (!Number.isFinite(number) || number < 0) throw new Error(`Informe ${label} válida.`);
    return number;
  }

  function validateInput(input) {
    const status = String(input?.status || "").trim();
    const rule = requirements(status);
    if (!rule) throw new Error("Escolha uma situação válida.");
    if (!input?.started) throw new Error("Toque em “Iniciar descarregamento” antes de concluir.");

    const observation = String(input?.observation || "").trim();
    if (observation.length > 1000) throw new Error("A observação deve ter no máximo 1.000 caracteres.");
    if (rule.photoRequired && !input?.hasPhoto) {
      throw new Error("Fotografe o canhoto assinado para confirmar a entrega.");
    }

    let reason = "";
    let occurrenceType = "";
    let itemCode = "";
    let expectedQuantity = "";
    let receivedQuantity = "";

    if (status === "refused") {
      reason = String(input?.refusalReason || "").trim();
      if (!REFUSAL_REASONS.includes(reason)) throw new Error("Selecione Pedido Duplicado ou Sem Pedido.");
    } else if (status === "occurrence") {
      occurrenceType = String(input?.occurrenceType || "").trim();
      if (!OCCURRENCE_TYPES.includes(occurrenceType)) throw new Error("Selecione o tipo da ocorrência.");
      if (occurrenceType === "general") {
        reason = String(input?.reason || "").trim();
        if (reason.length < 3) throw new Error("Descreva o motivo da ocorrência.");
        if (reason.length > 1000) throw new Error("O motivo deve ter no máximo 1.000 caracteres.");
      } else {
        itemCode = String(input?.itemCode || "").trim();
        if (!itemCode) throw new Error("Selecione o item em falta.");
        expectedQuantity = String(input?.expectedQuantity || "").trim();
        receivedQuantity = String(input?.receivedQuantity || "").trim();
        const expected = numberValue(expectedQuantity, "a quantidade total da NF-e");
        const received = numberValue(receivedQuantity, "a quantidade física recebida");
        if (received >= expected) {
          throw new Error("A quantidade física deve ser menor que a quantidade total da NF-e.");
        }
        reason = "Falta parcial";
      }
    }

    return {
      status, reason, observation, occurrenceType, itemCode, expectedQuantity, receivedQuantity
    };
  }

  return {
    STATUS_LABELS, REFUSAL_REASONS, OCCURRENCE_TYPES,
    statusLabel, requirements, validateInput
  };
});
