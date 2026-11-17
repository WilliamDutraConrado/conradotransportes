import json
import unittest

from conrado.supabase_storage import StorageError, SupabaseStorage, detect_image


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class SupabaseStorageTest(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def fake_open(req, timeout):
            self.requests.append(req)
            if "/sign/" in req.full_url:
                return Response({"signedURL": "/storage/v1/object/sign/canhotos/test?token=temporary"})
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

    def test_rejeita_mime_falso_e_caminho_arbitrario(self):
        with self.assertRaises(StorageError):
            detect_image(b"<script>" * 30)
        with self.assertRaises(StorageError):
            self.storage.signed_download_url("../another-company/secret.jpg")

    def test_url_de_curta_duracao(self):
        image = b"\xff\xd8\xff" + b"test-data" * 15 + b"\xff\xd9"
        result = self.storage.upload(3, 9, "evento-1234567890", image)
        signed = self.storage.signed_download_url(result.path)
        self.assertIn("token=temporary", signed)
        self.assertIn('"expiresIn": 300', self.requests[-1].data.decode())


if __name__ == "__main__":
    unittest.main()
