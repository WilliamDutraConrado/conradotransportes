const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(root, "web", "motorista.html"), "utf8");
const script = fs.readFileSync(path.join(root, "web", "assets", "motorista.js"), "utf8");
const stateFunction = script.match(/function locationResultState\(result\) \{[\s\S]*?^\}/m);
assert.ok(stateFunction, "função de tradução do estado de GPS ausente");
const locationResultState = vm.runInNewContext(`(${stateFunction[0]})`);

test("explica o uso e a retenção da localização antes do login", () => {
  const disclosure = html.indexOf('id="locationDisclosure"');
  const login = html.indexOf('id="login"');
  assert.ok(disclosure >= 0, "aviso de localização ausente");
  assert.ok(login > disclosure, "o aviso deve aparecer antes do login que ativa a rota");
  assert.match(html, /somente enquanto o aplicativo estiver aberto/);
  assert.match(html, /mantidas por até 30 dias/);
  assert.match(html, /aria-describedby="locationDisclosure loginHint"/);
});

test("traduz respostas do servidor em estados explícitos do GPS", () => {
  assert.deepEqual(
    { ...locationResultState({ recorded: true }) },
    { text: "Localização compartilhada", state: "on", remember: true, stop: false },
  );
  assert.deepEqual(
    { ...locationResultState({ recorded: false, reason: "throttled" }) },
    { text: "Localização compartilhada", state: "on", remember: true, stop: false },
  );
  assert.deepEqual(
    { ...locationResultState({ recorded: false, reason: "no_active_trip" }) },
    { text: "Localização pausada: sem viagem liberada", state: "idle", remember: false, stop: true },
  );
  assert.deepEqual(
    { ...locationResultState({ recorded: false, reason: "unavailable" }) },
    { text: "Localização indisponível no servidor", state: "off", remember: false, stop: true },
  );
  assert.deepEqual(
    { ...locationResultState({ recorded: false, reason: "future_reason" }) },
    { text: "Localização não compartilhada", state: "off", remember: false, stop: false },
  );
  assert.match(script, /Localização aguardando conexão/);
  assert.match(script, /Falha ao compartilhar localização; nova tentativa automática/);
});

test("interrompe a coleta quando o servidor não pode aceitar posições", () => {
  assert.match(script, /const outcome = locationResultState\(result\);/);
  assert.match(script, /if \(outcome\.stop\) stopLocationSharing\(false\);/);
  assert.match(script, /reason === "unavailable"[\s\S]*?stop: true/);
  assert.match(script, /reason === "no_active_trip"[\s\S]*?stop: true/);
});
