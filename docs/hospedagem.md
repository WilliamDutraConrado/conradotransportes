# Implantação planejada — Railway + Supabase

## Status atual

**Não publicar a aplicação atual em produção.** O piloto local utiliza SQLite e HTTP de desenvolvimento. Existe agora um adaptador PostgreSQL no código, mas a migração SQL ainda **não foi executada** no Supabase e a integração não foi validada em banco real.

Infraestrutura criada em 17/11/2026: projeto gratuito `conrado-entregas-dev` na região São Paulo, com bucket privado `canhotos`, permitindo apenas JPEG e PNG de até 5 MB. Nenhum documento real foi importado e nenhuma cobrança foi autorizada.

O módulo `conrado/supabase_storage.py` implementa envio privado de imagens e URLs temporárias (máximo 5 minutos); ele está integrado **somente ao novo adaptador PostgreSQL**. Os testes de armazenamento ainda usam respostas simuladas. Não disponibilize a chave de serviço no APK, painel ou código do GitHub.

## Arquitetura-alvo

- **Railway:** aplicação Python HTTPS atrás do proxy, serviço sem disco persistente de documentos. Variáveis sensíveis somente no gerenciador de secrets.
- **Supabase Postgres:** schema `app` privado; dados das transportadoras e viagens; conexão exclusiva pelo backend, com pool e credenciais protegidas.
- **Supabase Storage:** bucket privado `canhotos`; o backend concede acesso temporário a comprovantes autorizados. Nunca criar bucket público.
- **Motorista Android:** autenticação individual, fila offline local protegida, envio idempotente e sincronização com confirmação do servidor.

## Antes de qualquer deploy

1. Instalar/autorizar os conectores Railway e Supabase nas contas corretas.
2. Criar projeto **de desenvolvimento** sem informações reais. Não contratar plano pago sem aprovação.
3. Revisar `001_schema.sql` e aplicar em uma base vazia de teste.
4. Migrar o backend de SQLite para PostgreSQL e imagens do BLOB para Storage privado.
5. Adicionar TLS, autenticação de administração real, rate limiting, permissões multiempresa, auditoria e estratégia de backups.
6. Preparar healthcheck, variáveis de ambiente, migrações automáticas controladas, observabilidade e testes de integração.
7. Somente depois criar o serviço Railway, configurar domínio de teste e testar fluxo completo com dados fictícios.

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
# Integração PostgreSQL preparada em código (ainda não implantada)

O adaptador `conrado/postgres_store.py` implementa as operações da gestão e do
motorista usando exclusivamente o schema privado `app`. O backend seleciona o
banco apenas quando `CONRADO_BACKEND=postgres`, e depende de:

- `DATABASE_URL`: conexão PostgreSQL privada disponibilizada pelo Supabase.
- `SUPABASE_URL`: URL HTTPS do projeto Supabase.
- `SUPABASE_SERVICE_ROLE_KEY`: chave secreta **somente no servidor**.
- `CONRADO_ADMIN_TOKEN`: token administrativo aleatório de 20+ caracteres.

Instalar `requirements-postgres.txt` no servidor. Executar `infra/supabase/001_schema.sql`
no SQL Editor do projeto correto, após conferir o alvo; não há migração automática.
O bucket privado `canhotos` precisa existir com JPEG/PNG habilitados.

Com as três variáveis de ambiente configuradas em um terminal seguro, rodar
`python -m conrado.preflight`. A verificação é **somente leitura**: consulta a
existência das nove tabelas, a ausência de acesso do papel `anon` ao schema
privado e o estado privado do bucket. Se falhar, informa o ponto pendente sem
imprimir senhas ou chaves. Não substitui testes reais de importação e entrega.

**Não publicar `conrado.gestao` diretamente na Railway ainda:** o servidor
HTTP atual foi concebido para `127.0.0.1`, ainda não tem proteção completa
para internet pública (HTTPS na borda, autenticação administrativa de produção,
limite de tentativas de login, tratamento centralizado de falhas e monitoramento).
Os testes com PostgreSQL usam simulações; ainda faltam testes reais de integração
com o banco Supabase e a execução validada da migração SQL. Não inserir chaves,
placas reais, XMLs ou fotos no repositório.

