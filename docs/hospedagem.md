# Implantação planejada — Railway + Supabase

## Status atual

**Não publicar a aplicação atual em produção.** O código do piloto utiliza SQLite local, um servidor HTTP de desenvolvimento e a foto do canhoto fica gravada no SQLite sem criptografia. O SQL PostgreSQL em `infra/supabase/001_schema.sql` está preparado para revisão, mas ainda **não foi executado**, e o backend **não foi migrado**.

Infraestrutura criada em 17/11/2026: projeto gratuito `conrado-entregas-dev` na região São Paulo, com bucket privado `canhotos`, permitindo apenas JPEG e PNG de até 5 MB. Nenhum documento real foi importado e nenhuma cobrança foi autorizada.

O módulo `conrado/supabase_storage.py` implementa a comunicação privada para envio de imagens e geração de URLs temporárias (máximo 5 minutos). Está testado com respostas simuladas, **mas ainda não está integrado ao fluxo de entrega**. Não disponibilize a chave de serviço no APK, painel ou código do GitHub.

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
