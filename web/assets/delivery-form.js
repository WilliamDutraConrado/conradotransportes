"use strict";

(function exposeDeliveryForm(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DutraDelivery = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createDeliveryForm() {
  const STATUS_LABELS = Object.freeze({
    pending: "Pendente",
    delivered: "Entregue",
    occurrence: "Ocorrência",
    partial: "Parcial",
    refused: "Recusada"
  });

  const RULES = Object.freeze({
    delivered: Object.freeze({
      photoRequired: true,
      reasonRequired: false,
      hint: "Para concluir a entrega, fotografe o canhoto assinado.",
      reasonPlaceholder: "Observação opcional"
    }),
    occurrence: Object.freeze({
      photoRequired: false,
      reasonRequired: true,
      hint: "Descreva o ocorrido. A fotografia é opcional.",
      reasonPlaceholder: "Ex.: Cliente fechado"
    }),
    partial: Object.freeze({
      photoRequired: false,
      reasonRequired: true,
      hint: "Informe o motivo e, se possível, o produto e a quantidade afetados.",
      reasonPlaceholder: "Ex.: Falta de produto"
    }),
    refused: Object.freeze({
      photoRequired: false,
      reasonRequired: true,
      hint: "Informe por que o cliente recusou a entrega.",
      reasonPlaceholder: "Ex.: Pedido em desacordo"
    })
  });

  function statusLabel(status) {
    return STATUS_LABELS[status] || String(status || "");
  }

  function requirements(status) {
    return RULES[status] || null;
  }

  function validateInput(input) {
    const status = String(input && input.status || "").trim();
    const rule = requirements(status);
    if (!rule) throw new Error("Escolha uma situação válida.");

    const arrivalTime = String(input && input.arrivalTime || "").trim();
    if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(arrivalTime)) {
      throw new Error("Informe um horário de chegada válido.");
    }

    const reason = String(input && input.reason || "").trim();
    if (rule.reasonRequired && reason.length < 3) {
      throw new Error("Informe o motivo com pelo menos 3 caracteres.");
    }
    if (reason.length > 1000) throw new Error("O motivo deve ter no máximo 1.000 caracteres.");
    if (rule.photoRequired && !(input && input.hasPhoto)) {
      throw new Error("Fotografe o canhoto assinado para confirmar a entrega.");
    }

    return { status, arrivalTime, reason };
  }

  return { STATUS_LABELS, statusLabel, requirements, validateInput };
});
