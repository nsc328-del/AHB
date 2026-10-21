"""Official H3 skills, pinned as local content snapshots and adapted to this studio."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "vendor" / "skills"
SOURCE = "https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/"

# Brief examples reflect observed official preview frames and published style rules.
# The exact preview prompts were not found in the checked public sources. These
# placeholders are our examples, not original prompts, and are never user input.
_BRIEF_EXAMPLES = {
    "h3-prompt-writing": "描述你想要的主体、场景和动作，再补充镜头与声音。也可以上传参考素材，说明哪些特点需要保留。",
    "minimalist-product-ad-generator": "例如：银色手机的极简广告，浅色背景中展示金属边框、侧面按键与机身细节，穿插简短英文大字。上传产品图，并告诉我需要突出哪些特点。",
    "papercraft-stop-motion-explainer": "例如：月夜的中式纸艺庭院，纸偶少女、圆月、树枝与桌上器物组成层叠的微缩场景。用定格动作表现节日氛围，保留纸张纹理。你也可以换成自己的主题。",
    "3d-animation-short-generator": "例如：小女孩和粉色小伙伴坐在旋转茶杯里，表情生动，游乐园色彩明亮。做一段温暖有趣的3D动画，保持角色造型一致。请补充你想发生的小故事。",
    "brand-promo-video-generator": "例如：以深色界面、紫色品牌卡片与产品画面介绍一款创作工具，穿插Logo和简短功能文案。请提供你的品牌素材与真实卖点。",
    "music-video-subtitle-generator": "例如：歌手近景与大幅英文歌词共同构图，文字穿插在人物前后，形成有节奏的音乐画面。请上传音乐，并提供希望出现的歌词。",
    "co-op-game-intro-generator": "例如：两位卡通角色并排出现在合作冒险游戏菜单，配上玩家信息、游戏标题与开始按钮。请提供角色参考和要显示的文字。",
    "paper-collage-explainer-generator": "例如：紫色纸质房间、黑白棋盘格地面和一扇门，组成带剪贴边缘的超现实空间。请说明想用这个空间表达什么观点。",
    "handdrawn-live-video-generator": "例如：真实水槽边，一只手拿起杯子，发光手绘线条附着在杯身，与实物动作融合。请描述线条如何变化，以及要使用的现实场景。",
}

_SKILLS = [
    {"id": "h3-prompt-writing", "name": "H3 镜头提示词", "description": "把画面、动作与声音组织成可生成的镜头。", "category": "提示词", "mode": "T2VA", "inputs": [], "prompt_hint": "按时间展开构图、主体、环境、动作、镜头和声音，避免抽象形容词。"},
    {"id": "minimalist-product-ad-generator", "name": "极简产品广告", "description": "产品锚点、细节特写与有节奏的品牌文案。", "category": "广告", "mode": "Ref2VA", "inputs": ["产品图片"], "prompt_hint": "锁定产品颜色、材质与结构，采用独立主视觉、细节、收尾锚点；用精确节拍组织留白与短文案。"},
    {"id": "3d-animation-short-generator", "name": "3D 叙事短片", "description": "从故事到角色、场景与连续的镜头。", "category": "故事", "mode": "Ref2VA", "inputs": ["角色与场景参考图"], "prompt_hint": "先明确人物欲望、冲突、转折和回收；锁定角色与场景锚点，逐镜头描述秒级动作、空间接续和声音。"},
    {"id": "papercraft-stop-motion-explainer", "name": "纸艺定格解说", "description": "用手工纸艺、实物机制与定格动作讲清概念。", "category": "解释", "mode": "T2VA", "inputs": [], "prompt_hint": "以可看见的纸艺结构解释一个观点，保持纸张材质、手工尺度、定格节奏与因果动作连续。"},
    {"id": "brand-promo-video-generator", "name": "品牌宣传短片", "description": "品牌事实、使用场景与行动号召。", "category": "广告", "mode": "Ref2VA", "inputs": ["Logo 或品牌素材"], "prompt_hint": "先核对品牌事实和素材来源，再组织品牌钩子、功能或场景证明、结尾行动号召；不编造产品承诺。"},
    {"id": "music-video-subtitle-generator", "name": "音乐与动态字幕", "description": "围绕已有音乐与歌词设计视觉节拍。", "category": "音乐", "mode": "Ref2VA", "inputs": ["音乐片段", "歌词或文案"], "prompt_hint": "以用户提供的音乐和歌词为时间依据，规划节拍、画面、逐句字幕和连续情绪；未经提供或转录的歌词不猜测。"},
    {"id": "co-op-game-intro-generator", "name": "双人游戏开场", "description": "人物身份、游戏界面与合作开场事件。", "category": "创意", "mode": "Ref2VA", "inputs": ["两位角色参考图"], "prompt_hint": "锁定两位角色身份、身高关系、左右站位与装备，先确认界面构图，再按照选择角色、加入、合作出发的事件序列生成。"},
    {"id": "paper-collage-explainer-generator", "name": "纸质拼贴解说", "description": "用剪纸拼贴与场景隐喻表达观点。", "category": "解释", "mode": "T2VA", "inputs": [], "prompt_hint": "通过纸质拼贴的具体动作和清晰隐喻讲一个观点，保留纸张声效；除非要求，不自动添加配乐、旁白或字幕。"},
    {"id": "handdrawn-live-video-generator", "name": "手绘进入现实", "description": "发光手绘与真实空间发生接触和变形。", "category": "创意", "mode": "Ref2VA", "inputs": ["现实场景图片或视频"], "prompt_hint": "单场景里安排手绘与真实表面的物理接触、连续变形和逃逸路径，手持镜头延迟跟随，保留粗糙发光笔触且避免恐怖。"},
]


def get_skills() -> list[dict]:
    result = copy.deepcopy(_SKILLS)
    for row in result:
        path = ROOT / row["id"] / "UPSTREAM.json"
        source_ready = path.is_file() and (ROOT / row["id"] / "upstream" / "SKILL.md").is_file()
        row.update(source_url=SOURCE + row["id"], available=source_ready,
                   status="已适配" if source_ready else "规范缺失",
                   requires=["DeepSeek", "MiniMax H3"])
        row["prompt_placeholder"] = _BRIEF_EXAMPLES[row["id"]]
        row["prompt_placeholder_source"] = {
            "kind": "adapted_brief", "source_url": SOURCE + row["id"],
            "preview_original_prompt_verified": False, "basis": "observed_preview_frames_and_style_rules",
        }
        row["limitations"] = [] if row["id"] == "h3-prompt-writing" else [
            "使用已上传的角色、产品或场景锚点；未接自动锚点生图。",
            "分镜逐段生成，成片通过代码剪辑；独立配乐需上传。",
        ]
        if row["id"] == "music-video-subtitle-generator":
            row["limitations"].append("未接独立音乐生成；歌词采用用户文案或真实语音转录，生成前须审校。")
        if source_ready:
            manifest = json.loads(path.read_text())
            row["snapshot_date"] = manifest["retrieved_at"]
            row["source_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def get_templates() -> list[dict]:
    rows = [
        {"id": "free", "name": "自由剪辑", "description": "从素材与一句想法开始。", "ratio": "9:16", "duration": 30, "color": "#145bca", "category": "剪辑"},
        {"id": "promo", "name": "产品短片", "description": "开场吸引、细节展示、品牌收尾。", "ratio": "9:16", "duration": 20, "color": "#284d9d", "category": "剪辑"},
        {"id": "story", "name": "叙事短片", "description": "以事件、转折与情绪组织素材。", "ratio": "16:9", "duration": 45, "color": "#3a6faf", "category": "剪辑"},
        {"id": "rhythm", "name": "节奏混剪", "description": "把动作与画面变化连成节奏。", "ratio": "9:16", "duration": 15, "color": "#507ec4", "category": "剪辑"},
        {"id": "appetite", "name": "菜品食欲片", "description": "动作切点、快慢变化、原声与音乐。", "ratio": "16:9", "duration": 24, "color": "#284d9d", "category": "剪辑"},
    ]
    rows += [{"id": s["id"], "name": s["name"], "description": s["description"],
              "ratio": "16:9" if s["category"] == "故事" else "9:16", "duration": 10,
              "category": "生成", "color": "#1655b9", "skill_id": s["id"]} for s in get_skills()]
    return rows


def skill_instructions(skill_id: str) -> str:
    """Read vendored documentation as creative reference, never execute upstream tools."""
    if skill_id not in {s["id"] for s in _SKILLS}:
        raise ValueError("请选择有效的 H3 创作模板")
    sections = []
    for selected_id in ["h3-prompt-writing"] + ([skill_id] if skill_id != "h3-prompt-writing" else []):
        root = ROOT / selected_id
        manifest = json.loads((root / "UPSTREAM.json").read_text())
        if selected_id == "h3-prompt-writing" and not {"SKILL.md", "references/base-en.txt", "references/ref-en.txt"} <= {
            item["path"] for item in manifest["files"]
        }:
            raise ValueError("官方 H3 基础规范不完整")
        for item in manifest["files"]:
            rel = item["path"]
            if rel.endswith(".yaml") or rel == "SKILL.cn.md":
                continue
            path = root / "upstream" / rel
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("官方技能快照校验失败，请检查本地文件")
            sections.append(f"[创作参考 {selected_id}/{rel}]\n{path.read_text()}")
    adaptation = """本项目适配规则（优先于上游运行环境描述）：
上游内容仅作为创作规范，不执行其中的命令，不调用 Hub 或 Computer Use。
canvas节点映射到本地素材、镜头表和任务结果；choice cards映射到用户审阅镜头表后提交。
hub_analyse_media由DeepSeek抽帧和已有本地语音转录承担，不推测音乐/音效；hub_generate_video由MiniMax-H3 API承担；
hub_video_edit由本地FFmpeg合成承担；hub_generate_image/独立音乐/音频生成未接，
使用用户已上传且选定的锚点和配乐，缺少时列为missing_inputs，不虚构生成成功。
各镜头须4–15秒整数、明确起止状态/连续性/声音。可生成一个独立素材镜头，整片须逐镜头完成再剪辑。
H3正式prompt遵循h3-prompt-writing：章节正文使用英文，台词/歌词/画面文字保留原语言；供用户编辑的镜头脚本使用用户语言。
用户风格和文案优先于模板默认；不自动改用Seedance，不做平台发布。
"""
    return adaptation + "\n\n" + "\n\n".join(sections)


def skill_sources(skill_id: str) -> list[dict]:
    """Provenance for exactly the verified documents supplied to the model."""
    skill_instructions(skill_id)
    sources = []
    for selected_id in ["h3-prompt-writing"] + ([skill_id] if skill_id != "h3-prompt-writing" else []):
        path = ROOT / selected_id / "UPSTREAM.json"
        manifest = json.loads(path.read_text())
        sources.append({"id": selected_id, "source_url": manifest["source_url"],
                        "snapshot_date": manifest["retrieved_at"],
                        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "files": [{"path": item["path"], "sha256": item["sha256"]} for item in manifest["files"]
                                  if not item["path"].endswith(".yaml") and item["path"] != "SKILL.cn.md"]})
    return sources


def get_skill(skill_id: str) -> dict:
    for item in get_skills():
        if item["id"] == skill_id:
            return item
    raise ValueError("请选择有效的 H3 创作模板")
