# 手绘进入现实：本地适配

上游：https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/handdrawn-live-video-generator

原始文件保存在 upstream/，逐文件 SHA-256 见 UPSTREAM.json。此文件说明适配方式，不修改上游原文。

## 当前可执行路径

用户需求与已上传素材 → DeepSeek 按完整官方规范生成镜头表 → 用户审阅 → H3 逐镜头生成 → 下载入库 → FFmpeg 代码剪辑。

`create_generation_storyboard` 读取本技能的原文和引用规范，输出角色/场景锚点、缺失输入、镜头连续性、视听提示词和真实素材 ID；`create_generation` 执行 H3 请求。

## 运行环境映射

| 上游依赖 | 本项目实现 |
|---|---|
| canvas nodes | 本地素材记录、生成分镜、任务与产物 |
| choice cards | 可审阅镜头表与逐镜头生成按钮 |
| hub_analyse_media | DeepSeek 视觉抽帧与已有本地语音转录；不推测音乐或音效 |
| hub_generate_video | MiniMax-H3 v2 API，4–15 秒、768P/2K |
| hub_video_edit | Python/FFmpeg，保持来源与剪辑区间 |
| hub_generate_image | 未接入；使用用户上传的角色/产品/场景锚点 |
| hub_generate_music / hub_generate_audio | 未接入；独立配乐须上传，H3原生声音可按提示词生成 |
| Hub媒体搜索、网页搜索 | 不自动替用户寻找或上传外部素材 |
| Seedance fallback | 不自动改换模型或额外计费 |

## 本技能的内容规则

单场景里安排手绘与真实表面的物理接触、连续变形和逃逸路径，手持镜头延迟跟随，保留粗糙发光笔触且避免恐怖。

所有默认风格、文案语言、配乐与字幕规则服从当前用户需求。缺失锚点写入 missing_inputs，不能宣称已经生成。分镜不是成片，单镜头生成不是整片完成。
