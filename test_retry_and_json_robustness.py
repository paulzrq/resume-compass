"""专门测试这次为了排查"有时候评估会失败"新加的三个兜底：
1. _extract_json_object 能从带前后缀文字的响应里抠出干净JSON
2. stop_reason=="max_tokens" 时识别为截断，并自动重试一次
3. 重试成功后返回值和正常调用完全一样；重试次数用尽后如实抛出最后一次的错误
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
import scoring

FAKE_MODEL_JSON = {
    "dimensions": {
        k: {"evidence": [], "rationale": "示例", "score": 3}
        for k in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]
    },
    "ats_keywords": [], "vague_phrases": [], "strong_phrases": [],
    "bonus_checked": [], "strengths": ["优势"], "gaps": ["短板"], "stage_note": "",
}


class _Block:
    type = "text"
    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 50
    output_tokens_details = type("D", (), {"thinking_tokens": 5})()


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.usage = _Usage()
        self.stop_reason = stop_reason


# ---- 测试1：_extract_json_object 容忍前后多余文字 ----
messy = "这是我的回答：\n" + json.dumps(FAKE_MODEL_JSON, ensure_ascii=False) + "\n希望有帮助！"
extracted = scoring._extract_json_object(messy)
parsed = json.loads(extracted)
assert parsed["dimensions"]["edu"]["score"] == 3
print("OK: _extract_json_object 能从带寒暄前后缀的文本里抠出干净JSON")

# ---- 测试2：stop_reason=max_tokens 触发截断异常，_score_resume_once 自动重试一次并成功 ----
call_log = []


class _FlakyMessages:
    def create(self, **kwargs):
        call_log.append(1)
        if len(call_log) == 1:
            # 第一次调用：模拟被max_tokens截断（JSON没写完，stop_reason是max_tokens）
            truncated_text = json.dumps(FAKE_MODEL_JSON, ensure_ascii=False)[:40]
            return _Resp(truncated_text, stop_reason="max_tokens")
        # 第二次调用（重试）：正常返回完整JSON
        return _Resp(json.dumps(FAKE_MODEL_JSON, ensure_ascii=False), stop_reason="end_turn")


class _FlakyClient:
    def __init__(self, api_key=None):
        self.messages = _FlakyMessages()


scoring.anthropic.Anthropic = _FlakyClient
framework = scoring.load_framework()
field = scoring.get_field(framework, "swe")
result = scoring._score_resume_once("一份简历原文", field, "", api_key="fake-key")
assert len(call_log) == 2, f"应该正好调用了2次（1次截断+1次重试成功），实际是{len(call_log)}次"
assert result["dimension_scores"]["edu"] == 3
print("OK: 第1次被max_tokens截断，_score_resume_once自动重试一次后拿到正常结果")

# ---- 测试3：两次都失败时，如实抛出最后一次的错误，不会无限重试 ----
call_log2 = []


class _AlwaysTruncatedMessages:
    def create(self, **kwargs):
        call_log2.append(1)
        truncated_text = json.dumps(FAKE_MODEL_JSON, ensure_ascii=False)[:40]
        return _Resp(truncated_text, stop_reason="max_tokens")


class _AlwaysTruncatedClient:
    def __init__(self, api_key=None):
        self.messages = _AlwaysTruncatedMessages()


scoring.anthropic.Anthropic = _AlwaysTruncatedClient
try:
    scoring._score_resume_once("一份简历原文", field, "", api_key="fake-key")
    raise AssertionError("两次都截断，应该抛出异常，不应该正常返回")
except scoring._TruncatedResponseError:
    pass
assert len(call_log2) == 2, f"应该正好重试1次、总共调用2次后放弃，实际是{len(call_log2)}次"
print("OK: 连续2次都截断时，重试1次后如实抛出错误，不会无限重试")

print("ALL OK: 重试与JSON容错逻辑验证通过")
