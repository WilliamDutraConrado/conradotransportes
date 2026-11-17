# Conrado Entregas

Projeto-piloto de gestão de entregas para a Conrado Transportes. Desenvolvimento iniciado em novembro de 2026.

Escopo: importação manual de XML de NF-e, viagens vinculadas a placas, controle individual de notas e canhotos, ocorrências, painel administrativo e aplicativo Android offline.

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

## Estrutura de dados (em desenvolvimento)

O módulo `conrado/store.py` implementa cadastro de motoristas/veículos, viagens em rascunho, importação transacional de notas e liberação de uma viagem por placa. Ele usa SQLite local, mantém um registro de eventos administrativos e está ligado à interface experimental de gestão. Nenhum motorista ou placa real vem pré-cadastrado. Não utilize essa estrutura para dados operacionais reais até concluirmos autenticação individual, autorização, backups e gestão de evidências.

## Gestão de viagens em teste

Agora o módulo de dados também possui interface própria, protegida por chave administrativa:

```bash
export CONRADO_ADMIN_TOKEN='substitua-por-uma-chave-aleatoria-com-mais-de-20-caracteres'
python3 -m conrado.gestao
```

Abra `http://127.0.0.1:8766` no **mesmo computador**. Insira a chave, cadastre motoristas e veículos, crie rascunhos de viagens, importe os XMLs correspondentes e libere a viagem após conferência. Dados são salvos em `conrado_local.sqlite` local (ignorado pelo Git). **Não use esta versão em produção:** a chave única administrativa não substitui a gestão completa de usuários e permissões, não há HTTPS externo nem backup automatizado.

## Área móvel experimental

No mesmo servidor de gestão, acesse `http://127.0.0.1:8766/motorista`. O administrador pode definir senhas individuais no cadastro; o motorista entra com placa e senha e visualiza somente a viagem liberada para ele. É possível registrar ocorrências com motivo e histórico; para baixar uma nota como **entregue**, a fotografia é obrigatória. A viagem só encerra se nenhuma nota permanecer pendente. Fotos são armazenadas no SQLite local **sem criptografia em repouso** nesta POC, por isso não use imagens reais de clientes antes de concluirmos a segurança. Esta tela ainda não funciona offline e **não é um APK**.

Nas ocorrências, cliente, NF, cidade, vendedor (quando informado), produtos, motorista e placa são aproveitados do XML e dos cadastros. O horário é sugerido pelo celular e pode ser corrigido. O motorista seleciona o produto, informa a quantidade afetada e descreve o motivo. O aplicativo prepara o texto no padrão da Conrado para revisão, cópia ou compartilhamento manual; nenhum envio ao WhatsApp é automático.
