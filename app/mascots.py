"""field_id -> 职业插画文件名 的映射，以及取图片路径的小工具。

图片来自 app/mascots/ 目录下的 28 张人物插画（1254x1254px，纯白底），
文件名严格取自 app/mascots/职业名称.txt。注意部分复合职位用的是全角斜杠
"／" 而不是普通斜杠 "/"。

biomed 以及自定义方向（CUSTOM_FIELD_ID）不在这 28 张插画范围内，
mascot_path() 对它们返回 None，调用方需要优雅降级（不显示头像）。
"""
from pathlib import Path

MASCOTS_DIR = Path(__file__).resolve().parent / "mascots"

FIELD_ID_TO_MASCOT_FILENAME = {
    "swe": "软件工程师.png",
    "ds": "数据科学家.png",
    "mle": "机器学习工程师.png",
    "engineering": "硬件工程师.png",
    "cybersecurity": "网络安全工程师.png",
    "robotics": "机器人／自动化工程师.png",
    "fintech_eng": "金融科技工程师.png",
    "finance": "投行分析师.png",
    "risk_analyst": "风险分析师.png",
    "actuary": "精算师.png",
    "consulting": "咨询顾问.png",
    "marketing": "市场营销.png",
    "pm": "产品经理.png",
    "ba": "商业分析师.png",
    "ops": "供应链管理.png",
    "data_analyst": "数据分析师.png",
    "accounting": "会计师／审计师.png",
    "hr": "人力资源.png",
    "sales": "销售.png",
    "law": "律师.png",
    "clinical_research": "临床研究助理.png",
    "media": "传媒／公关.png",
    "film_production": "影视制作人.png",
    "teacher": "教师.png",
    "ux": "UI／UX设计师.png",
    "graphic_design": "平面设计师.png",
    "architecture": "建筑师.png",
    "game_design": "游戏设计师.png",
    # biomed 没有对应插画（不在28张范围内）
}

# field_id -> 分享按钮主色（从对应插画的服装/主体颜色里提取，深色调，配白色文字对比度足够）。
# 用脚本对每张插画做了一次颜色统计（排除近白背景、近黑轮廓线和肤色区间后，取出现最多的颜色
# 区块，再统一收窄到饱和度0.45~0.75、明度0.30~0.48的范围，让28种颜色风格统一、不刺眼）。
# 没有插画的方向（如自定义方向）用 DEFAULT_ACCENT_COLOR 兜底。
DEFAULT_ACCENT_COLOR = "#493a5c"

FIELD_ID_TO_ACCENT_COLOR = {
    "swe": "#243054",
    "ds": "#4c2a4c",
    "mle": "#2e2e54",
    "engineering": "#4c3f26",
    "cybersecurity": "#2e3a54",
    "robotics": "#2a3b4c",
    "fintech_eng": "#183c60",
    "finance": "#13414c",
    "risk_analyst": "#26324c",
    "actuary": "#193f4c",
    "consulting": "#184754",
    "marketing": "#185760",
    "pm": "#64427a",
    "ba": "#42597a",
    "ops": "#2a4c4c",
    "data_analyst": "#30517a",
    "accounting": "#37597a",
    "hr": "#7a3730",
    "sales": "#1e7a74",
    "law": "#2e543a",
    "clinical_research": "#154954",
    "media": "#346034",
    "film_production": "#4c1919",
    "teacher": "#4c3326",
    "ux": "#243b6c",
    "graphic_design": "#60243c",
    "architecture": "#2a3b4c",
    "game_design": "#3a2e54",
}


def mascot_path(field_id: str):
    """返回给定 field_id 对应插画的绝对路径；没有插画（或文件缺失）时返回 None。"""
    filename = FIELD_ID_TO_MASCOT_FILENAME.get(field_id)
    if not filename:
        return None
    p = MASCOTS_DIR / filename
    return p if p.is_file() else None


def mascot_accent_color(field_id: str) -> str:
    """返回给定 field_id 对应插画的主色调；没有专属颜色时返回 DEFAULT_ACCENT_COLOR。"""
    return FIELD_ID_TO_ACCENT_COLOR.get(field_id, DEFAULT_ACCENT_COLOR)
