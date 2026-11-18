# PWA e operação offline do motorista

O endereço `/motorista` é uma Progressive Web App. Em HTTPS, o navegador pode
instalá-la na tela inicial sem APK ou loja de aplicativos.

## Comportamento implementado

- O shell da aplicação é mantido pelo service worker; rotas `/api/` nunca são
  colocadas no cache.
- A última viagem carregada fica cifrada no IndexedDB por AES-GCM com chave não
  exportável do navegador.
- Baixas, fotografias e pedidos de encerramento sem confirmação entram em uma
  fila cifrada.
- O mesmo `event_id` é reutilizado no reenvio. O backend trata repetição como
  sucesso idempotente, evitando duas baixas para a mesma ação.
- A fila só remove uma operação após HTTP de sucesso. Erros de validação ficam
  bloqueados para revisão; falhas de rede, sessão ou servidor permanecem
  pendentes.
- Ao recuperar a internet, a sincronização é automática se a sessão ainda for
  válida; também existe botão manual.
- Se a sessão expirar, a fila permanece no aparelho e o aplicativo solicita novo
  login. Um registro local corrompido é isolado para não bloquear os seguintes.
- Atualizações ficam aguardando confirmação; o app não recarrega no meio de uma baixa.

## Limites de segurança

- Uma rota nova exige autenticação online. A rota já armazenada pode ser aberta
  offline no mesmo aparelho.
- Depois de fechar a sessão, operações pendentes continuam cifradas no aparelho
  e exigem novo login para envio.
- O aparelho precisa usar bloqueio de tela, sistema atualizado e não pode ser
  compartilhado entre motoristas durante uma rota.
- A PWA não é um APK nativo, não rastreia localização e não envia WhatsApp
  automaticamente.

## Homologação presencial

1. Abrir o domínio HTTPS e usar **Instalar app**. No iPhone, usar Safari,
   Compartilhar e **Adicionar à Tela de Início**.
2. Entrar com motorista fictício e carregar uma viagem sintética.
3. Ativar modo avião, registrar ocorrência com fotografia e solicitar
   encerramento.
4. Fechar e reabrir a PWA, conferir a fila e a rota salva.
5. Restaurar a conexão, autenticar novamente se solicitado e sincronizar.
6. Confirmar no painel uma única baixa, o canhoto privado e a fila vazia.
7. Repetir simulando resposta demorada e clique duplicado.
8. Publicar uma nova versão de teste, confirmar o aviso e atualizar após concluir
   o preenchimento atual.

Somente depois desse roteiro o modo offline pode ser homologado para piloto.
