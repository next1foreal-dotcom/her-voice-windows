"""Bounded local-only seeded PCM benchmark against the already running model."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def measure(base, voice, text, *, reference_prefix_cache=None):
    payload = {"model": "drama-3", "voice": voice, "input": text,
               "response_format": "pcm", "stream_format": "audio", "seed": 0}
    if reference_prefix_cache is not None:
        payload['options'] = {'reference_prefix_cache': reference_prefix_cache}
    request = urllib.request.Request(base + "/v1/audio/speech", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    first = last = None
    digest = hashlib.sha256()
    total = 0
    peak_gap = 0.0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response:
        if response.headers.get_content_type() not in {"audio/pcm", "application/octet-stream"}:
            raise RuntimeError("Expected raw PCM audio, received " + response.headers.get_content_type())
        headers = time.perf_counter() - start
        while block := response.read1(8192):
            now = time.perf_counter() - start
            if first is None:
                first = now
            if last is not None:
                peak_gap = max(peak_gap, now - last)
            last = now
            total += len(block)
            digest.update(block)
    wall = time.perf_counter() - start
    if not total or total % 2:
        raise RuntimeError("Empty or incomplete PCM16 output")
    duration = total / 88200
    return {"headers_s": headers, "ttfa_s": first, "wall_s": wall, "bytes": total,
            "audio_s": duration, "rtf": wall / duration, "max_read_gap_s": peak_gap,
            "pcm_sha256": digest.hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8792")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--count", type=int, choices=range(1, 4), default=2)
    parser.add_argument('--reference-prefix-cache', choices=('on', 'off'))
    parser.add_argument('--text')
    args = parser.parse_args()
    if args.base_url != "http://127.0.0.1:8792":
        parser.error("Only the owned loopback model endpoint is allowed")
    if args.reference_prefix_cache is not None:
        receipt_path = Path(__file__).resolve().parents[1] / '.runtime/drama3/runtime.json'
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if 'expert_prefix_memo' not in receipt.get('capabilities', []):
            parser.error('Experimental native prefix memo is not installed; no benchmark request sent')
    text = "你好，我在这里。今天我们可以慢慢聊，你想先说些什么？"
    text = args.text or text
    cache = None if args.reference_prefix_cache is None else args.reference_prefix_cache == 'on'
    records = []
    for index in range(args.count):
        result = measure(args.base_url, args.voice, text, reference_prefix_cache=cache)
        records.append(result)
        print(json.dumps({"run": index + 1, **result}), flush=True)
    print(json.dumps({"cloud_requests": 0, "voice_id": args.voice, "seed": 0,
                      "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "repeat_pcm_equal": len({r["pcm_sha256"] for r in records}) == 1,
                      "runs": records}), flush=True)
