import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from conrado.importer import NFeError, parse_nfe, preview


REGEX = r"PLACA[: ]+([A-Z]{3}[0-9][A-Z0-9][0-9]{2})"


def xml(chave: str, placa: str = "ABC1D23") -> bytes:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe{chave}">
  <ide><nNF>123</nNF></ide>
  <dest><CNPJ>12345678000190</CNPJ><xNome>Cliente Teste</xNome>
    <enderDest><xLgr>Rua Teste</xLgr><nro>10</nro><xMun>Divinópolis</xMun><UF>MG</UF></enderDest>
  </dest><transp><vol><qVol>2</qVol><pesoB>10.5</pesoB></vol>
  <vol><qVol>1</qVol><pesoB>5.25</pesoB></vol></transp>
  <infAdic><infCpl>VEICULO PLACA: {placa}</infCpl></infAdic>
</infNFe></NFe></nfeProc>'''.encode()


class ImportadorTest(unittest.TestCase):
    def test_extrai_identidade_placa_peso_e_volumes(self):
        nota = parse_nfe(xml("1" * 44), REGEX)
        self.assertEqual(nota["placa"], "ABC1D23")
        self.assertEqual(nota["numero"], "123")
        self.assertEqual(nota["peso_bruto_kg"], 15.75)
        self.assertEqual(nota["volumes"], 3)

    def test_duplicata_e_placa_ausente(self):
        with TemporaryDirectory() as pasta:
            d = Path(pasta)
            (d / "primeira.xml").write_bytes(xml("1" * 44))
            (d / "duplicada.xml").write_bytes(xml("1" * 44))
            (d / "sem_placa.xml").write_bytes(xml("2" * 44, ""))
            resultado = preview(list(d.glob("*.xml")), REGEX)
            self.assertEqual(resultado["total_notas"], 2)
            self.assertIn("SEM_PLACA", resultado["placas"])
            self.assertEqual(len(resultado["erros"]), 1)

    def test_rejeita_entidades_xml(self):
        with self.assertRaises(NFeError):
            parse_nfe(b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><foo/>', REGEX)

    def test_rejeita_chave_invalida(self):
        with self.assertRaises(NFeError):
            parse_nfe(xml("1" * 43), REGEX)


if __name__ == "__main__":
    unittest.main()
