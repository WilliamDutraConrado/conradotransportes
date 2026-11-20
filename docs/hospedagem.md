# Implantação do piloto — Render + Supabase

## Status atual

O serviço de homologação usa Render atrás de HTTPS, PostgreSQL privado e Storage privado no Supabase. A liberação deve permanecer restrita ao piloto controlado; expansão ou uso como produção ampla depende de homologação em aparelhos reais, monitoramento e política de retenção aprovada.

Infraestrutura original validada em 18/11/2026 no projeto de desenvolvimento `frarirxzbajnfifngkfq`, região `us-east-1`: schema privado `app` com doze tabelas e RLS ativo, sem acesso de schema para `anon` ou `authenticated`; bucket privado `canhotos`, permitindo apenas JPEG e PNG de até 5 MB. A migração 007 acrescenta a décima terceira tabela, `delivery_starts`, também privada. Nenhum documento real foi importado e nenhuma cobrança foi autorizada.

Na mesma data, a conexão real pelo pooler IPv4 foi aprovada pelo preflight e o
smoke test transacional exercitou as tabelas existentes com rollback confirmado. Não
restaram registros fictícios e nenhum arquivo foi enviado ao Storage.

Em 18/11/2026, o teste E2E real cadastrou administrador, motorista e veículo fictícios,
criou e liberou uma viagem, importou uma NF-e sintética, registrou uma baixa,
enviou um PNG sintético ao bucket privado, validou uma URL temporária,
encerrou a viagem e revogou a sessão. A exclusão do objeto pela API do Storage e
a limpeza por IDs exatos foram confirmadas; nenhum arquivo ou registro permaneceu.

O módulo `conrado/supabase_storage.py` implementa envio privado, URLs temporárias
(máximo 5 minutos), consulta por caminho exato e exclusão de imagens; ele está
integrado **somente ao adaptador PostgreSQL**. Há testes simulados e validação
real com conteúdo sintético. Não disponibilize a chave de serviço no APK, painel
ou código do GitHub.

## Arquitetura-alvo

- **Render:** aplicação Python HTTPS atrás do proxy, serviço sem disco persistente de documentos. Variáveis sensíveis somente no gerenciador de secrets.
- **Supabase Postgres:** schema `app` privado; dados das transportadoras e viagens; conexão exclusiva pelo backend, com pool e credenciais protegidas.
- **Supabase Storage:** bucket privado `canhotos`; o backend concede acesso temporário a comprovantes autorizados. Nunca criar bucket público.
- **Motorista PWA:** instalação pelo navegador, autenticação individual, fila local cifrada, envio idempotente e sincronização com confirmação do servidor.

## Antes de qualquer deploy

1. Confirmar que o serviço Render e o projeto Supabase pertencem às contas corretas.
2. Criar projeto **de desenvolvimento** sem informações reais. Não contratar plano pago sem aprovação.
3. ~~Revisar `001_schema.sql` e aplicar em uma base vazia de teste.~~ Concluído no ambiente de desenvolvimento em 17/11/2026.
4. ~~Validar o backend PostgreSQL e o Storage privado com dados integralmente fictícios.~~ Concluído em 17/11/2026, incluindo limpeza verificada.
5. Adicionar TLS e permissões multiempresa. Autenticação individual, perfis administrativos, fila offline cifrada, atribuição de auditoria, rate limiting, retenção configurável e rotina de backup já foram implementados.
6. ~~Preparar healthcheck, variáveis de ambiente, migrações controladas e testes de integração.~~ Concluído no código; observabilidade externa depende do provedor.
7. O push em `main` aciona o deploy automático no Render; validar `/healthz`, `/readyz` e o fluxo sintético depois de cada versão.

## Segredos que nunca devem ir ao GitHub

- `DATABASE_URL` com senha do banco.
- Chave administrativa, tokens JWT e chaves do Supabase.
- Chave de serviço do Supabase Storage.
- XMLs reais, canhotos, nomes e placas dos motoristas reais.

Não enviar credenciais para o chat ou colar diretamente no código. Configure-as apenas nas telas oficiais do provedor.

## Critério para passar a teste real

- Fluxo importação → viagem → celular → fotografia → ocorrência → sincronização → encerramento.
- Teste de perda de conexão e reinício do aparelho sem perder dados.
- Isolamento entre empresas e motoristas, testes de controle de acesso e senhas.
- Backup e restauração de ensaio, logs sem dados pessoais e tempo de retenção definido.
- Aceite de termos operacionais e cuidados da LGPD; permissão para fotos/localização quando aplicável.

O projeto permanece em ambiente experimental enquanto essas verificações estiverem pendentes.
# Integração PostgreSQL validada no ambiente de desenvolvimento

O adaptador `conrado/postgres_store.py` implementa as operações da gestão e do
motorista usando exclusivamente o schema privado `app`. O backend seleciona o
banco apenas quando `CONRADO_BACKEND=postgres`, e depende de:

- `DATABASE_URL`: conexão PostgreSQL privada disponibilizada pelo Supabase.
- `SUPABASE_URL`: URL HTTPS do projeto Supabase.
- `SUPABASE_SECRET_KEY`: chave `sb_secret_...` **somente no servidor**. A variável
  legada `SUPABASE_SERVICE_ROLE_KEY` é aceita apenas durante a migração.
- `CONRADO_ADMIN_TOKEN`: opcional e somente para o piloto local de transição;
  manter `CONRADO_ALLOW_ADMIN_TOKEN=0` na hospedagem.

Instalar `requirements-postgres.txt` no servidor. O arquivo
`infra/supabase/001_schema.sql` já foi executado no ambiente de desenvolvimento;
não o reaplicar em outro projeto sem conferir explicitamente o alvo. Não há migração
automática. O bucket privado `canhotos` já existe com JPEG/PNG e limite de 5 MB.
Migrações incrementais conhecidas são aplicadas explicitamente, por exemplo:
`python -m conrado.migrate 003_persistent_login_limits`. O executor recusa
nomes arbitrários e verifica RLS e ausência de leitura por `anon` e
`authenticated` depois do commit. As migrações 003 a 007 foram aplicadas e
validadas no ambiente de desenvolvimento até 20/11/2026. A migração 004 cria as
tabelas privadas sem usuário ou senha; a 005 adiciona os perfis `admin`,
`operator` e `auditor`, preservando contas anteriores. A migração 006 prepara
índices para retenção e diagnóstico. A migração 007 adiciona início de
descarregamento, tempo, localização pontual e campos estruturados de ocorrência.

Copie `.env.example` para `.env`, preencha os segredos apenas no computador (o
arquivo é ignorado pelo Git) e rode
`python -m conrado.preflight`. A verificação é **somente leitura**: consulta a
existência das treze tabelas, RLS, ausência de acesso dos papéis `anon` e
`authenticated` ao schema privado e as restrições do bucket. Se falhar, informa o ponto pendente sem
imprimir senhas ou chaves. Não substitui testes reais de importação e entrega.

Depois da pré-verificação, `python -m conrado.smoke` exercita as treze tabelas
com dados totalmente fictícios e força rollback. O comando confirma em uma
segunda conexão que nenhum registro permaneceu e não envia arquivos ao Storage.

`python -m conrado.e2e` valida a API HTTP local conectada ao Supabase: cadastro
fictício, viagem, importação de NF-e sintética, início de descarregamento, baixa
com PNG sintético, ocultação da NF-e, URL temporária, encerramento e logout. A limpeza usa somente os IDs e o caminho
criados no teste e é verificada no banco e na listagem autenticada do bucket.

**Liberar somente no piloto controlado:** o servidor aceita
hospedagem somente atrás de HTTPS, com origem exata configurada e a
chave administrativa estática desabilitada. A autenticação administrativa
individual, os perfis e permissões, a atribuição de cada alteração à sessão, a
fila offline cifrada, o tratamento centralizado de falhas, o limitador de login
e a rotina de backup estão implementados. A retenção existe, mas permanece
desativada até aprovação dos prazos. Ainda faltam o ensaio presencial de
restauração, monitoramento externo e homologação em celular
antes de qualquer exposição com dados reais.
Os testes unitários continuam isolados por simulações, complementados pelo E2E
real e destrutivo somente para seus próprios dados sintéticos. Ainda faltam as
proteções operacionais listadas acima. Não inserir chaves, placas reais, XMLs ou
fotos no repositório.

