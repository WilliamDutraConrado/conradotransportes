# Runbook de hospedagem de teste

Este runbook prepara um ambiente **de teste**. Ele não autoriza uso em produção,
dados reais, plano pago ou publicação em lojas de aplicativos.

## Pré-requisitos

1. A suíte local precisa passar integralmente.
   `python -m conrado.release_check` precisa aprovar as variáveis e ferramentas
   dentro da imagem de hospedagem.
2. `python -m conrado.preflight`, `python -m conrado.smoke` e
   `python -m conrado.e2e` precisam terminar com sucesso. Depois da limpeza do
   E2E, `python -m conrado.ops_audit` também precisa ser aprovado.
3. O domínio de teste precisa usar HTTPS e ser o único valor de
   `CONRADO_ALLOWED_ORIGINS`.
4. O responsável precisa executar e registrar um ensaio da rotina cifrada de
   backup e restauração em projeto vazio antes de qualquer dado operacional.

A rotina `python -m conrado.backup` reúne o dump lógico e as evidências privadas
em um pacote cifrado com manifesto de hashes. Ela complementa, mas não substitui,
backup gerenciado e cópia fora do provedor. Consulte `backup-restauracao.md`.

## Variáveis do serviço

Configurar apenas no gerenciador de segredos do provedor:

- `CONRADO_BACKEND=postgres`
- `CONRADO_BIND=0.0.0.0`
- `CONRADO_ALLOWED_ORIGINS=https://DOMINIO_EXATO_DE_TESTE`
- `CONRADO_ACCESS_LOG=1`
- `DATABASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SUPABASE_PROJECT_REF`
- `CONRADO_RETENTION_ENABLED=1` após aprovação formal dos prazos
- `CONRADO_EVIDENCE_RETENTION_DAYS=180`
- `CONRADO_AUDIT_RETENTION_DAYS=730`
- `CONRADO_MAINTENANCE_INTERVAL_HOURS=24`

Em hospedagem, a chave administrativa estática fica desabilitada por padrão. A
variável `CONRADO_ADMIN_TOKEN` é somente para o piloto local de transição.

`PORT` é configurada pelo `render.yaml`. Não gravar valores secretos em
Dockerfile, Blueprint, GitHub Actions, logs ou mensagens.

## Configuração do Render

- Conectar o Blueprint `render.yaml` da raiz ao repositório público.
- Confirmar o plano `free`, região `virginia` e healthcheck `/readyz` antes de
  criar o serviço.
- Preencher as quatro variáveis marcadas com `sync: false` diretamente no
  painel. Nunca enviar esses valores ao chat ou ao GitHub.
- O container já inclui `pg_dump` e `pg_restore`; o arquivo de backup precisa ser
  retirado imediatamente do disco efêmero e guardado em destino cifrado.
- O limitador de login usa a tabela privada `app.login_limits` e funciona entre
  reinícios ou réplicas sem armazenar IP ou placa em claro.
- Criar somente domínio de teste; não apontar domínio operacional.

O plano gratuito não oferece shell. Antes de liberar o domínio, executar em uma
máquina autorizada, com as mesmas variáveis do Supabase carregadas localmente,
`python -m conrado.admin_user NOME_DO_USUARIO`. A senha é solicitada duas vezes
com entrada oculta. Não passá-la como argumento, variável de build, mensagem ou
log. Depois, validar login/logout e manter `CONRADO_ALLOW_ADMIN_TOKEN=0`.

Aplicar antes do primeiro login: `003_persistent_login_limits`, `004_admin_auth`,
`005_admin_roles` e `006_retention_indexes`. O executor verifica RLS após cada migração. Não configurar
migração destrutiva automática no processo de deploy.

Antes de publicar a versão com a torre de controle, aplicar e validar
`python -m conrado.migrate 008_driver_positions`. A migração
`008_driver_positions` deve estar concluída antes do deploy da torre para que o
sinal GPS contínuo, a trilha do dia e o indicador "Com sinal ao vivo" reflitam
as posições dos motoristas. Não automatizar a migração no processo de deploy.

O Render consulta o healthcheck durante o deploy. No plano gratuito, o serviço
hiberna após inatividade e pode levar cerca de um minuto para reativar. Isso é
aceitável apenas em homologação; monitoramento contínuo ainda precisa ser
configurado separadamente.

## Validação após deploy

1. Confirmar `GET /healthz` com HTTP 200.
2. Confirmar `GET /readyz` com HTTP 200.
3. Confirmar que uma origem diferente do domínio configurado recebe HTTP 403 em
   operações de escrita.
4. Executar o fluxo completo somente com NF-e e PNG sintéticos.
5. Confirmar no banco e no bucket que a limpeza removeu os IDs e caminhos do
   teste.
6. Conferir que logs JSON possuem `request_id`, rota normalizada e duração, mas
   não tokens, senhas, XML, placa, cliente ou URL assinada.
7. Instalar `/motorista` no aparelho de homologação e executar o roteiro de
   modo avião descrito em `pwa-offline.md`.
8. Criar backup cifrado, verificá-lo e restaurá-lo em um segundo projeto vazio.
9. Conferir `/api/admin/diagnostics` e as duas exportações com perfis autorizados.
10. Executar `python -m conrado.maintenance preview` antes de habilitar a rotina.

## Rollback

1. Remover o domínio público do serviço se houver risco de exposição.
2. Reativar um dos dois últimos deploys aprovados no painel do Render.
3. Se houver suspeita de segredo exposto, rotacionar token administrativo, chave
   de serviço e senha do banco antes de reabrir o acesso.
4. Não reverter migração destrutivamente. Restaurar primeiro em um projeto novo
   e validar banco e Storage separadamente.
5. Registrar horário, versão, impacto e IDs afetados sem copiar dados pessoais
   para o chamado.

## Bloqueios para piloto real

- Revisão periódica das contas administrativas e definição dos responsáveis.
- Ensaio presencial da restauração cifrada do banco e do Storage.
- Aprovação formal dos prazos de retenção e da base legal para canhotos.
- Monitoramento contínuo e alerta de indisponibilidade.
- Homologação da fila offline em aparelho Android físico após reconexão e reinício.
