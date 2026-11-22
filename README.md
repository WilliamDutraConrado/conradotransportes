<p align="center">
  <img src="web/assets/emblema-dutralog.webp" alt="Emblema oficial DutraLog" width="180">
</p>

# DutraLog e DutraTracking

Projeto-piloto de gestão de entregas para a Conrado Transportes. O DutraLog atende a operação do motorista; o DutraTracking concentra a visão administrativa. Desenvolvimento iniciado em novembro de 2026.

Versão candidata atual: **0.10.0-rc.4**.

Escopo: importação manual de XML de NF-e, viagens vinculadas a placas, controle individual de notas e canhotos, ocorrências, painel administrativo e PWA instalável com fila offline.

Nenhuma NF-e real, imagem de canhoto ou credencial deve ser armazenada neste repositório público.

## Conferência local de XMLs

Requer Python 3.11+; não precisa instalar dependências.

```bash
python3 -m conrado.painel
```

Acesse `http://127.0.0.1:8765` no navegador do mesmo computador, selecione os XMLs e confira a separação por placa, as paradas e os totais. Essa tela não grava XMLs, não tem login e **não deve ser publicada na internet**. A regra da placa é provisória até recebermos um exemplo anonimizado.

Para rodar os testes:

```bash
python3 -m unittest discover -s tests -v
```

## Conexão local segura com o Supabase

O banco de desenvolvimento e o bucket privado já estão preparados. Para testar
o backend PostgreSQL sem gravar segredos no Git, copie `.env.example` para
`.env`, preencha os valores diretamente no computador e execute:

```bash
python3 -m conrado.preflight
python3 -m conrado.smoke
python3 -m conrado.e2e
python3 -m conrado.ops_audit
python3 -m conrado.release_check
python3 -m conrado.gestao
node --test tests/js/test_offline_queue.js
python3 -m pip_audit --requirement requirements-postgres.txt --strict
```

O arquivo `.env` é ignorado pelo Git e suas variáveis não são impressas. Não
envie a senha do banco, a chave secreta do Supabase ou o token administrativo
para o chat. A importação continua aceitando somente NF-e válidas e grava no
PostgreSQL apenas os campos extraídos necessários; o XML original não é salvo.
O comando `conrado.smoke` usa somente dados fictícios dentro de uma transação,
reverte tudo ao final e confirma em uma segunda conexão que nada permaneceu.
O comando `conrado.e2e` inicia a API local com PostgreSQL, autentica um
administrador fictício, confirma sua atribuição nos eventos, importa uma NF-e
fictícia, inicia o descarregamento, registra uma entrega com PNG sintético no
bucket privado, confirma a ocultação da NF-e, valida a URL temporária, encerra a
viagem e limpa os registros pelos IDs exatos.
O XML não é enviado ao Storage e a imagem de teste é removida pela API oficial.
O comando `conrado.ops_audit` é somente leitura e informa apenas totais: confere
eventos, evidências obrigatórias e a existência dos objetos privados, sem exibir
nomes, placas ou caminhos.

## Estrutura de dados (em desenvolvimento)

O módulo `conrado/store.py` implementa cadastro de motoristas/veículos, viagens em rascunho, importação transacional de notas e liberação de uma viagem por placa. Ele usa SQLite local, mantém um registro de eventos administrativos e está ligado à interface experimental de gestão. Nenhum motorista, placa ou administrador real vem pré-cadastrado. Os perfis `admin`, `operator` e `auditor` são aplicados também pelo servidor; o painel apenas reflete essas permissões.

## Gestão de viagens em teste

Agora o módulo de dados também possui interface própria. Para o piloto estritamente local, a chave administrativa de transição ainda pode ser usada:

```bash
export CONRADO_ADMIN_TOKEN='substitua-por-uma-chave-aleatoria-com-32-ou-mais-caracteres'
python3 -m conrado.gestao
```

Abra `http://127.0.0.1:8766` no **mesmo computador**. Insira a chave de transição ou entre com uma conta administrativa individual, cadastre motoristas e veículos, crie rascunhos de viagens, importe os XMLs correspondentes e libere a viagem após conferência. Dados são salvos em `conrado_local.sqlite` local (ignorado pelo Git). **Não use esta versão em produção:** ainda faltam o ensaio presencial de restauração, monitoramento contínuo e homologação operacional.

## Área móvel do piloto

No mesmo servidor de gestão, acesse `http://127.0.0.1:8766/motorista`. O administrador pode definir senhas individuais no cadastro; o motorista entra com placa e senha e visualiza somente a viagem liberada para ele. As entregas pendentes aparecem em ordem numérica crescente de NF-e, com atalhos para Waze e Google Maps. Ao tocar em **Iniciar descarregamento**, o app registra o horário e tenta uma coleta pontual e opcional da localização. Para baixar uma nota como **Entregue**, a fotografia é obrigatória. A PWA otimiza a foto, mantém rota e operações em fila cifrada no aparelho, sincroniza posteriormente e reaproveita identificadores idempotentes. Ela não é um APK nativo; a instalação no celular exige o domínio HTTPS da homologação.

O campo de situação oferece apenas **Entregue**, **Recusa** e **Ocorrência geral**. Recusa aceita somente **Pedido Duplicado** ou **Sem Pedido**. Em falta parcial, dentro de ocorrência, o motorista escolhe um item real do XML, vê a quantidade total e informa a quantidade física recebida. Cliente, NF, cidade, vendedor, produtos, motorista e placa são aproveitados dos dados já validados. O aplicativo prepara o texto no padrão da Conrado para revisão, cópia ou compartilhamento manual; nenhum envio ao WhatsApp é automático.

Depois de uma baixa confirmada ou preservada na fila offline, a NF-e sai da rota ativa. Somente um Administrador pode estorná-la, com motivo obrigatório; a entrega volta para o motorista sem apagar o evento anterior, a fotografia, os horários ou a trilha de auditoria.

O painel administrativo também consolida a produtividade por motorista: viagens totais, em rota e encerradas; NF-e entregues, pendentes e com ocorrência; e percentual de entrega concluída. Recusas e ocorrências não são contabilizadas como entregas concluídas; registros parciais antigos permanecem identificados apenas no histórico.

Novos acessos de motorista usam PIN individual de exatamente cinco dígitos; sequências óbvias e números repetidos são recusados. Durante a migração, credenciais antigas continuam válidas somente até a redefinição individual. Após cinco tentativas inválidas em cinco minutos, o acesso do motorista fica bloqueado por quinze minutos. O limitador persiste entre reinícios e guarda somente hashes de IP e placa. A área móvel possui saída explícita, que revoga a sessão no servidor. As respostas incluem proteção contra enquadramento, política de referência restrita e permissões mínimas de câmera. A gestão possui login individual, sessão de oito horas, logout e autorização por perfil. Esses controles ajudam o piloto, mas ainda não substituem HTTPS na borda, monitoramento e homologação presencial.

O painel administrativo permite abrir uma evidência por acesso temporário de no
máximo cinco minutos; o bucket permanece privado. O servidor também expõe
`/healthz` e `/readyz`, aceita domínio externo somente quando uma origem HTTPS
exata é configurada e devolve um identificador de requisição em erros internos,
sem incluir detalhes de conexão. A preparação de hospedagem e rollback está em
[`docs/runbook-hospedagem.md`](docs/runbook-hospedagem.md).

Administrador e Auditor também podem consultar um diagnóstico agregado e exportar
auditoria; todos os perfis autenticados podem exportar ocorrências. Os CSVs são
gerados sob demanda, neutralizam fórmulas e não permanecem no servidor. Com logs
habilitados, a aplicação escreve JSON com rota normalizada, status, duração e
`request_id`, sem corpo, token ou dados do cliente.

A gestão possui autenticação administrativa individual com sessão de oito horas,
logout e revogação após redefinição da senha. A chave administrativa continua
disponível somente no modo local de transição e é desabilitada automaticamente
quando o servidor usa `0.0.0.0`. Nenhuma conta é criada automaticamente; a
preparação manual usa `python -m conrado.admin_user NOME_DO_USUARIO`, com senha
oculta no terminal.

## Backup cifrado

`python -m conrado.backup create CAMINHO` produz um pacote cifrado com o dump do
banco, as evidências privadas e um manifesto de hashes. `verify` confere o
pacote; `restore` somente aceita um projeto de recuperação vazio e sua referência
exata. O procedimento completo está em
[`docs/backup-restauracao.md`](docs/backup-restauracao.md); o roteiro móvel está
em [`docs/pwa-offline.md`](docs/pwa-offline.md).

## Retenção, manuais e homologação

A política automática permanece desativada até aprovação dos prazos. Use
`python -m conrado.maintenance preview` para listar somente contagens e consulte
[`docs/seguranca-operacional.md`](docs/seguranca-operacional.md). O roteiro de
aceite está em [`docs/checklist-homologacao.md`](docs/checklist-homologacao.md),
com recuperação em [`docs/rollback-recuperacao.md`](docs/rollback-recuperacao.md).
Há manuais rápidos em [`docs/manual-motorista.md`](docs/manual-motorista.md) e
[`docs/manual-gestor.md`](docs/manual-gestor.md). As notas da versão estão no
[`CHANGELOG.md`](CHANGELOG.md).
