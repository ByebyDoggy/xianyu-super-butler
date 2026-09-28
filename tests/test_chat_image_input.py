"""买家发来的图片要真的送到模型，而不是只送 "[图片]" 两个字。

回归的故障：买家发图，`handle_message` 取 `message['1']['10']['reminderContent']`
拿到的是固定字符串 "[图片]"，图片本体在 `message['1']['6']['3']['5']` 里，
全程没人解析 —— 于是模型只能看到 "[图片]"，答非所问。

覆盖三件事：
1. `_extract_image_urls` 能从真实推送帧里抠出图片 URL，且绝不乱认数字；
2. 有图时最后一条 user 消息变成多模态 content part 数组，没图时完全不变；
3. 只吃纯文本的分支（DashScope / Gemini）和前端提示词预览不会因为数组而崩。
"""

import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from XianyuAutoAsync import XianyuLive
from app.ai_reply_engine import AIReplyEngine
from app.db_manager import db_manager

# 真实推送帧里的 content 字段（截图实测抓下来的形态）
REAL_IMAGE_CONTENT = json.dumps({
    "atUsers": [],
    "contentType": 2,
    "image": {
        "pics": [{
            "height": 1080,
            "type": 0,
            "url": "https://img.alicdn.com/imgextra/i1/2218289862997/O1CN01l7R6BVm6WBB2vGwq_!!2218289862997-0-xy_chat.jpg",
            "width": 1440,
        }]
    },
}, ensure_ascii=False)

IMG1 = "https://img.alicdn.com/a-xy_chat.jpg"
IMG2 = "https://img.alicdn.com/b-xy_chat.jpg"


def _image_message(content: str = REAL_IMAGE_CONTENT):
    """按线上真实的帧结构造一条图片消息。"""
    return {
        "1": {
            "1": {"1": "2218289862997@goofish"},
            "2": "67160542982@goofish",
            "3": "4320984567861.PNM",
            "4": 0,
            "5": 1790561731431,
            "6": {"1": 101, "3": {"1": "", "2": "[图片]", "3": "", "4": 2, "5": content}},
            "10": {
                "reminderContent": "[图片]",
                "senderUserId": "2218289862997",
                "bizTag": json.dumps({"sourceId": "S:1", "messageId": "e3db3f54777e4fe792e96e5d571bc4e9"}),
            },
        }
    }


def _text_message(text: str = "你好"):
    content = json.dumps(
        {"atUsers": [], "contentType": 1, "text": {"text": text}},
        ensure_ascii=False,
    )
    return {
        "1": {
            "2": "67160542982@goofish",
            "6": {"3": {"2": text, "4": 1, "5": content}},
            "10": {"reminderContent": text, "senderUserId": "2218289862997"},
        }
    }


def _extractor():
    """只需 cookie_id，构造一个不跑 __init__ 的实例。"""
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "test-account"
    return live


class ExtractImageUrlsTests(unittest.TestCase):
    def setUp(self):
        self.live = _extractor()

    def test_real_frame_yields_image_url(self):
        urls = self.live._extract_image_urls(_image_message())
        self.assertEqual(
            urls,
            ["https://img.alicdn.com/imgextra/i1/2218289862997/"
             "O1CN01l7R6BVm6WBB2vGwq_!!2218289862997-0-xy_chat.jpg"],
        )

    def test_text_message_has_no_image(self):
        self.assertEqual(self.live._extract_image_urls(_text_message()), [])

    def test_voice_message_has_no_image(self):
        content = json.dumps({"contentType": 3, "audio": {"url": "x"}})
        self.assertEqual(self.live._extract_image_urls(_image_message(content)), [])

    def test_base64_wrapped_payload(self):
        import base64

        wrapped = base64.b64encode(REAL_IMAGE_CONTENT.encode("utf-8")).decode("ascii")
        urls = self.live._extract_image_urls(_image_message(wrapped))
        self.assertTrue(urls and urls[0].startswith("https://img.alicdn.com/"))

    def test_legacy_pic_url_is_accepted(self):
        content = json.dumps({"contentType": 2, "picUrl": IMG1})
        self.assertEqual(self.live._extract_image_urls(_image_message(content)), [IMG1])

    def test_multiple_pics_dedup_and_order_kept(self):
        content = json.dumps({
            "contentType": 2,
            "image": {"pics": [{"url": IMG1}, {"url": IMG2}, {"url": IMG1}, {"no_url": 1}]},
        })
        self.assertEqual(self.live._extract_image_urls(_image_message(content)), [IMG1, IMG2])

    def test_id_like_numbers_are_never_treated_as_image(self):
        # 会话 ID / 商品 ID / 订单号一律不能当图片地址
        content = json.dumps({"contentType": 2, "image": {"pics": [{"url": ""}]}})
        self.assertEqual(self.live._extract_image_urls(_image_message(content)), [])

    def test_malformed_inputs_return_empty(self):
        for bad in (
            None,
            "not-a-dict",
            {},
            {"1": "67160542982@goofish"},
            {"1": {"6": "not-a-dict"}},
            {"1": {"6": {"3": {}}}},
            {"1": {"6": {"3": {"5": "not json at all {"}}}},
        ):
            with self.subTest(bad=bad):
                self.assertEqual(self.live._extract_image_urls(bad), [])


class MultimodalContentTests(unittest.TestCase):
    def test_without_images_content_stays_a_plain_string(self):
        content = AIReplyEngine._build_user_content("[图片]")
        self.assertIsInstance(content, str)
        self.assertEqual(content, "[图片]")

    def test_blank_image_urls_are_ignored(self):
        for images in (None, [], [""], ["   "], [None]):
            with self.subTest(images=images):
                self.assertIsInstance(AIReplyEngine._build_user_content("你好", images), str)

    def test_with_images_content_becomes_parts(self):
        content = AIReplyEngine._build_user_content("[图片]", [IMG1, IMG2])
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("[图片]", content[0]["text"])
        self.assertEqual(
            [p["image_url"]["url"] for p in content[1:]],
            [IMG1, IMG2],
        )
        self.assertTrue(all(p["type"] == "image_url" for p in content[1:]))

    def test_content_to_text_flattens_parts(self):
        content = AIReplyEngine._build_user_content("[图片]", [IMG1])
        text = AIReplyEngine._content_to_text(content)
        self.assertIn("[图片]", text)
        self.assertIn(IMG1, text)

    def test_content_to_text_hides_base64_body(self):
        content = [{"type": "text", "text": "看图"},
                   {"type": "image_url", "image_url": {"url": "data:image/webp;base64,AAAA"}}]
        text = AIReplyEngine._content_to_text(content)
        self.assertIn("[图片: (base64)]", text)
        self.assertNotIn("AAAA", text)

    def test_content_to_text_passes_strings_through(self):
        self.assertEqual(AIReplyEngine._content_to_text("你好"), "你好")


class ComposePromptPartsTests(unittest.TestCase):
    SETTINGS = {
        "custom_prompts": json.dumps({"default": "你是一位客服"}, ensure_ascii=False),
        "max_bargain_rounds": 3,
        "max_discount_percent": 10,
        "max_discount_amount": 100,
    }
    ITEM = {"title": "DeepSeek 日卡", "price": "2", "desc": "2元/天"}

    def setUp(self):
        self.engine = AIReplyEngine()

    def _parts(self, images=None):
        return self.engine._compose_prompt_parts(
            settings=self.SETTINGS,
            intent="default",
            item_info=self.ITEM,
            message="[图片]",
            context=[],
            bargain_count=0,
            images=images,
        )

    def test_last_user_message_is_multimodal_when_images_present(self):
        messages = self._parts([IMG1])["messages"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIsInstance(messages[-1]["content"], list)
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"], IMG1)

    def test_last_user_message_unchanged_without_images(self):
        messages = self._parts()["messages"]
        self.assertIsInstance(messages[-1]["content"], str)
        self.assertEqual(messages[-1]["content"], "[图片]")

    def test_system_prompt_is_always_text(self):
        messages = self._parts([IMG1])["messages"]
        self.assertIsInstance(messages[0]["content"], str)


class PromptPreviewTests(unittest.TestCase):
    """预览接口必须永远返回纯文本，否则前端会渲染出 [object Object]。"""

    def setUp(self):
        self.engine = AIReplyEngine()
        self.settings = dict(ComposePromptPartsTests.SETTINGS)
        self.settings["ai_enabled"] = True
        self.settings["context_enabled"] = False

    def test_preview_renders_images_as_text(self):
        with patch("app.ai_reply_engine.db_manager") as db:
            db.get_ai_reply_settings.return_value = self.settings
            db.find_ai_reply_override.return_value = None
            preview = self.engine.build_prompt_preview(
                cookie_id="test-account",
                message="[图片]",
                item_id="",
                chat_id="",
                images=[IMG1],
            )
        self.assertTrue(all(isinstance(m["content"], str) for m in preview["messages"]))
        self.assertIn(IMG1, preview["messages"][-1]["content"])
        self.assertTrue(preview["has_images"])

    def test_preview_without_images_marks_no_images(self):
        with patch("app.ai_reply_engine.db_manager") as db:
            db.get_ai_reply_settings.return_value = self.settings
            db.find_ai_reply_override.return_value = None
            preview = self.engine.build_prompt_preview(
                cookie_id="test-account", message="你好", item_id="", chat_id="",
            )
        self.assertFalse(preview["has_images"])


class TextOnlyProviderGuardTests(unittest.TestCase):
    """DashScope / Gemini 分支只吃纯文本，收到多模态数组不能崩。"""

    def setUp(self):
        self.engine = AIReplyEngine()
        self.messages = [
            {"role": "system", "content": "系统规则"},
            {"role": "user", "content": AIReplyEngine._build_user_content("[图片]", [IMG1])},
        ]

    def test_dashscope_builds_prompt_without_crashing(self):
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"output": {"text": "ok"}}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(json)
            return _Resp()

        settings = {"base_url": "https://dashscope.aliyuncs.com/api/v1/apps/APPID", "api_key": "k"}
        with patch("app.ai_reply_engine.requests.post", fake_post):
            out = self.engine._call_dashscope_api(settings, self.messages)
        self.assertEqual(out, "ok")
        self.assertIn(IMG1, captured["input"]["prompt"])

    def test_gemini_builds_payload_without_crashing(self):
        captured = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(json)
            return _Resp()

        settings = {"model_name": "gemini-2.0-flash", "api_key": "k"}
        with patch("app.ai_reply_engine.requests.post", fake_post):
            out = self.engine._call_gemini_api(settings, self.messages)
        self.assertEqual(out, "ok")
        parts = captured["contents"][-1]["parts"]
        self.assertTrue(all(isinstance(p["text"], str) for p in parts))
        self.assertIn(IMG1, parts[0]["text"])


class StoredImagesParsingTests(unittest.TestCase):
    def test_none_and_blank_return_empty(self):
        for raw in (None, "", "null", "[]", "not json", '{"a":1}', '[1, 2]'):
            with self.subTest(raw=raw):
                self.assertEqual(AIReplyEngine._parse_stored_images(raw), [])

    def test_list_of_urls_is_parsed(self):
        raw = json.dumps([IMG1, IMG2, "  ", None])
        self.assertEqual(AIReplyEngine._parse_stored_images(raw), [IMG1, IMG2])


class ConversationImagesPersistenceTests(unittest.TestCase):
    """买家发的图必须落库，否则下一轮追问时历史里只剩 "[图片]"。"""

    COOKIE = "2218031692538"
    ITEM = "__image_hist_item__"

    def setUp(self):
        self.engine = AIReplyEngine()
        self.chat_id = f"__image_hist_{int(time.time() * 1000)}__"

    def tearDown(self):
        with db_manager.lock:
            db_manager.conn.execute(
                "DELETE FROM ai_conversations WHERE chat_id = ?", (self.chat_id,)
            )
            db_manager.conn.commit()

    def _context(self):
        return self.engine.get_conversation_context(
            self.chat_id, self.COOKIE, item_id=self.ITEM, limit=10, max_age_minutes=10
        )

    def test_images_are_saved_and_restored(self):
        self.engine.save_conversation(
            self.chat_id, self.COOKIE, "buyer", self.ITEM, "user", "[图片]",
            "default", images=[IMG1, IMG2],
        )
        self.engine.save_conversation(
            self.chat_id, self.COOKIE, "buyer", self.ITEM, "assistant", "看到啦",
            "default",
        )
        context = self._context()
        self.assertEqual([m["role"] for m in context], ["user", "assistant"])
        self.assertEqual(context[0]["images"], [IMG1, IMG2])
        self.assertEqual(context[1]["images"], [])

    def test_message_without_images_stores_null(self):
        self.engine.save_conversation(
            self.chat_id, self.COOKIE, "buyer", self.ITEM, "user", "你好", "default",
        )
        self.assertEqual(self._context()[0]["images"], [])
        raw = db_manager.conn.execute(
            "SELECT images FROM ai_conversations WHERE chat_id = ?", (self.chat_id,)
        ).fetchone()[0]
        self.assertIsNone(raw)


class HistoryImageContextTests(unittest.TestCase):
    """图片进历史上下文：最近的优先、有上限、assistant 永不带图。"""

    SETTINGS = {
        "custom_prompts": json.dumps({"default": "你是一位客服"}, ensure_ascii=False),
        "max_bargain_rounds": 3,
        "max_discount_percent": 10,
        "max_discount_amount": 100,
    }
    ITEM = {"title": "DeepSeek 日卡", "price": "2", "desc": "2\u5143/\u5929"}

    def setUp(self):
        self.engine = AIReplyEngine()

    def _parts(self, context, images=None):
        return self.engine._compose_prompt_parts(
            settings=self.SETTINGS, intent="default", item_info=self.ITEM,
            message="\u8fd9\u4e2a\u80fd\u7528\u5417", context=context, bargain_count=0, images=images,
        )

    def _history(self, parts):
        msgs = parts["messages"]
        return msgs[1:-1]  # 去掉 system 与最后一条当前消息

    def test_historical_user_image_is_restored(self):
        context = [
            {"role": "user", "content": "[\u56fe\u7247]", "images": [IMG1]},
            {"role": "assistant", "content": "\u770b\u5230\u5566"},
        ]
        history = self._history(self._parts(context))
        self.assertIsInstance(history[0]["content"], list)
        self.assertEqual(history[0]["content"][1]["image_url"]["url"], IMG1)
        self.assertIsInstance(history[1]["content"], str)

    def test_assistant_history_never_carries_images(self):
        context = [{"role": "assistant", "content": "\u597d\u7684", "images": [IMG1]}]
        history = self._history(self._parts(context))
        self.assertIsInstance(history[0]["content"], str)

    def test_budget_prefers_newest_and_is_capped(self):
        context = [
            {"role": "user", "content": "\u65e7\u56fe1", "images": [IMG1]},
            {"role": "assistant", "content": "\u55ef"},
            {"role": "user", "content": "\u65e7\u56fe2", "images": [IMG2]},
        ]
        history = self._history(self._parts(context))
        # MAX_HISTORY_IMAGES = 2：两张历史图都放得下，但最旧的先被分配
        with_images = [m for m in history if isinstance(m["content"], list)]
        self.assertEqual(len(with_images), 2)

        # 收紧预算到 1 时，只能是最新的那张
        original = AIReplyEngine.MAX_HISTORY_IMAGES
        AIReplyEngine.MAX_HISTORY_IMAGES = 1
        try:
            history = self._history(self._parts(context))
        finally:
            AIReplyEngine.MAX_HISTORY_IMAGES = original
        with_images = [m for m in history if isinstance(m["content"], list)]
        self.assertEqual(len(with_images), 1)
        self.assertEqual(with_images[0]["content"][1]["image_url"]["url"], IMG2)

    def test_history_without_images_is_all_plain_text(self):
        context = [
            {"role": "user", "content": "\u4f60\u597d", "images": []},
            {"role": "assistant", "content": "\u60a8\u597d"},
        ]
        for msg in self._history(self._parts(context)):
            self.assertIsInstance(msg["content"], str)


class HandleMessageThreadsDecryptedPayloadTests(unittest.IsolatedAsyncioTestCase):
    """handle_message 必须把「解密后的业务消息」交给防抖链路。

    曾经传的是原始推送帧（顶层 headers/body），于是下游所有按 message['1']
    取值的地方**静默失效**：
      - messageId 取不到 → 去重退化成 chat_id+文本，同文本一小时内被误判重复
      - 买家图片 URL 取不到 → 模型只看到 "[图片]"
    两者都不报错，只能靠这个测试守住。
    """

    COOKIE = "2218031692538"

    def _live(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = self.COOKIE
        live.myid = self.COOKIE
        live.order_status_handler = None
        return live

    @staticmethod
    def _frame(payload, mid="mid-1"):
        """把解密后的业务消息包成线上那种原始推送帧。"""
        import base64

        encoded = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        return {
            "headers": {"mid": mid},
            "body": {"syncPushPackage": {"data": [{"data": encoded}]}},
        }

    class _WS:
        async def send(self, *_args, **_kwargs):
            return None

    async def test_debounce_receives_decrypted_message_with_images(self):
        live = self._live()
        captured = {}

        async def fake_schedule(self, **kwargs):  # 类方法被替换后仍会收到 self
            captured.update(kwargs)

        with patch.object(XianyuLive, "send_notification", AsyncMock()), \
             patch.object(XianyuLive, "_schedule_debounced_reply", fake_schedule):
            await live.handle_message(self._frame(_image_message()), self._WS())

        self.assertIn("message_data", captured)
        threaded = captured["message_data"]
        # 是解密后的业务消息（有 '1'），不是原始帧（有 'body'）
        self.assertIn("1", threaded)
        self.assertNotIn("body", threaded)
        # 图片真的能从这条链路上解出来 —— 这正是之前失效的地方
        self.assertTrue(live._extract_image_urls(threaded))
        # messageId 也要能取到（否则去重会退化成 chat_id+文本 的兜底键）
        self.assertEqual(
            live._extract_message_id(threaded), "e3db3f54777e4fe792e96e5d571bc4e9"
        )

    async def test_raw_frame_would_lose_images_and_message_id(self):
        """对照：假如误传原始帧，messageId 与图片都会丢（而且是静默的）。"""
        live = self._live()
        raw = self._frame(_image_message())
        self.assertEqual(live._extract_image_urls(raw), [])
        self.assertIsNone(live._extract_message_id(raw))


if __name__ == "__main__":
    unittest.main()
