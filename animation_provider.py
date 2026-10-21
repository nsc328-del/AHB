"""DeepSeek-authored animation plans, source revisions and bounded render repair.

No hand-authored scenes substitute for a provider result. Every response and
render attempt is checkpointed next to the private source revision.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from pathlib import Path

import providers

PLAN_SCHEMA = providers._object({
    "title": {"type": "string", "minLength": 1, "maxLength": 100},
    "scenes": {"type": "array", "minItems": 1, "maxItems": 6, "items": providers._object({
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "description": {"type": "string", "minLength": 10, "maxLength": 4000},
        "duration": {"type": "number", "minimum": 2, "maximum": 30},
    })},
})
SCENE_SCHEMA = PLAN_SCHEMA["properties"]["scenes"]["items"]
SOURCE_SCHEMA = providers._object({
    "html": {"type": "string", "minLength": 100, "maxLength": 100000},
    "design_notes": {"type": "string", "maxLength": 2000},
})
PATCH_SCHEMA = providers._object({
    "edits": {"type": "array", "minItems": 1, "maxItems": 24, "items": providers._object({
        "old": {"type": "string", "minLength": 1, "maxLength": 6000},
        "new": {"type": "string", "maxLength": 12000},
    })},
    "design_notes": {"type": "string", "maxLength": 2000},
})


def apply_source_edits(original, edits):
    result = original
    for edit in edits:
        old, new = edit['old'], edit['new']
        if not old or result.count(old) != 1:
            raise ValueError('局部修改未能唯一定位旧源码，请提供包含相邻代码的精确 old 字符串')
        result = result.replace(old, new, 1)
    return result


REVIEW_SCHEMA = providers._object({
    "passed": {"type": "boolean"},
    "issues": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 600}},
})

PLAN_SYSTEM = """你是中文动态图形短片的编导。输出可由HTML/CSS/SVG/Canvas表达的分段方案。
内容文字、图形关系、动作、时序、终态要具体，兼顾中文阅读时间。根据需求独立构图，不把每段都做成相同卡片列表。
采用用户给定事实，不造经营数据、产品能力、客户背书或虚假实拍。信息不足时避开未证实数字。
无音频输入时按无旁白、无配乐设计，用文字与图形讲清楚，不承诺已经生成音轨。
描述里写明实际画面文字和出现时间；用自然中文让用户可审阅。每段独立起止，不依赖上一段的运行状态。
style只是表达方向而非固定模板。free可自主设计；type侧重字体编排；data仅使用给定数字；flow侧重步骤因果。
片段时长总和必须等于用户目标时长；一般2至4段，每段2至30秒。不要生成源码。"""
SOURCE_SYSTEM = """你是动态图形设计师与前端动画工程师。根据已确认单段脚本，实际编写完整的自包含HTML动画。
这是生产视频源码，不是网页原型。输出独立HTML文档（含style和script），由固定时间轴逐帧导出。
硬约定：
1. window.renderAt(t) 是唯一场景驱动入口，t为该片段的秒数；支持任意顺序重复调用，画面只由t决定。初始化时调用renderAt(0)。
2. 根元素id为stage，尺寸使用给定width/height CSS px，body margin:0，overflow:hidden；不要responsive rem/vh布局。动画不得依赖帧历史或真实时钟。
3. 禁止requestAnimationFrame、setInterval、setTimeout、Date、performance.now、Math.random、fetch、XMLHttpRequest、WebSocket、import、外链、iframe、视频音频元素、浏览器对话框、文件读取、导航、localStorage。无外网资源，不引用CDN，不用外部图片。
4. 使用本机字体 'PingFang SC','Arial',sans-serif；中文用正常字重和合理换行，避免细小字、文字挤压、裁切和重叠。内容元素保持画幅内至少40px安全边距，正文至少26px（720px高度）；段落不超过4行。文字可用DOM或SVG以便程序诊断。
5. 动画用绝对时间计算所有属性。CSS动画/transition禁止；元素初始布局只创建一次，renderAt内设置transform/opacity/clip-path等或清屏重绘Canvas。可用自写easeOut函数，不依赖三方库。
6. 画面首帧必须有真实视觉内容，中末段信息可读。不要整屏字幕卡堆叠；根据内容自主设计字体、几何、线条、层次、运动和留白。无预制模板约束，不抄案例。
7. 只实现当前场景，不添加其他片段、播放按钮、网页导航、说明文案或开发者标签。用户指定实际文字须完整保留；未经证实数字不要补造。
8. 不向任意网络/系统发送信息。不要尝试绕过资源约束。最后10%时间清晰停留，避免末帧全部退场或空白。
若提供旧版HTML和修改指令，只改变该指令涉及的内容，保留其他表达。若提供程序诊断或视觉问题，修复具体问题。
返回JSON html与design_notes。"""


def _context(context: dict) -> dict:
    return {key: context.get(key) for key in ("title", "brief", "style", "ratio", "width", "height") if context.get(key) is not None}


def _request(name, schema, system, payload, *, images=None, checkpoint=None):
    """Persist raw response text before structural validation, never credentials."""
    sink_token = None
    if checkpoint is None:
        import storage as db
        import usage
        job_id = (usage.CONTEXT.get() or {}).get('job_id')
        if job_id:
            checkpoint = db.DATA / 'animation_sources' / 'responses' / job_id / name
    system += '\n仅返回符合以下JSON Schema的对象，禁止Markdown代码围栏、禁止添加未定义字段：' + json.dumps(schema, ensure_ascii=False)
    if checkpoint:
        checkpoint = Path(checkpoint)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        def sink(text, status):
            checkpoint.with_suffix('.response.json').write_text(json.dumps({"status": status, "text": text}, ensure_ascii=False, indent=2))
        sink_token = providers._ANALYSIS_TEXT_SINK.set(sink)
    def read_complete_response():
        import jsonschema
        saved = checkpoint.with_suffix('.response.json') if checkpoint else None
        if not saved or not saved.is_file():
            return None
        receipt = json.loads(saved.read_text())
        if receipt.get('status') != 'completed':
            return None
        text = receipt.get('text', '').strip()
        match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1)
        try:
            result = json.loads(text)
            jsonschema.validate(result, schema)
        except (ValueError, jsonschema.ValidationError):
            return None
        return result
    try:
        result = read_complete_response()
        if result is None:
            try:
                import usage
                with usage.context(operation=name):
                    result = providers._json_response(name, schema, system, json.dumps(payload, ensure_ascii=False), images=images)
            except providers.ProviderError:
                # A complete, strictly valid JSON payload inside one code fence
                # needs no second provider call. Its original response is kept.
                result = read_complete_response()
                if result is None:
                    raise
    finally:
        if sink_token is not None:
            providers._ANALYSIS_TEXT_SINK.reset(sink_token)
    if checkpoint:
        checkpoint.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 2 <= value <= 30:
        raise ValueError("动画片段时长须为2至30秒")
    return round(round(float(value) * 30) / 30, 6)


def plan(params: dict) -> dict:
    target = float(params.get('duration', 20))
    payload = {key: params.get(key) for key in ('brief', 'style', 'ratio', 'duration')}
    result = _request('animation_plan', PLAN_SCHEMA, PLAN_SYSTEM, payload)
    scenes = [{"id": uuid.uuid4().hex, **row, "duration": _duration(row['duration'])} for row in result['scenes']]
    if abs(sum(s['duration'] for s in scenes)-target) > .1:
        raise ValueError("方案总时长与目标不符，请重新生成方案")
    return {"title": result['title'], "scenes": scenes}


def revise_scene(scene: dict, context: dict, instruction: str) -> dict:
    result = _request('animation_scene_revision', SCENE_SCHEMA,
        PLAN_SYSTEM + '\n本次只修改给定的一段，按用户要求修改文案、表达和时长。未要求改动的内容保持。',
        {"context": _context(context), "scene": {k: scene.get(k) for k in ('title','description','duration')}, "instruction": instruction})
    return {"id": scene['id'], **result, "duration": _duration(result['duration'])}


def generate_and_render(scene: dict, context: dict, output_path, progress=None, prior_source=None, instruction=None) -> dict:
    import animation_renderer
    import storage as db
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Private source and model responses never enter /api/media or the app origin.
    private = db.DATA / 'animation_sources' / hashlib.sha256((('anchor-v2:' if prior_source else '') + str(output)).encode()).hexdigest()[:24]
    private.mkdir(parents=True, exist_ok=True)
    width, height = int(context.get('width',1280)), int(context.get('height',720))
    duration = _duration(scene['duration'])
    design = {"context": _context(context), "scene": {key: scene[key] for key in ('title','description','duration')},
              "width": width, "height": height, "fps": 30}
    if prior_source:
        design['prior_html'] = prior_source
    if instruction:
        design['instruction'] = instruction
    input_fingerprint=hashlib.sha256(json.dumps(design,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    audit = {"provider": "deepseek", "model": providers.DEEPSEEK_MODEL, "attempts": [], "human_source_edits": 0}
    previous_issues=[]
    # Successful output is reused on local recovery, not sent to a model again.
    completed = private/'complete.json'
    if completed.is_file() and output.is_file():
        saved=json.loads(completed.read_text())
        if saved.get('input_fingerprint') == input_fingerprint:
            return saved['result']
    for attempt in range(2):
        entry={"attempt": attempt+1}
        if progress: progress({"stage":"source","progress":5+attempt*5,"message":"编写动画" if not attempt else "修正动画"})
        source_file=private/f'attempt-{attempt+1}.json'
        payload={**design, **({"diagnostics":previous_issues} if previous_issues else {})}
        if source_file.is_file():
            response=json.loads(source_file.read_text())
        else:
            if design.get('prior_html'):
                patch=_request('animation_source_patch', PATCH_SCHEMA,
                    SOURCE_SYSTEM.replace('返回JSON html与design_notes。', '') + '\n本次只输出精确文本替换 edits。old 必须逐字匹配 prior_html 中唯一位置，new 为替换内容；使用尽可能小的上下文，禁止重写无关布局/参数/动画。仅改一句文案时只替换这一处文字，不重新实现动画；诊断修复时只改有证据的问题。不要返回 html 字段。',
                    payload,checkpoint=private/f'attempt-{attempt+1}-patch')
                try:
                    html=apply_source_edits(design['prior_html'],patch['edits'])
                except ValueError as error:
                    previous_issues=[str(error)];audit['attempts'].append({'attempt':attempt+1,'status':'patch_failed','diagnostics':previous_issues})
                    (private/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
                    continue
                response={'html':html,'design_notes':patch.get('design_notes'),'patches':patch['edits']}
                source_file.write_text(json.dumps(response,ensure_ascii=False,indent=2))
            else:
                response=_request('animation_source', SOURCE_SCHEMA, SOURCE_SYSTEM,payload,checkpoint=private/f'attempt-{attempt+1}')
        html=response['html']
        (private/f'attempt-{attempt+1}.html').write_text(html)
        if context.get('source_path'):
            source_path=Path(context['source_path']).resolve()
            if not source_path.is_relative_to((db.DATA/'animation_sources').resolve()):
                raise ValueError('动画源码路径无效')
            source_path.parent.mkdir(parents=True,exist_ok=True)
            source_path.write_text(html)
        entry['source_hash']=hashlib.sha256(html.encode()).hexdigest()
        candidate=output.with_name(output.stem+f'-attempt-{attempt+1}'+output.suffix)
        try:
            render_file=private/f'render-{attempt+1}.json'
            if render_file.is_file() and candidate.is_file():
                rendered=json.loads(render_file.read_text())
            else:
                rendered=animation_renderer.render_scene(html,candidate,width=width,height=height,duration=duration,fps=30,progress=progress)
                render_file.write_text(json.dumps(rendered,ensure_ascii=False,indent=2))
            entry['renderer']=rendered
            keyframes=rendered.get('checkpoints',[])
            images=[{"path":str(frame['path']),"label":f"第{float(frame['time']):.2f}秒渲染结果"} for frame in keyframes if Path(frame['path']).is_file()]
            if not images: raise ValueError('动画未产出可检查的关键帧')
            review_file=private/f'review-{attempt+1}.json'
            if review_file.is_file():
                review=json.loads(review_file.read_text())
            else:
                review=_request('animation_visual_review',REVIEW_SCHEMA,
                    '你是动画输出质检。只检查实际渲染帧与脚本。关键错误包括乱码、持续遮挡、内容错误、整段空白。必须逐帧按其确切time判断：5.0秒帧绝不能要求5.5秒才出现的字幕；刚到入场边界的元素可仍在淡入，0秒空白但随后正常是可接受入场。未采样到某文案不能等同整段缺失，不推测帧间运动。visible_text是程序计算的可见DOM文字，可辅助确认，仍需检查图像是否遮挡；无该证据时不伪造。不要把局部短暂留白或个人审美偏好当失败。局部修改时优先验证修改目标，未改部分不要求重新设计。若无有据可核对的关键错误passed=true且issues为空。',
                    {"scene":design['scene'],"instruction":instruction,"design_notes":response.get('design_notes'),"program_diagnostics":rendered.get('diagnostics',[]),"frames":[{"time":f["time"],"visible_text":f.get("visible_text")} for f in keyframes]},
                    images=images,checkpoint=private/f'review-{attempt+1}')
            entry['review']=review
            if not review['passed']:
                previous_issues=review['issues'] or ['视觉核对未通过，请检查所有文字与布局']
                entry['status']='needs_repair'
            else:
                candidate.replace(output)
                entry['status']='succeeded';audit['attempts'].append(entry)
                result={"source_html":html,"source_hash":entry['source_hash'],"renderer":{**rendered,"output_path":str(output)},"audit":audit}
                completed.write_text(json.dumps({"input_fingerprint":input_fingerprint,"result":result},ensure_ascii=False,indent=2))
                return result
        except (ValueError, RuntimeError) as error:
            if isinstance(error,providers.ProviderError): raise
            previous_issues=[str(error)[:1000]]
            previous_issues.extend(json.dumps(item,ensure_ascii=False)[:1500] for item in getattr(error, "diagnostics", [])[:12])
            entry.update(status='failed',diagnostics=previous_issues)
        audit['attempts'].append(entry)
        (private/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
        design['prior_html']=html
    visible_issues = []
    for issue in previous_issues:
        try:
            parsed = json.loads(issue)
            issue = parsed.get('message', '') if isinstance(parsed, dict) else issue
        except ValueError:
            pass
        if issue:
            visible_issues.append(issue)
    raise ValueError('动画经过一次修正仍未通过检查：'+'；'.join(visible_issues)[:250])
