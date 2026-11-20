# Manual rápido do gestor

## Perfis

- **Administrador:** usuários, senhas, operação, diagnóstico e auditoria.
- **Operador:** motoristas, viagens, XMLs e ocorrências; não gerencia senhas administrativas.
- **Auditor:** consulta, diagnóstico e exportações; não altera a operação.

## Preparar uma viagem

1. Cadastre motorista, placa e tipo do veículo.
2. Crie a viagem em rascunho.
3. Importe os XMLs e confira placa, NF-e, cliente, peso e quantidade.
4. Libere a viagem somente após a conferência.

XML malformado, entidade externa, duplicidade ou placa divergente bloqueia o lote inteiro; corrija a origem e importe novamente.

## Acompanhar

Use o resumo, produtividade e **Ver notas**. Entrega, recusa e ocorrência permanecem separadas; “parcial” aparece apenas em registros antigos. O histórico mostra início, fim, duração, localização pontual, observações e evidências. Fotografias usam acesso temporário e não devem ser copiadas para pastas públicas.

Para corrigir uma baixa, abra **Ver notas → Estornar baixa** e informe um motivo objetivo. Essa ação é exclusiva do Administrador: a NF-e retorna à rota do motorista, mas o evento anterior, a foto e o estorno permanecem no histórico. Não use estorno para apagar evidências.

## Relatórios e diagnóstico

- **Baixar ocorrências:** relatório operacional autenticado.
- **Baixar auditoria:** somente Administrador e Auditor.
- **Diagnóstico:** versão, contagens técnicas, sessões vencidas e evidências órfãs, sem clientes ou credenciais.

## Segurança

Cada pessoa deve usar sua própria conta. Ao desligar alguém, desative a conta; as sessões são revogadas. Nunca envie senhas, chaves do Supabase ou backups por mensagem.
