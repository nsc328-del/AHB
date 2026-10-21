// Each style-to-preview association comes from the official skills README.
// Remote originals are embedded directly; no generated substitutes or local copies.
const SOURCE = 'https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/README.md';
const MEDIA = 'https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/assets/';
const styles = [
  ['minimalist-product-ad-generator', '极简产品广告', 360, 203],
  ['3d-animation-short-generator', '3D 叙事短片', 360, 202],
  ['papercraft-stop-motion-explainer', '纸艺定格解说', 360, 202],
  ['brand-promo-video-generator', '品牌宣传短片', 360, 203],
  ['music-video-subtitle-generator', '音乐与动态字幕', 360, 202],
  ['co-op-game-intro-generator', '双人游戏开场', 360, 202],
  ['paper-collage-explainer-generator', '纸质拼贴解说', 360, 202],
  ['handdrawn-live-video-generator', '手绘进入现实', 360, 202],
];

// The general prompting skill has no dedicated style preview.
export const GENERATION_PREVIEWS = Object.freeze({
  'h3-prompt-writing': null,
  ...Object.fromEntries(styles.map(([id, title, width, height]) => [id, Object.freeze({
    title,
    src: `${MEDIA}${id}.gif`,
    kind: 'image',
    animated: true,
    content_type: 'image/gif',
    width,
    height,
    source_url: `${SOURCE}#${id}`,
    attribution: 'MiniMax H3 官方示例',
    verified_at: '2026-10-20',
  })])),
});

export function getGenerationPreview(skillId) {
  return GENERATION_PREVIEWS[skillId] || null;
}
