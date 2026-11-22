# Changelog

## 0.10.0-rc.4 — 2026-11-21

- Cabeçalho do aplicativo do motorista refinado em azul-marinho, com contraste e acabamento próprios para uso no celular.
- Atalhos de navegação reduzidos aos ícones oficiais do Waze e do Google Maps, preservando os destinos e a área mínima de toque.
- Baixa como `Entregue` agora solicita somente o primeiro nome do recebedor; o campo de observações permanece vazio e opcional.
- Cache da PWA atualizado para distribuir os novos ícones e a interface revisada aos aparelhos instalados.

Todas as alterações relevantes deste projeto são registradas aqui.

## 0.10.0-rc.3 — 2026-11-20

### Identidade visual

- Emblema oficial fornecido para o DutraLog aplicado ao aplicativo do motorista, ao DutraTracking e ao painel local de conferência.
- Cabeçalhos, favicon, Apple Touch Icon e ícones PWA Android/maskable agora usam a mesma fonte visual.
- Arquivo-mestre e gerador reproduzível de ícones mantidos no projeto, sem alterar proporção ou recortar o símbolo.
- Cache da PWA e versão de release avançados para `0.10.0-rc.3`, permitindo a atualização controlada nos aparelhos já instalados.

## 0.10.0-rc.2 — 2026-11-20

### Acesso do motorista

- Novos acessos usam PIN individual de exatamente 5 dígitos; sequências óbvias e números repetidos são recusados.
- A administração orienta e valida o PIN antes do envio, enquanto o servidor repete a validação como fonte de verdade.
- Credenciais anteriores continuam válidas somente durante a migração, evitando interromper uma rota antes da redefinição individual.
- Cinco falhas em cinco minutos bloqueiam o motorista por quinze minutos; a conta administrativa mantém a política separada de senha forte.
- Cache da PWA e versão de release avançados para `0.10.0-rc.2`.

## 0.10.0-rc.1 — 2026-11-20

### Operação do motorista

- Rota redesenhada em cartões recolhíveis, com ordem numérica crescente de NF-e e destaque da próxima entrega.
- Atalhos oficiais por endereço para Waze e Google Maps em cada NF-e.
- Início explícito do descarregamento com horário do aparelho, cronômetro e localização pontual opcional.
- Tela ativa exibe somente pendências; baixas confirmadas ou preservadas offline saem da rota.
- Situações limitadas a Entregue, Recusa e Ocorrência geral; recusa restrita a Pedido Duplicado ou Sem Pedido.
- Falta parcial tratada dentro da ocorrência, com item validado contra o XML e comparação entre quantidade total e física.

### Fotos e confiabilidade

- Captura aceita imagens da câmera, corrige a orientação quando suportada, redimensiona e comprime para JPEG antes do envio.
- Limite explícito para foto original, tempo maior de upload e mensagens distintas entre preparação, envio e confirmação.
- Foto obrigatória de Entregue validada no navegador e novamente no servidor; upload privado tem compensação em caso de falha do banco.
- Resposta perdida após upload é recuperada somente após baixar o objeto privado e confirmar seu hash exato.
- Fila offline cifrada preserva foto e baixa; uma rejeição do servidor devolve a NF-e à rota para correção.
- O app solicita armazenamento persistente quando o navegador oferece o recurso, reduzindo risco de limpeza automática da fila.
- Arquivos funcionais usam revalidação HTTP para evitar que uma correção urgente fique presa no cache antigo.

### Gestão, histórico e segurança

- Estorno exclusivo de Administrador, com motivo obrigatório, reabertura segura da viagem e preservação dos eventos/evidências anteriores.
- Histórico registra início, fim, duração, localização, observação, quantidades e estornos; atendimentos em andamento aparecem na gestão.
- Concorrência serializada por NF-e para impedir dois inícios ou duas baixas simultâneas; uma baixa concluída não pode ser sobrescrita pelo motorista.
- Migração `007_delivery_workflow` aditiva, tabela de inícios privada com RLS e geolocalização liberada apenas para a própria origem HTTPS.

### Validação

- 132 testes Python e 12 testes JavaScript aprovados.
- Preflight do Supabase, smoke das 13 tabelas, auditoria de dependências e E2E real com foto sintética aprovados.
- E2E confirmou início, baixa, Storage privado, ocultação, encerramento e limpeza integral dos dados fictícios.

## 0.9.0-rc.2 — 2026-11-19

### Correções urgentes no DutraLog

- Validação imediata dos campos obrigatórios para Entregue, Recusada, Parcial e Ocorrência.
- Mensagens de erro e confirmação exibidas dentro da própria nota fiscal.
- Indicação visível de salvamento para evitar toques repetidos durante o envio da fotografia.
- Atualização pontual da nota após a baixa, sem recarregar toda a rota nem perder a posição da tela.
- Progresso visível durante o login e atualização do cache do PWA para distribuir a correção.

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
