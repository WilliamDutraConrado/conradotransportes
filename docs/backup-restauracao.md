# Backup e restauração de recuperação

A rotina protege, no mesmo pacote, o dump do PostgreSQL e todas as evidências
referenciadas pela tabela `app.evidence`. O pacote é cifrado antes de sair da
pasta temporária e cada arquivo possui SHA-256 no manifesto.

## Requisitos

- `pg_dump` e `pg_restore` da mesma versão principal ou mais recente que o
  PostgreSQL de origem. O `Dockerfile` do projeto já instala esses utilitários.
- `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` e
  `SUPABASE_PROJECT_REF` configurados no ambiente seguro.
- Destino fora do repositório, preferencialmente em unidade cifrada e com cópia
  adicional fora do provedor principal.
- Senha exclusiva de pelo menos 16 caracteres. A senha não é armazenada e não
  existe recuperação automática.

## Criar e conferir

```bash
python -m conrado.backup create /destino-seguro/conrado-AAAA-MM-DD.backup
python -m conrado.backup verify /destino-seguro/conrado-AAAA-MM-DD.backup
```

Os comandos solicitam a senha de forma oculta. O arquivo nunca é sobrescrito.
A URL do banco não aparece na linha de comando do `pg_dump`; as credenciais são
repassadas somente no ambiente do processo filho.

O limite desta rotina de piloto é 512 MB por pacote. Ao se aproximar desse
volume, migrar para backup de objetos em escala e retenção automatizada.

## Ensaio de restauração

Nunca restaurar por cima do projeto de desenvolvimento ou produção. Criar um
projeto de recuperação vazio, configurar seus segredos e confirmar literalmente
a referência desse projeto:

```bash
python -m conrado.backup restore /destino-seguro/conrado-AAAA-MM-DD.backup \
  --confirm-project-ref REFERENCIA_EXATA_DO_PROJETO_VAZIO
```

A rotina interrompe a restauração quando:

- a referência não coincide com `SUPABASE_PROJECT_REF`;
- o banco de destino contém qualquer registro operacional;
- a senha, o manifesto ou algum hash está incorreto;
- uma evidência já existe, falha no envio ou não pode ser confirmada.

Após o ensaio, executar `conrado.preflight`, `conrado.ops_audit` e um fluxo E2E
somente com dados sintéticos. Registrar data, responsável, duração, versão do
backup e resultado sem copiar nomes, placas, XMLs ou caminhos de evidência.

## Retenção sugerida para homologação

Até a política definitiva ser aprovada, manter no máximo três backups de teste,
sem dados reais, e excluir o projeto de recuperação após o ensaio. A retenção de
XMLs e canhotos reais depende de decisão operacional e jurídica antes do piloto.
