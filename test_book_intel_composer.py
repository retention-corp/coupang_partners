"""Tests for book_intel.composer — mocks subprocess, asserts parsing + shape + retry."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from book_intel.composer import CompositionError, ComposerConfig, compose_post


def _stdout_wrapper(content: str) -> str:
    return json.dumps({"ok": True, "data": {"turn": {"finalAssistantVisibleText": content}}}, ensure_ascii=False)


def _stdout_payloads_wrapper(content: str) -> str:
    """Alt OpenClaw shape: runs with delivery-payload output (long/complex prompts)."""

    return json.dumps({"runId": "r", "status": "ok", "result": {"payloads": [{"text": content}]}}, ensure_ascii=False)


class ComposerSuccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = {
            "book": {"title": "세이노의 가르침", "author": "세이노", "isbn13": "9791168473690", "category": "자기계발"},
            "aladin": {"detail": {"description": "자기계발서"}, "bestseller_rank": 3},
            "naver": {"description": "네이버 책 요약"},
            "data4library": {"monthly_loans": 1200, "similar_books": []},
            "coupang": {"top_reviews": []},
            "youtube": [],
            "target_persona": {"interests": ["솔로 오퍼레이터"]},
        }

    def test_a_tier_success(self) -> None:
        inner = {
            "title": "솔로 운영자의 세이노 읽기",
            "lead": "혼자 일하며 버티는 사람에게 이 책은 냉정한 습관의 중요성을 일깨운다.",
            "body": "본문..." + "x" * 1600,
            "tags": ["자기계발", "솔로오퍼레이터", "세이노"],
        }
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stdout_wrapper(json.dumps(inner, ensure_ascii=False)), stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed) as patched:
            post = compose_post(self.raw, tier="A")
        self.assertEqual(post["title"], "솔로 운영자의 세이노 읽기")
        self.assertEqual(post["tier"], "A")
        self.assertEqual(post["prompt_variant"], "blog_a_tier")
        self.assertIn("도서추천", post["tags"])  # prefix inserted
        self.assertIn("쿠팡파트너스", post["tags"])
        patched.assert_called_once()

    def test_b_tier_lowercase_tier_input_works(self) -> None:
        inner = {
            "title": "짧은 소개",
            "lead": "이 책은 일상 에세이입니다",
            "body": "본문 " + "가" * 700,
        }
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stdout_wrapper(json.dumps(inner, ensure_ascii=False)), stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed):
            post = compose_post(self.raw, tier="b")
        self.assertEqual(post["tier"], "B")
        self.assertEqual(post["prompt_variant"], "blog_b_tier")

    def test_code_fences_are_stripped(self) -> None:
        inner = {"title": "제목", "lead": "요약", "body": "본문"}
        wrapped_with_fences = f"```json\n{json.dumps(inner, ensure_ascii=False)}\n```"
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stdout_wrapper(wrapped_with_fences), stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed):
            post = compose_post(self.raw, tier="A")
        self.assertEqual(post["title"], "제목")

    def test_openclaw_payloads_shape_is_parsed(self) -> None:
        """Long prompts return `result.payloads[0].text` instead of `data.turn.finalAssistantVisibleText`."""

        inner = {"title": "제목", "lead": "요약", "body": "본문 본문 본문"}
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stdout_payloads_wrapper(json.dumps(inner, ensure_ascii=False)), stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed):
            post = compose_post(self.raw, tier="A")
        self.assertEqual(post["title"], "제목")

    def test_json_embedded_in_prose_is_recovered(self) -> None:
        inner = {"title": "제목", "lead": "요약", "body": "본문"}
        noisy = f"Sure, here is the post:\n\n{json.dumps(inner, ensure_ascii=False)}\n\nLet me know if you need changes."
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=_stdout_wrapper(noisy), stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed):
            post = compose_post(self.raw, tier="A")
        self.assertEqual(post["title"], "제목")


class ComposerFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = {"book": {"title": "T", "isbn13": "isbn-x"}}

    def test_missing_title_field_retries_then_fails(self) -> None:
        bad = _stdout_wrapper("not json at all")
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=bad, stderr="")
        with mock.patch.object(subprocess, "run", return_value=completed) as patched:
            with self.assertRaises(CompositionError):
                compose_post(self.raw, tier="A")
        # MAX_RETRIES=2 → subprocess.run called twice
        self.assertEqual(patched.call_count, 2)

    def test_timeout_raises_composition_error(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd=["openclaw"], timeout=1)):
            with self.assertRaises(CompositionError):
                compose_post(self.raw, tier="A")

    def test_non_zero_exit_raises(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom")
        with mock.patch.object(subprocess, "run", return_value=completed):
            with self.assertRaises(CompositionError):
                compose_post(self.raw, tier="A")


class SessionIdTests(unittest.TestCase):
    def test_session_id_derives_from_isbn(self) -> None:
        raw = {"book": {"title": "T", "isbn13": "9791168473690"}}
        inner = {"title": "t", "lead": "l", "body": "b"}
        stdout = _stdout_wrapper(json.dumps(inner))
        captured: list[list[str]] = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            compose_post(raw, tier="A")
        argv = captured[0]
        # --session-id contains the isbn
        self.assertIn("--session-id", argv)
        sid_value = argv[argv.index("--session-id") + 1]
        self.assertIn("9791168473690", sid_value)
        self.assertIn("-a-", sid_value.lower())

    def test_session_id_falls_back_to_title_hash(self) -> None:
        raw = {"book": {"title": "제목만있는책"}}
        inner = {"title": "t", "lead": "l", "body": "b"}
        stdout = _stdout_wrapper(json.dumps(inner))
        captured: list[list[str]] = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout=stdout, stderr="")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            compose_post(raw, tier="B")
        argv = captured[0]
        sid_value = argv[argv.index("--session-id") + 1]
        self.assertTrue(sid_value.startswith("book-intel-b-"))


if __name__ == "__main__":
    unittest.main()
