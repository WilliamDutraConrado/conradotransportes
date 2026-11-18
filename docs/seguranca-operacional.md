# Segurança operacional

## Retenção

A limpeza automática fica desativada até aprovação dos prazos. Na hospedagem, configurar:

- `CONRADO_RETENTION_ENABLED=1`
- `CONRADO_EVIDENCE_RETENTION_DAYS=180`
- `CONRADO_AUDIT_RETENTION_DAYS=730`
- `CONRADO_MAINTENANCE_INTERVAL_HOURS=24`

Canhotos só são elegíveis após o encerramento da viagem. Sessões vencidas e limitadores antigos também são removidos. Antes de ativar, executar `python -m conrado.maintenance preview`; usar `apply` apenas em ensaio controlado.

## Logs

Com `CONRADO_ACCESS_LOG=1`, cada requisição gera JSON com nível, evento, identificador, método, rota normalizada, status e duração. Os logs não incluem corpo, token, senha, placa, cliente, endereço ou resposta do provedor.

## Dependências

Em 18/11/2026, `pip-audit 2.10.1` não encontrou vulnerabilidades conhecidas nas dependências de produção resolvidas por `requirements-postgres.txt`. O CI repete `python -m pip_audit --requirement requirements-postgres.txt --strict` em cada alteração.

## Exportações

CSV exige sessão autenticada, recebe cabeçalho para download e neutraliza células iniciadas por caracteres interpretados como fórmula. Os relatórios não permanecem gravados no backend.
