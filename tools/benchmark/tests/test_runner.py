"""Offline reproducible contract tests; no real LLM or benchmark claims."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from tools.benchmark import runner


class FakeChat(BaseHTTPRequestHandler):
    received = []
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received.append(body)
        if body["model"] == "bad":
            self.send_response(429)
            self.end_headers()
            return
        if body["stream"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
            self.wfile.flush()
            time.sleep(.02)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')
            self.wfile.flush()
            if body.get("stream_options") == {"include_usage": True} and body["model"] != "no-usage":
                self.wfile.write(b'data: {"choices":[],"usage":{"completion_tokens":2,"prompt_tokens":3}}\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        else:
            data = json.dumps({"choices": [{"message": {"content": "ok"}}],
                               "usage": {"completion_tokens": 4, "prompt_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


@contextmanager
def server():
    FakeChat.received = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


class BenchmarkRunnerTests(unittest.TestCase):
    def test_endpoint_rejects_nonlocal_and_redirect_url(self):
        for endpoint in ["https://127.0.0.1:8081/v1/chat/completions", "http://localhost:8081/v1/chat/completions",
                         "http://example.com:8081/v1/chat/completions", "http://127.0.0.1:8081/evil",
                         "http://127.0.0.1:8081/v1/chat/completions?x=y"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                runner.validate_endpoint(endpoint)
        runner.validate_endpoint("http://127.0.0.1:8081/v1/chat/completions")

    def test_dataset_hash_and_reject_unexpected_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dataset.jsonl"
            p.write_text('{"messages":[{"role":"user","content":"hello"}]}\n')
            rows, sha = runner.read_dataset(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(sha), 64)
            p.write_text('{"messages":[],"secret":"leak"}\n')
            with self.assertRaises(ValueError):
                runner.read_dataset(p)

    def test_nearest_rank_is_deterministic(self):
        self.assertEqual(runner.percentile_nearest_rank(list(range(1, 21)), .95), 19)
        self.assertIsNone(runner.percentile_nearest_rank([], .95))

    def test_streaming_ttft_is_first_content_not_first_event(self):
        with server() as endpoint:
            sample = runner.request_once(endpoint, "sample", [{"role":"user", "content":"test"}], stream=True, max_tokens=64, timeout=5)
        self.assertTrue(sample.success, sample.error)
        self.assertIsNotNone(sample.ttft_ms)
        self.assertGreater(sample.ttft_ms, 10)
        self.assertEqual(sample.completion_tokens, 2)
        self.assertEqual(sample.prompt_tokens, 3)
        self.assertEqual(FakeChat.received[-1]["stream_options"], {"include_usage": True})

    def test_nonstream_completion_and_usage(self):
        with server() as endpoint:
            sample = runner.request_once(endpoint, "sample", [{"role":"user", "content":"test"}], stream=False, max_tokens=64, timeout=5)
        self.assertTrue(sample.success, sample.error)
        self.assertIsNone(sample.ttft_ms)
        self.assertEqual(sample.completion_tokens, 4)
        self.assertNotIn("stream_options", FakeChat.received[-1])

    def test_warmup_excluded_and_concurrent_measurement(self):
        with server() as endpoint:
            result = runner.run(endpoint, "sample", [[{"role":"user","content":"hello"}]],
                                stream=False, max_tokens=64, concurrency=2, requests=4, warmup=2, timeout=5)
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["successes"], 4)
        self.assertEqual(result["output_tokens"], 16)
        self.assertEqual(len(FakeChat.received), 6)

    def test_http_errors_are_counted_not_misreported_as_successes(self):
        with server() as endpoint:
            result = runner.run(endpoint, "bad", [[{"role":"user","content":"hello"}]],
                                stream=False, max_tokens=64, concurrency=1, requests=3, warmup=0, timeout=5)
        self.assertEqual(result["failures"], 3)
        self.assertEqual(result["errors"], {"http_429": 3})
        self.assertIsNone(result["output_tokens_per_second"])

    def test_missing_token_usage_does_not_invent_throughput(self):
        samples = [runner.Sample(200, True, 100, 40, 10, 20), runner.Sample(200, True, 110, 45, None, None)]
        result = runner.summarize(samples, 1, True)
        self.assertEqual(result["completion_token_coverage"], 1)
        self.assertIsNone(result["output_tokens_per_second"])

    def test_command_outputs_report_without_prompts(self):
        with server() as endpoint, tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.jsonl"
            output = Path(directory) / "run.json"
            dataset.write_text('{"messages":[{"role":"user","content":"private fixture prompt"}]}\n')
            args = ["--endpoint", endpoint, "--dataset", str(dataset), "--model", "sample", "--runtime", "mock",
                    "--hardware", "mock-cpu", "--model-revision", "mock-rev", "--requests", "2", "--warmup", "1", "--output", str(output)]
            self.assertEqual(runner.main(args), 0)
            report = output.read_text()
            self.assertNotIn("private fixture prompt", report)
            self.assertEqual(json.loads(report)["results"]["successes"], 2)



    def test_warmup_and_measured_streams_both_request_usage(self):
        with server() as endpoint:
            report = runner.run(
                endpoint, "sample", [[{"role": "user", "content": "private prompt"}]],
                stream=True, max_tokens=32, concurrency=2, requests=3,
                warmup=1, timeout=5,
            )
        self.assertEqual(len(FakeChat.received), 4)  # Warmup + measured.
        self.assertTrue(all(body.get("stream_options") == {"include_usage": True}
                            for body in FakeChat.received))
        self.assertEqual(report["successes"], 3)
        self.assertEqual(report["completion_token_coverage"], 3)
        self.assertEqual(report["output_tokens"], 6)
        self.assertGreater(report["output_tokens_per_second"], 0)

    def test_requested_usage_may_still_be_missing(self):
        with server() as endpoint:
            report = runner.run(
                endpoint, "no-usage", [[{"role": "user", "content": "test"}]],
                stream=True, max_tokens=32, concurrency=1, requests=2,
                warmup=0, timeout=5,
            )
        self.assertTrue(all(body.get("stream_options") == {"include_usage": True}
                            for body in FakeChat.received))
        self.assertEqual(report["successes"], 2)
        self.assertEqual(report["completion_token_coverage"], 0)
        self.assertIsNone(report["output_tokens"])
        self.assertIsNone(report["output_tokens_per_second"])

    def test_stream_usage_compatibility_flag_omits_stream_options(self):
        with server() as endpoint, tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.jsonl"
            output = Path(directory) / "benchmark.json"
            dataset.write_text('{"messages":[{"role":"user","content":"private fixture"}]}\n')
            args = ["--endpoint", endpoint, "--dataset", str(dataset), "--model", "sample",
                    "--model-revision", "mock-rev", "--runtime", "mock-runtime",
                    "--hardware", "mock-hardware", "--requests", "2",
                    "--warmup", "1", "--concurrency", "1", "--stream",
                    "--no-stream-usage", "--output", str(output)]
            self.assertEqual(runner.main(args), 0)
            self.assertNotIn("private fixture", output.read_text())
            self.assertTrue(all("stream_options" not in b for b in FakeChat.received))
            report = json.loads(output.read_text())
            self.assertFalse(report["configuration"]["stream_usage_requested"])
            self.assertEqual(report["results"]["successes"], 2)
            self.assertEqual(report["results"]["completion_token_coverage"], 0)
            self.assertIsNone(report["results"]["output_tokens_per_second"])

    def test_cli_records_usage_request_in_run_configuration(self):
        with server() as endpoint, tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.jsonl"
            output = Path(directory) / "baseline.json"
            dataset.write_text('{"messages":[{"role":"user","content":"example"}]}\n')
            args = ["--endpoint", endpoint, "--dataset", str(dataset), "--model", "sample",
                    "--model-revision", "mock-rev", "--runtime", "mock-runtime",
                    "--hardware", "mock-hardware", "--requests", "2", "--warmup", "0",
                    "--stream", "--output", str(output)]
            self.assertEqual(runner.main(args), 0)
            report = json.loads(output.read_text())
            self.assertTrue(report["configuration"]["stream_usage_requested"])
            self.assertEqual(report["results"]["completion_token_coverage"], 2)
            self.assertEqual(report["results"]["output_tokens"], 4)
            self.assertGreater(report["results"]["output_tokens_per_second"], 0)


class StreamProtocolTests(unittest.TestCase):
    """Protocol-level regression tests; all data is synthetic."""

    def parse(self, data: bytes):
        import io
        return runner._consume_stream(io.BytesIO(data), time.perf_counter())

    def test_valid_usage_and_content(self):
        events = (
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"yes"}}]}\n\n'
            b'data: {"choices":[],"usage":{"completion_tokens":2,"prompt_tokens":7}}\n\n'
            b'data: [DONE]\n\n'
        )
        first, completion, prompt = self.parse(events)
        self.assertIsNotNone(first)
        self.assertEqual((completion, prompt), (2, 7))

    def test_multiline_data_and_comments(self):
        data = (
            b': heartbeat\n\nevent: message\n'
            b'data: {"choices":[{"delta":\n'
            b'data: {"content":"hello"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        self.assertIsNotNone(self.parse(data)[0])

    def test_usage_only_sse_event_supported(self):
        data = (
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            b'data: {"usage":{"completion_tokens":5}}\n\n'
            b'data: [DONE]\n\n'
        )
        self.assertEqual(self.parse(data)[1], 5)

    def test_error_after_content_rejected(self):
        data = (
            b'data: {"choices":[{"delta":{"content":"partial secret"}}]}\n\n'
            b'data: {"error":{"message":"private runtime secret"}}\n\n'
            b'data: [DONE]\n\n'
        )
        with self.assertRaisesRegex(ValueError, "upstream_stream_error"):
            self.parse(data)

    def test_explicit_error_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "upstream_stream_error"):
            self.parse(b'event: error\ndata: {"message":"secret"}\n\ndata: [DONE]\n\n')

    def test_filtered_completion_rejected(self):
        data = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"content_filter"}]}\n\ndata: [DONE]\n\n'
        with self.assertRaisesRegex(ValueError, "upstream_stream_error"):
            self.parse(data)

    def test_invalid_sse_json_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_sse_json"):
            self.parse(b'data: {not-valid}\n\ndata: [DONE]\n\n')

    def test_invalid_delta_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_sse_event"):
            self.parse(b'data: {"choices":[{"delta":{"content":42}}]}\n\ndata: [DONE]\n\n')

    def test_missing_done_rejected_even_after_content(self):
        with self.assertRaisesRegex(ValueError, "missing_stream_terminator"):
            self.parse(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    def test_byte_limit_rejects_unbounded_sse(self):
        with self.assertRaisesRegex(ValueError, "response_too_large"):
            self.parse(b":" + b"x" * runner.MAX_RESPONSE_BYTES + b"\n\n")

    def test_role_only_done_has_no_first_token(self):
        self.assertIsNone(self.parse(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: [DONE]\n\n')[0])


if __name__ == "__main__":
    unittest.main()
