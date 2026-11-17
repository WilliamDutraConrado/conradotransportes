"""Prévia local, somente leitura, de NF-e (sem armazenar dados)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}
MAX_BYTES = 3_000_000


class NFeError(ValueError):
    """XML inválido ou incompatível com NF-e."""


def _value(parent: ET.Element, xpath: str) -> str:
    return (parent.findtext(xpath, default="", namespaces=NS) or "").strip()


def _seller(inf: ET.Element, observation: str) -> str:
    """Extrai vendedor quando a NF-e o informa em campo livre, sem inventar valor."""
    for field in inf.findall("n:infAdic/n:obsCont", NS):
        label = (field.get("xCampo") or "").strip()
        if re.search(r"(?i)\b(?:vendedor|vendedora|representante)\b", label):
            value = _value(field, "n:xTexto")
            if value:
                return value[:100]
    match = re.search(
        r"(?i)\b(?:vendedor|vendedora|representante)\s*[:#-]?\s*"
        r"([A-ZÀ-ÖØ-öø-ÿ][A-ZÀ-ÖØ-öø-ÿ .'-]{1,99}?)"
        r"(?=\s+(?:placa|embarque|pedido|rota|carga)\b|[;|\r\n]|$)",
        observation,
    )
    return match.group(1).strip() if match else ""


def parse_nfe(data: bytes, placa_regex: str) -> dict:
    """Extrai campos necessários ao planejamento sem confiar em XML remoto."""
    if len(data) > MAX_BYTES:
        raise NFeError("Arquivo XML excede o tamanho permitido")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise NFeError("Declarações DTD/ENTITY não são permitidas")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NFeError("XML malformado") from exc

    inf = root.find(".//n:infNFe", NS)
    if inf is None:
        raise NFeError("Documento não contém infNFe no namespace oficial")

    chave = inf.get("Id", "")
    if not re.fullmatch(r"NFe\d{44}", chave):
        raise NFeError("Chave da NF-e ausente ou inválida")

    endereco = inf.find("n:dest/n:enderDest", NS)
    if endereco is None:
        raise NFeError("Endereço do destinatário ausente")

    observacao = " ".join(filter(None, (
        _value(inf, "n:infAdic/n:infCpl"),
        _value(inf, "n:infAdic/n:infAdFisco"),
    )))
    match = re.search(placa_regex, observacao, re.IGNORECASE)
    placa = match.group(1).upper() if match else None
    if placa and not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", placa):
        raise NFeError("Placa extraída não corresponde ao padrão esperado")

    cnpj = _value(inf, "n:dest/n:CNPJ") or _value(inf, "n:dest/n:CPF")
    if not cnpj:
        raise NFeError("Destinatário não identificado")

    endereco_partes = [
        _value(endereco, f"n:{campo}")
        for campo in ("xLgr", "nro", "xCpl", "xBairro", "xMun", "UF", "CEP")
    ]
    items = []
    for detail in inf.findall("n:det", NS):
        product = detail.find("n:prod", NS)
        if product is None:
            continue
        code = _value(product, "n:cProd")
        description = _value(product, "n:xProd")
        quantity = _value(product, "n:qCom")
        unit = _value(product, "n:uCom")
        if code or description:
            items.append({
                "codigo": code[:60],
                "descricao": description[:200],
                "quantidade": quantity[:30],
                "unidade": unit[:20],
            })
    return {
        "chave": chave[3:],
        "numero": _value(inf, "n:ide/n:nNF"),
        "destinatario_id": cnpj,
        "destinatario": _value(inf, "n:dest/n:xNome"),
        "endereco": ", ".join(x for x in endereco_partes if x),
        "cidade": _value(endereco, "n:xMun"),
        "vendedor": _seller(inf, observacao),
        "itens": items,
        "placa": placa,
        "peso_bruto_kg": sum(float(_value(vol, "n:pesoB") or 0) for vol in inf.findall("n:transp/n:vol", NS)),
        "volumes": sum(int(_value(vol, "n:qVol") or 0) for vol in inf.findall("n:transp/n:vol", NS)),
    }


def preview(paths: list[Path], placa_regex: str) -> dict:
    # Valida o regex uma vez antes de abrir os arquivos.
    try:
        compiled = re.compile(placa_regex)
    except re.error as exc:
        raise NFeError("Expressão de placa inválida") from exc
    if compiled.groups != 1:
        raise NFeError("A expressão de placa deve possuir exatamente um grupo de captura")

    notas: dict[str, dict] = {}
    erros = []
    for path in paths:
        try:
            nota = parse_nfe(path.read_bytes(), placa_regex)
            if nota["chave"] in notas:
                erros.append({"arquivo": path.name, "erro": "NF-e duplicada no lote"})
            else:
                notas[nota["chave"]] = nota
        except (OSError, NFeError, ValueError) as exc:
            erros.append({"arquivo": path.name, "erro": str(exc)})

    grupos = defaultdict(list)
    for nota in notas.values():
        grupos[nota["placa"] or "SEM_PLACA"].append(nota)
    return {"total_notas": len(notas), "placas": dict(sorted(grupos.items())), "erros": erros}


def main() -> int:
    cli = argparse.ArgumentParser(description="Prévia local de XML de NF-e por placa")
    cli.add_argument("pasta", type=Path)
    cli.add_argument("--placa-regex", required=True)
    args = cli.parse_args()
    if not args.pasta.is_dir():
        cli.error("Informe uma pasta existente com XMLs")
    try:
        resultado = preview(sorted(args.pasta.glob("*.xml")), args.placa_regex)
    except NFeError as exc:
        cli.error(str(exc))
    json.dump(resultado, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if not resultado["erros"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
