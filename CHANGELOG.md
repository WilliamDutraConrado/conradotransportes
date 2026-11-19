# Changelog

Todas as alterações relevantes deste projeto são registradas aqui.

## 0.9.0-rc.1 — 2026-11-19

### Marca e experiência

- Aplicativo do motorista renomeado para DutraLog.
- Central de gestão renomeada para DutraTracking.
- Nova identidade visual compartilhada, com símbolo de rota em SVG e ícones PWA atualizados.
- Interface mais clara, com hierarquia visual, tipografia e contraste revisados para desktop e celular.
- Textos operacionais revisados em português, com pluralização natural e instruções menos técnicas.

### Operação

- Exportações administrativas identificadas com a marca DutraTracking.
- Atualização da versão do PWA para distribuir os novos recursos visuais sem preservar o cache anterior.

## 0.8.0-rc.1 — 2026-11-18

### Adicionado

- PWA instalável com ícones Android, maskable e Apple Touch.
- Guia de instalação para Android e iPhone e atualização controlada do service worker.
- Fila offline cifrada com recuperação de registro corrompido e reautenticação após sessão expirada.
- Perfis Administrador, Operador e Auditor, com validação visual e permissões separadas.
- Exportações autenticadas de ocorrências e auditoria em CSV.
- Diagnóstico agregado sem placas, clientes, endereços ou credenciais.
- Retenção configurável de sessões, auditoria e canhotos de viagens encerradas.
- Logs JSON com rota normalizada, duração, status e identificador de requisição.
- Backup cifrado, restauração protegida e verificação de integridade.
- Migração `006_retention_indexes` para consultas de manutenção.
- Auditoria automática de dependências no CI.

### Segurança e confiabilidade

- Serialização de reenvios concorrentes no SQLite e idempotência por `event_id`.
- Rejeição de XML malformado, DTD/ENTITY, regex arbitrária na API e fotos truncadas.
- Proteção contra fórmulas em CSV.
- Sessões vencidas recusadas e elegíveis para limpeza automática.

### Validação

- Testes Python, testes Node da fila offline, smoke, E2E e auditoria operacional.
- Ensaio visual móvel e dos perfis Operador/Auditor com dados sintéticos.

### Pendências para homologação

- Deploy real, domínio HTTPS e segredos de produção.
- Aprovação formal dos prazos de retenção.
- Ensaio de restauração em um segundo projeto Supabase vazio.
- Instalação e teste de câmera em aparelho físico.
