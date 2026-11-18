# Pacote de commit — 0.8.0-rc.1

## Conteúdo

- Backend, PWA, migrações `003`–`006`, testes, Dockerfile e documentação.
- Versão única: `0.8.0-rc.1` em `VERSION`, backend, HTML e service worker.
- Binários esperados: quatro ícones PNG gerados por `tools/generate_pwa_icons.ps1`.

## Verificação antes do commit

```powershell
python -m unittest discover -s tests
node --test tests/js/test_offline_queue.js
node --check web/assets/motorista.js
node --check web/assets/gestao.js
node --check web/sw.js
python -m pip_audit --requirement requirements-postgres.txt --strict
git diff --check
```

## Evidências da candidata

Verificações concluídas em 18/11/2026:

- 119 testes Python aprovados;
- 7 testes Node da fila offline aprovados;
- sintaxe dos scripts do motorista, da gestão e do service worker aprovada;
- `pip-audit 2.10.1` sem vulnerabilidades conhecidas nas dependências resolvidas;
- perfis Operador e Auditor validados visualmente em largura móvel, com ações e exportações restritas conforme a função;
- migração `006_retention_indexes` aplicada e verificada no ambiente de desenvolvimento;
- fluxo E2E e auditoria operacional do ambiente de desenvolvimento aprovados após a migração.
- prévia real da retenção executada sem exclusões: zero sessões, limites de login, auditorias ou evidências candidatas.

A política automática de retenção permanece desabilitada até aprovação operacional dos prazos. A verificação de liberação para hospedagem exige que ela seja habilitada explicitamente.

## Ponto de parada para o responsável

Antes da hospedagem, o responsável precisa:

1. escolher o provedor e informar a origem HTTPS de homologação;
2. aprovar ou ajustar os prazos propostos: evidências por 180 dias, auditoria por 730 dias e manutenção a cada 24 horas;
3. disponibilizar `SUPABASE_SECRET_KEY` e `SUPABASE_PROJECT_REF`, substituindo a chave legada;
4. garantir `pg_dump` e `pg_restore` no ambiente de operação para o ensaio de backup e restauração.

Com essas definições, a configuração hospedada deverá usar `CONRADO_BIND=0.0.0.0`, `CONRADO_ALLOW_ADMIN_TOKEN=0`, `CONRADO_ACCESS_LOG=1` e `CONRADO_RETENTION_ENABLED=1`. Depois disso, executar preflight, smoke, E2E, auditoria operacional e ensaio de restauração antes de liberar motoristas reais.

O pacote deve ser versionado somente depois de confirmar que `.env`, XML, backup ou dados reais não aparecem no índice do Git. A tag da candidata não autoriza publicação: nenhum push ou deploy deve ocorrer antes das decisões acima.
