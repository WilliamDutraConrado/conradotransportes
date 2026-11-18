# Checklist de homologação

Registrar responsável, data e evidência para cada item. Não usar documentos reais antes da aprovação completa.

## Infraestrutura

- [ ] Domínio HTTPS de homologação configurado.
- [ ] Variáveis verificadas com `python -m conrado.release_check`.
- [ ] Migrações `003` a `006` aplicadas e preflight aprovado.
- [ ] Chave legada substituída por `SUPABASE_SECRET_KEY`.
- [ ] Token administrativo estático desativado externamente.
- [ ] Logs estruturados e healthcheck `/readyz` visíveis no provedor.

## Perfis

- [ ] Administrador cria contas, redefine senhas e exporta auditoria.
- [ ] Operador cadastra motoristas/viagens e não acessa senhas nem auditoria.
- [ ] Auditor consulta operação, diagnóstico e auditoria sem alterar dados.
- [ ] Sessões expiram e logout revoga o acesso.

## Motorista e PWA

- [ ] Instalação Android pelo navegador.
- [ ] Instalação iPhone pelo Safari e “Adicionar à Tela de Início”.
- [ ] Câmera aceita JPEG/PNG e rejeita arquivo inválido.
- [ ] Rota abre online, em modo avião e após fechar/reabrir o app.
- [ ] Baixa offline aparece como pendente e sincroniza uma única vez.
- [ ] Sessão expirada preserva a fila e exige novo login.
- [ ] Atualização do app só reinicia após confirmação do motorista.

## Falhas e recuperação

- [ ] Reenvio duplicado retorna sucesso sem evento duplicado.
- [ ] Queda durante upload não deixa canhoto órfão.
- [ ] XML inválido não causa importação parcial.
- [ ] Backup cifrado criado e verificado.
- [ ] Restauração ensaiada em projeto vazio, nunca sobre a base ativa.
- [ ] Rollback do aplicativo testado conforme `rollback-recuperacao.md`.

## Aprovação

- [ ] Gestor operacional aprovou mensagens e fluxo.
- [ ] Responsável por privacidade aprovou retenção de canhotos e auditoria.
- [ ] RPO e RTO foram definidos.
- [ ] Piloto autorizado com quantidade limitada de motoristas e viagens.
