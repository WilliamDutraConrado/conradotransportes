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
