"""Build the local, evidence-linked comparison without changing workbench results."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def esc(value):
    return html.escape(str(value))


def timecode(value):
    return f"{int(value // 60):02}:{value % 60:04.1f}"


cases = {}
for key in ("long", "short"):
    asset = read(f"{key}-deepseek.json")
    usage = read(f"{key}-usage.json")
    cases[key] = {
        "asset_id": asset["id"],
        "title": asset["name"],
        "duration": asset["duration"],
        "media_url": f"http://127.0.0.1:8787{asset['url']}",
        "model": asset["analysis"]["model"],
        "ds_frames": asset["analysis"]["coverage"]["sampled_frame_count"],
        "detector_candidate_intervals": asset["analysis"]["coverage"]["detected_shot_count"],
        "ds_content_segments": len(asset["analysis"]["segments"]),
        "ds_semantic_sections": len(asset["analysis"]["structure"]),
        "audio_used_in_analysis": asset["analysis"]["audio_used_in_analysis"],
        "usage": usage["aggregates"],
        "recorded_calls": usage["coverage"]["recorded_calls"],
    }

findings = [
    {
        "case": "short", "time": 6.766667, "title": "同一拍摄镜头内的动作，被当成新的镜头",
        "ds": "返回 12 个内容段，界面标成“12 个镜头”；上游检测产生 17 个候选区间。",
        "independent": "按跨场景切换复核为 6 个连续拍摄段。人物入画、居中、出画是同一段内的动作，不是三次切镜。",
        "evidence": "确认的切点约为 2.600、4.733、6.767、8.700、10.867 秒。1.667、1.833、2.000 秒虽触发场景检测，但邻帧仍在同一便利店外镜头。",
        "implication": "镜头边界、镜头内动作、语义段落需要分开存储和展示。",
    },
    {
        "case": "short", "time": 6.766667, "title": "楼梯镜头的开始时间偏晚",
        "ds": "楼梯照片展示从 7.600 秒开始；6.500–7.600 秒仍描述为照相馆离场。",
        "independent": "6.767 秒已切到楼梯区域；7.600 秒只是这个新镜头中人物居中的一帧。",
        "evidence": "cut-neighbors.jpg 中 6.733 与 6.801 秒分别为照相区域和楼梯区域，差异可直接回看。",
        "implication": "抽到人物的时间不等于镜头入点，不能把模型段落端点直接送给渲染器。",
    },
    {
        "case": "short", "time": 0, "title": "迁移的是动作规律，不只是地点列表",
        "ds": "识别了旅行、多场景、餐饮、照片展示、街边打卡等内容。",
        "independent": "6 段反复使用同方向横向移镜：环境→人物居中→人物退出→下一处。每段约 1.70–2.60 秒，整体是平行打卡结构。",
        "evidence": "两个顺序接触表及五处跨场景邻帧。",
        "implication": "剪新素材时需要匹配运镜方向、主体入出画位置与停留时间，而非只检索相同场所标签。",
    },
    {
        "case": "long", "time": 56.7, "title": "视觉分组遗漏了关键的思想转折",
        "ds": "将 60.967–105.550 秒归为“工作实态与压力叙述”；此前称“落日抒情段”。",
        "independent": "约 29.1–56.7 秒是在缩短期待、回到日常；约 56.7–84.4 秒反向拉大到宇宙尺度，追问做事的意义；84.4 秒后才进入努力与自我怀疑。",
        "evidence": "画面字幕与本地转录可互相核对。早餐、热巧克力的语义配的是海景，视觉对象和表达意图并不相同。",
        "implication": "对白/字幕与画面需要共同进入段落分析，素材匹配不能只按名词。",
    },
    {
        "case": "long", "time": 18.567, "title": "长短镜头变化比统一的“快剪”更关键",
        "ds": "概括为“旁白推进＋高光素材闪剪”，识别到高光蒙太奇和情绪回落；但返回的 22 个内容段不是 22 个实际镜头。",
        "independent": "开头约 18.567 秒才首次明显换镜；144–154 秒密集切换；约 166–175 秒又回到较长的人物口述。",
        "evidence": "开头、密集段和回收段接触表与边界邻帧。",
        "implication": "保留长表达、短证明、停顿、再回人物的节奏变化，不应把全片均匀切成短镜头。",
    },
    {
        "case": "long", "time": 190.5, "title": "片尾还有结构，不只是黑场",
        "ds": "最后内容段覆盖 186.067–209.998 秒，描述草坪航拍后切至黑场字母；结构上合并为情绪回落与片尾。",
        "independent": "约 190.5 秒后还包含音乐署名、引语、印记、人物座右铭回收、高空影像叠化和作者尾卡。",
        "evidence": "contact_5.jpg 与 boundarysheet_3.jpg 保留对应画面。",
        "implication": "长片尾段抽帧过疏会漏掉语义回收，不应把中间未见画面补写成持续黑场。",
    },
]

report = {
    "date": "2026-10-20",
    "scope": "两条真实样片下载、导入和现有工作台 DeepSeek 分析，与独立证据复核对照；未改变分析算法或覆盖模型结果。",
    "cases": cases,
    "total_deepseek_tokens": sum(c["usage"]["total_tokens"] for c in cases.values()),
    "total_deepseek_calls": sum(c["recorded_calls"] for c in cases.values()),
    "minimax_calls": 0,
    "findings": findings,
    "pipeline_observations": [
        "两次 audio_used_in_analysis 均为 false：视觉请求发出时本地转录尚未就绪，完成后仅附加到结果，没有重新推理。",
        "短片 ASR 自动判英语，语言概率 0.3068，文本不连贯；未经听审不能视为准确对白或歌词。",
        "长片仅送入 48 帧，短片 39 帧；独立侧采用更密取样和局部边界复核。因此这不是同输入的模型能力排名。",
        "界面把模型内容 segments 的数量标为镜头数，混淆了内容分段和真实切镜。",
    ],
    "recommended_order": [
        "分离镜头边界、镜头内动作、语义段落；场景检测只产出候选，抑制连续运镜误报并复核邻帧。",
        "等本地转录完成或明确失败后再融合视觉分析；低置信转录不充当确定对白。",
        "长片按镜头/语义块局部分析后汇总，保证首尾和短镜头覆盖，不让全片 48 帧上限稀释密集段。",
        "保留人的选择和修订记录；本次两条样片不支持训练模型或证明某种剪法导致高播放。",
    ],
    "limitations": [
        "独立判断先于对应 DeepSeek 输出冻结，但已知来源标题及短片评论，不是双盲评测。",
        "独立侧实际进行了连续取样帧、切点邻帧、字幕与本地 ASR 复核；没有完整音频听审，不评价音乐风格、BPM或精确卡点。",
        "长片独立侧查看 230 个取样帧位；短片查看 50 个顺序抽样位与 40 个候选邻帧位，不代表逐帧审阅。",
    ],
}
(ROOT / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

cards = []
for key in ("long", "short"):
    a = read(f"{key}-deepseek.json")["analysis"]
    independent = read(f"independent-{key}.json")
    rows = independent["semantic_sections"] if key == "long" else independent["shots"]
    file = "sources/long_BV1kT421r7vE_h264.mp4" if key == "long" else "sources/seoul-travel-7473440493129583907.mp4"
    def button(row, name):
        return f'<button data-video="{key}" data-time="{row["start"]}"><b>{timecode(row["start"])}–{timecode(row["end"])}</b> {esc(name)}</button>'
    own = "".join(button(s, s.get("name", s.get("description", ""))) for s in rows)
    ds = "".join(button(s, s["role"]) for s in a["structure"])
    links = "".join(f'<a href="{key}-{suffix}.json">{label}</a>' for suffix,label in [("deepseek","DS 原始输出"),("local-transcript","本地转录"),("usage","真实用量")])
    cards.append(f'''<section id="{key}">
      <div class="section-head"><h2>{esc(cases[key]["title"].removesuffix(".mp4"))}</h2>
      <span class="pill">{cases[key]["duration"]:.2f}s · {cases[key]["usage"]["total_tokens"]:,} tok</span></div>
      <video id="video-{key}" controls preload="metadata" src="{file}"></video>
      <div class="pair"><div><h3>独立复核 · {"语义段落" if key=="long" else "连续拍摄段"}</h3><div class="nodes">{own}</div></div>
      <div><h3>DeepSeek · 语义段落</h3><div class="nodes">{ds}</div></div></div>
      <p class="muted">DS：{cases[key]["ds_frames"]} 张输入帧 · {cases[key]["ds_content_segments"]} 个内容段 · 本次未读取音频转录</p>
      <div class="links">{links}<a href="independent-{key}.json">独立判断原始记录</a></div>
    </section>''')

finding_cards = "".join(f'''<article><p class="eyebrow">{"短片" if f["case"]=="short" else "长片"} ·
    <button class="jump" data-video="{f["case"]}" data-time="{f["time"]}">定位 {timecode(f["time"])}</button></p>
    <h3>{esc(f["title"])}</h3><div class="pair"><p><strong>DeepSeek</strong>{esc(f["ds"])}</p>
    <p><strong>独立复核</strong>{esc(f["independent"])}</p></div>
    <p class="muted">{esc(f["evidence"])}</p><p>{esc(f["implication"])}</p></article>''' for f in findings)
items = lambda values: "".join(f"<li>{esc(v)}</li>" for v in values)
document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>两条真实样片 · DeepSeek 对照</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f6f7fa;color:#20242d;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;line-height:1.7}}
main{{max-width:1120px;margin:auto;padding:48px 24px 80px}}h1{{font-size:36px;letter-spacing:-1.2px;line-height:1.25;margin:14px 0}}h2{{font-size:21px;margin:0}}h3{{font-size:17px;margin:8px 0}}
.eyebrow{{font-size:12px;letter-spacing:1px;color:#243beb}}.lead{{max-width:800px;font-size:19px}}.muted{{color:#697181;font-size:14px}}section,article{{background:white;border-radius:20px;padding:26px;margin:22px 0}}article{{border:1px solid #e4e7ee}}
.section-head{{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}.pill{{background:#f1f3fb;color:#243beb;border-radius:30px;padding:7px 14px;font-size:13px;white-space:nowrap}}
video{{display:block;width:100%;height:410px;object-fit:contain;background:#171a20;border-radius:12px;margin:22px 0}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:26px}}
.nodes{{display:flex;flex-direction:column;gap:8px}}button{{font:inherit;cursor:pointer}}.nodes button{{border:0;background:#f3f5fa;border-radius:10px;text-align:left;padding:10px 12px;color:#353b49;font-size:13px}}.nodes b{{display:block;font-variant-numeric:tabular-nums;color:#243beb}}
.nodes button:hover{{background:#e9edff}}strong{{display:block;color:#243beb;font-size:13px;margin-bottom:7px}}.jump{{border:0;background:none;color:#243beb;font-size:12px;padding:0}}
a{{color:#243beb;text-decoration:none}}a:hover{{text-decoration:underline}}.links{{display:flex;gap:18px;flex-wrap:wrap;font-size:13px}}details{{margin-top:24px}}summary{{cursor:pointer;color:#243beb}}.evidence{{width:100%;height:auto;display:block;margin:16px 0;border-radius:10px}}
li{{margin:10px 0}}@media(max-width:700px){{.pair{{grid-template-columns:1fr}}main{{padding:28px 14px}}section,article{{padding:18px}}video{{height:310px}}h1{{font-size:29px}}}}
</style><main><p class="eyebrow">REFERENCE REVIEW / 2026.10.20</p><h1>场景看懂了，剪法还要再核对。</h1>
<p class="lead">两条样片已导入工作台。DeepSeek 能形成可用的内容索引；当前流程仍把内容段混作镜头，并遗漏了声音驱动的语义转折。</p>
<p class="muted">2 次 DeepSeek · 36,951 tok · 0 次 MiniMax。两次原始结果保留，未覆盖、未训练模型。</p>
<nav class="links"><a href="#long">长片 03:30</a><a href="#short">短片 00:12.57</a><a href="#differences">具体差异</a><a href="http://127.0.0.1:8787/#reference">打开工作台</a><a href="comparison.json">完整对照记录</a></nav>
{''.join(cards)}<div id="differences"><h2>回到时间点核对</h2>{finding_cards}</div>
<section><h2>先改流程，再讨论模型优化</h2><ol>{items(report["recommended_order"])}</ol>
<h3>本次比较的边界</h3><ul>{items(report["pipeline_observations"]+report["limitations"])}</ul></section>
<section><h2>证据与来源</h2><p><a href="https://www.bilibili.com/video/BV1kT421r7vE/">长片原发布页 · 小壕h</a> · <a href="https://www.douyin.com/video/7473440493129583907">短片原发布页 · 冷面公主</a></p>
<details><summary>短片五处切镜与同镜头运动误报</summary><img class="evidence" src="independent-short/cut-neighbors.jpg"></details>
<details><summary>长片开头：先表达，再换镜</summary><img class="evidence" src="independent-long/contact_1.jpg"></details>
<details><summary>长片后段：高密度展示后再放慢</summary><img class="evidence" src="independent-long/contact_4.jpg"></details>
<details><summary>工作台导入记录</summary><img class="evidence" src="workbench-imported.png"></details></section>
</main><script>document.querySelectorAll('[data-video]').forEach(b=>b.addEventListener('click',()=>{{const v=document.getElementById('video-'+b.dataset.video);v.currentTime=Number(b.dataset.time);v.scrollIntoView({{behavior:'smooth',block:'center'}});}}));</script></html>'''
(ROOT / "拉片对照.html").write_text(document, encoding="utf-8")
print(json.dumps({"report": str(ROOT / "拉片对照.html"), "calls": report["total_deepseek_calls"], "tokens": report["total_deepseek_tokens"]}, ensure_ascii=False))
