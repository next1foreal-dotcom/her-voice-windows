"""Summarize existing engine timings without retaining speech or making requests."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import statistics

FIELDS = ("stt", "llm", "tts_ttfa", "e2e", "vad_settle", "vad_decision", "hold")


def summarize(lines):
    records = []
    for line in lines:
        if " latency: " not in line:
            continue
        record = {}
        for field in FIELDS:
            match = re.search(rf"\b{field}=(\d+(?:\.\d+)?)s\b", line)
            if match:
                record[field + "_ms"] = round(float(match.group(1)) * 1000)
        for field in ("status", "source", "smart_turn_status"):
            match = re.search(rf"\b{field}=([a-z_]+)\b", line)
            if match:
                record[field] = match.group(1)
        records.append(record)
    completed = [r for r in records if r.get("status") == "completed" and r.get("source") == "speech"]
    metrics = {}
    for field in FIELDS:
        values = sorted(r[field + "_ms"] for r in completed if field + "_ms" in r)
        if values:
            metrics[field + "_ms"] = {"count": len(values), "median": statistics.median(values),
                "p90": values[max(0, __import__("math").ceil(len(values) * 0.9) - 1)], "max": max(values)}
    return {"records": records, "completed_speech_turns": len(completed), "metrics": metrics,
            "notes": "Server measurements overlap and are not additive. E2E ends at yielded TTS audio, not browser playback.", "cloud_requests": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", errors="replace") as source:
        print(json.dumps(summarize(source), ensure_ascii=True, indent=2))
