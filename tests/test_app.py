from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app import BridgeService, VkBrowserPoster


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> dict:
        return self.payload


class SequenceClient:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RichMessageTests(unittest.TestCase):
    def test_rich_message_becomes_plain_text_with_links(self) -> None:
        post = {
            "rich_message": {
                "blocks": [
                    {"type": "heading", "text": "Заголовок", "size": 1},
                    {
                        "type": "paragraph",
                        "text": [
                            "Читайте ",
                            {
                                "type": "url",
                                "text": {"type": "bold", "text": "подробности"},
                                "url": "https://example.test/article",
                            },
                            ".",
                        ],
                    },
                    {
                        "type": "list",
                        "items": [
                            {
                                "label": "•",
                                "blocks": [{"type": "paragraph", "text": "Первый пункт"}],
                            },
                            {
                                "label": "•",
                                "blocks": [{"type": "paragraph", "text": "Второй пункт"}],
                            },
                        ],
                    },
                    {
                        "type": "photo",
                        "photo": [{"file_id": "photo-1", "width": 100, "height": 100}],
                        "caption": {"text": "Подпись", "credit": "Автор"},
                    },
                ]
            }
        }

        text, entities = BridgeService.extract_text_and_entities(post)

        self.assertEqual(entities, [])
        self.assertEqual(
            text,
            "Заголовок\n\n"
            "Читайте подробности (https://example.test/article).\n\n"
            "• Первый пункт\n"
            "• Второй пункт\n\n"
            "Подпись\n— Автор",
        )

    def test_rich_media_is_recursive_and_deduplicated(self) -> None:
        post = {
            "rich_message": {
                "blocks": [
                    {
                        "type": "photo",
                        "photo": [
                            {"file_id": "small", "file_size": 10, "width": 90, "height": 90},
                            {"file_id": "large", "file_size": 100, "width": 800, "height": 800},
                        ],
                    },
                    {
                        "type": "collage",
                        "blocks": [
                            {
                                "type": "photo",
                                "photo": [{"file_id": "large", "width": 800, "height": 800}],
                            },
                            {
                                "type": "video",
                                "video": {
                                    "file_id": "video-1",
                                    "file_name": "clip.mp4",
                                    "mime_type": "video/mp4",
                                },
                            },
                        ],
                    },
                ]
            }
        }

        media = BridgeService.extract_media_items(post)

        self.assertEqual(
            media,
            [
                ("large", "photo", "photo.jpg", "image/jpeg"),
                ("video-1", "video", "clip.mp4", "video/mp4"),
            ],
        )

    def test_rich_messages_can_bypass_hashtag_filter(self) -> None:
        service = BridgeService.__new__(BridgeService)
        service.settings = SimpleNamespace(
            repost_all_posts=False,
            repost_rich_messages=True,
        )

        self.assertTrue(service.should_repost_post({"rich_message": {"blocks": []}}, "без хештега"))
        self.assertFalse(service.should_repost_post({}, "без хештега"))


class VkResilienceTests(unittest.TestCase):
    def test_worker_error_prefers_structured_stdout(self) -> None:
        error = BridgeService.extract_vk_worker_error(
            stdout=b'{"ok": false, "error": "VK API wall.post failed: code=10"}',
            stderr=b"noisy http logs",
        )

        self.assertEqual(error, "VK API wall.post failed: code=10")

    @patch("app.time.sleep", return_value=None)
    def test_vk_api_call_retries_transport_error(self, _sleep) -> None:
        request = httpx.Request("POST", "https://api.vk.com/method/wall.post")
        client = SequenceClient(
            [
                httpx.ConnectError("temporary", request=request),
                FakeResponse({"response": {"post_id": 42}}),
            ]
        )
        poster = VkBrowserPoster.__new__(VkBrowserPoster)

        result = poster.vk_api_call(client, "wall.post", "token", owner_id=-1)

        self.assertEqual(result, {"post_id": 42})
        self.assertEqual(client.calls, 2)

    @patch("app.time.sleep", return_value=None)
    def test_media_upload_reopens_file_for_retry(self, _sleep) -> None:
        request = httpx.Request("POST", "https://upload.test")
        client = SequenceClient(
            [
                httpx.ReadTimeout("temporary", request=request),
                FakeResponse({"server": 1, "photo": "[]", "hash": "ok"}),
                FakeResponse({"server": 1, "photo": '[{"id":1}]', "hash": "ok"}),
            ]
        )
        poster = VkBrowserPoster.__new__(VkBrowserPoster)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "photo.jpg"
            path.write_bytes(b"image")
            result = poster.upload_file_with_retries(
                client=client,
                upload_url="https://upload.test",
                field_name="photo",
                path=path,
                mime_type="image/jpeg",
                required_fields=("photo", "server", "hash"),
            )

        self.assertEqual(result["photo"], "[{\"id\":1}]")
        self.assertEqual(client.calls, 3)


if __name__ == "__main__":
    unittest.main()
