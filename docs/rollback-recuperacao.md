# Rollback e recuperação de desastre

## Quando interromper o piloto

Interromper novas baixas se houver perda de dados, duplicidade não idempotente, indisponibilidade prolongada, acesso indevido ou falha de restauração. Preservar logs e não apagar evidências manualmente.

## Rollback do aplicativo

1. Colocar o piloto em pausa e orientar motoristas a não descartarem filas locais.
2. Reativar no provedor a última versão aprovada do container.
3. Confirmar `/healthz`, `/readyz`, login e consulta de uma viagem sintética.
4. Reabrir a PWA e sincronizar primeiro um único aparelho de teste.
5. Liberar os demais somente após confirmar ausência de duplicidade.

A migração `006_retention_indexes` cria apenas índices e reafirma permissões; normalmente não precisa ser revertida. Migrações de dados nunca devem ser desfeitas automaticamente.

## Recuperação do banco e canhotos

1. Criar um projeto Supabase vazio e isolado.
2. Configurar credenciais temporárias somente no ambiente de recuperação.
3. Executar `python -m conrado.backup verify ARQUIVO`.
4. Executar a restauração apontando explicitamente para o projeto vazio.
5. Rodar preflight, smoke, E2E e auditoria operacional.
6. Comparar o manifesto do backup, contagens e hashes de canhotos.
7. Somente depois decidir a troca de tráfego ou importação controlada.

## Objetivos pendentes de decisão

- **RPO:** perda máxima aceitável de dados em horas.
- **RTO:** tempo máximo aceitável para restaurar o serviço.
- Responsáveis por declarar incidente, aprovar restauração e liberar o retorno.
