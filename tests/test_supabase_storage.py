import json
import unittest
import urllib.error

from conrado.supabase_storage import StorageError, SupabaseStorage, detect_image


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _size=-1):
        return self.body


class SupabaseStorageTest(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def fake_open(req, timeout):
            self.requests.append(req)
            if req.get_method() == "DELETE":
                return Response([])
            if "/object/list/" in req.full_url:
                return Response([])
            if "/sign/" in req.full_url:
                return Response({"signedURL": "/object/sign/canhotos/test?token=temporary"})
            return Response({"Key": "ok"})

        self.storage = SupabaseStorage("https://example.supabase.co", "test-service-key", opener=fake_open)

    def test_upload_privado_e_hash(self):
        image = b"\xff\xd8\xff" + b"test-data" * 15 + b"\xff\xd9"
        result = self.storage.upload(3, 9, "evento-1234567890", image)
        self.assertEqual(result.mime, "image/jpeg")
        self.assertEqual(result.size, len(image))
        self.assertTrue(result.path.startswith("viagens/3/notas/9/"))
        request = self.requests[0]
        self.assertEqual(request.get_header("Content-type"), "image/jpeg")
        self.assertNotIn("test-service-key", request.full_url)

    def test_upload_recupera_resposta_perdida_validando_hash_privado(self):
        image = b"\xff\xd8\xff" + b"network-recovery" * 12 + b"\xff\xd9"
        requests = []

        def uncertain_open(req, timeout):
            requests.append(req)
            if req.get_method() == "POST":
                raise urllib.error.URLError("resposta perdida")
            response = Response({})
            response.body = image
            return response

        storage = SupabaseStorage("https://example.supabase.co", "test-service-key", opener=uncertain_open)

        result = storage.upload(3, 9, "evento-rede-123456", image)

        self.assertEqual(result.sha256, __import__("hashlib").sha256(image).hexdigest())
        self.assertEqual([request.get_method() for request in requests], ["POST", "GET"])
        self.assertIn("/object/authenticated/canhotos/", requests[1].full_url)

    def test_upload_nao_recupera_objeto_com_hash_divergente(self):
        image = b"\xff\xd8\xff" + b"expected-content" * 12 + b"\xff\xd9"
        other = b"\xff\xd8\xff" + b"different-content" * 12 + b"\xff\xd9"

        def mismatched_open(req, timeout):
            if req.get_method() == "POST":
                raise urllib.error.URLError("resposta perdida")
            response = Response({})
            response.body = other
            return response

        storage = SupabaseStorage("https://example.supabase.co", "test-service-key", opener=mismatched_open)

        with self.assertRaises(StorageError):
            storage.upload(3, 9, "evento-rede-654321", image)

    def test_rejeita_mime_falso_e_caminho_arbitrario(self):
        with self.assertRaises(StorageError):
            detect_image(b"<script>" * 30)
        with self.assertRaises(StorageError):
            self.storage.signed_download_url("../another-company/secret.jpg")

    def test_url_de_curta_duracao(self):
        image = b"\xff\xd8\xff" + b"test-data" * 15 + b"\xff\xd9"
        result = self.storage.upload(3, 9, "evento-1234567890", image)
        signed = self.storage.signed_download_url(result.path)
        self.assertTrue(signed.startswith("https://example.supabase.co/storage/v1/object/sign/"))
        self.assertIn("token=temporary", signed)
        self.assertIn('"expiresIn": 300', self.requests[-1].data.decode())

    def test_exclusao_usa_api_do_storage_e_caminho_exato(self):
        image = b"\xff\xd8\xff" + b"test-data" * 15 + b"\xff\xd9"
        result = self.storage.upload(3, 9, "evento-1234567890", image)

        self.storage.delete(result.path)

        request = self.requests[-1]
        self.assertEqual(request.get_method(), "DELETE")
        self.assertEqual(json.loads(request.data), {"prefixes": [result.path]})
        with self.assertRaises(StorageError):
            self.storage.delete("../outro-bucket/arquivo.jpg")

    def test_exclusao_aceita_resposta_json_de_sucesso(self):
        def fake_open(req, timeout):
            return Response({"message": "Successfully deleted"})

        storage = SupabaseStorage("https://example.supabase.co", "test-service-key", opener=fake_open)
        storage.delete("viagens/3/notas/9/evento-1234567890-abcdef012345.jpg")

    def test_consulta_existencia_por_caminho_exato(self):
        path = "viagens/3/notas/9/evento-1234567890-abcdef012345.jpg"
        self.assertFalse(self.storage.exists(path))
        request = self.requests[-1]
        body = json.loads(request.data)
        self.assertEqual(body["prefix"], "viagens/3/notas/9")
        self.assertEqual(body["search"], "evento-1234567890-abcdef012345.jpg")

    def test_backup_baixa_objeto_autenticado_e_restaura_hash_exato(self):
        path = "viagens/3/notas/9/evento-1234567890-abcdef012345.jpg"
        image = b"\xff\xd8\xff" + b"backup-data" * 15 + b"\xff\xd9"

        def fake_open(req, timeout):
            self.requests.append(req)
            if req.get_method() == "GET":
                response = Response({})
                response.body = image
                return response
            return Response({"Key": "ok"})

        storage = SupabaseStorage("https://example.supabase.co", "test-service-key", opener=fake_open)
        downloaded = storage.download(path)
        storage.restore(path, downloaded, __import__("hashlib").sha256(image).hexdigest())

        self.assertEqual(downloaded, image)
        self.assertIn("/object/authenticated/canhotos/", self.requests[0].full_url)
        self.assertEqual(self.requests[1].get_header("X-upsert"), "false")
        with self.assertRaises(StorageError):
            storage.restore(path, downloaded, "0" * 64)


if __name__ == "__main__":
    unittest.main()
