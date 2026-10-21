export const BRAND = {
  name: '隆莱呷卤肉饭',
  note: '示例运营计划',
  source: {
    title: '南方+：罗麦克与隆莱呷品牌',
    url: 'https://finance.sina.com.cn/roll/2026-09-04/doc-iniqsvkn5822119.shtml',
    date: '2026-09-04',
  },
};

export const MONTHS = [
  { month: 1, theme: '新年第一碗', focus: '从开门、盛饭和第一勺卤汁，记录新一年的日常。', events: [{ day: 1, label: '元旦' }, { day: 12, label: '开店日常' }, { day: 26, label: '月末选片' }] },
  { month: 2, theme: '一起好好吃', focus: '拍一个人和两个人吃饭时，各自喜欢的搭配。', events: [{ day: 6, label: '一人一碗' }, { day: 14, label: '两人同桌' }, { day: 26, label: '月末选片' }] },
  { month: 3, theme: '午休有饭吃', focus: '跟着一份午餐，从柜台点单拍到坐下开吃。', events: [{ day: 4, label: '午休一餐' }, { day: 18, label: '取餐片刻' }, { day: 27, label: '月末选片' }] },
  { month: 4, theme: '街角烟火气', focus: '把街道、门头和店里的饭香连成一条短片。', events: [{ day: 8, label: '街角门店' }, { day: 19, label: '周末进店' }, { day: 28, label: '月末选片' }] },
  { month: 5, theme: '假日也吃饭', focus: '用真实到店路线和用餐画面，讲清一顿饭的体验。', events: [{ day: 1, label: '五一' }, { day: 15, label: '店员吃法' }, { day: 27, label: '月末选片' }] },
  { month: 6, theme: '日常有滋味', focus: '把浇汁、拌饭、夹起一口这些细节拍清楚。', events: [{ day: 5, label: '浇汁特写' }, { day: 17, label: '一口搭配' }, { day: 26, label: '月末选片' }] },
  { month: 7, theme: '夏日一顿饭', focus: '记录夏天进店、落座和端上一碗饭的片刻。', events: [{ day: 7, label: '进店片刻' }, { day: 18, label: '夏日午餐' }, { day: 28, label: '月末选片' }] },
  { month: 8, theme: '饭点的日常', focus: '用柜台和厨房的真实动作，呈现门店一天的节奏。', events: [{ day: 6, label: '开门之前' }, { day: 19, label: '饭点记录' }, { day: 27, label: '月末选片' }] },
  { month: 9, theme: '回到午餐桌', focus: '把工作日午餐拍得具体，让人看见怎么点、怎么吃。', events: [{ day: 2, label: '工作日午餐' }, { day: 16, label: '一人食搭配' }, { day: 28, label: '月末选片' }] },
  { month: 10, theme: '秋日一碗热饭', focus: '围绕热饭质感、午休一餐和店员吃法，月底回看选题。', events: [{ day: 1, label: '国庆' }, { day: 21, label: '午休一餐' }, { day: 23, label: '秋日热饭' }, { day: 28, label: '月底复盘' }] },
  { month: 11, theme: '热饭与日常', focus: '让端饭、拌饭和开吃的声音成为画面的主角。', events: [{ day: 5, label: '一碗热饭' }, { day: 17, label: '门店原声' }, { day: 27, label: '月末选片' }] },
  { month: 12, theme: '这一年的饭', focus: '从一年留下的门店画面中，挑出人和饭的记忆。', events: [{ day: 8, label: '店员记忆' }, { day: 23, label: '年度选片' }, { day: 31, label: '年末回看' }] },
];

export const SEED_IDEAS = [
  {
    id: 'lailai-lunch', title: '今天午休，好好吃一顿', category: '日常一餐',
    angle: '跟着一份卤肉饭，从点单到坐下开吃；保留真实的柜台声和碗筷声。',
    platform: 'douyin', duration: 20, date: '2026-10-21',
    shots: ['门口走入，带到柜台', '盛饭后浇一勺卤汁', '端碗落桌，掰开筷子', '拌匀后夹起一口'],
    rationale: '用具体的午休场景，回应工作日吃什么的日常选择。',
  },
  {
    id: 'lailai-sauce', title: '这一勺，慢一点看', category: '食物特写',
    angle: '围绕浇卤汁的一个动作拍质感，镜头靠近米饭，少说话。',
    platform: 'xiaohongshu', duration: 20, date: '2026-10-21',
    shots: ['近拍盛好的米饭', '侧面拍卤汁落下', '勺子轻轻拌开', '停在夹起的一口饭'],
    rationale: '食物的颜色、质感和动作，适合形成容易看懂的短内容。',
  },
  {
    id: 'lailai-staff', title: '店员自己怎么吃这碗饭', category: '门店人物',
    angle: '请一位愿意出镜的店员，现场演示自己真实喜欢的吃法。',
    platform: 'channels', duration: 30, date: '2026-10-21',
    shots: ['店员端出自己的一碗饭', '说一句个人吃法', '手部演示拌饭或搭配', '近拍完成后的这一口'],
    rationale: '把产品放进一个人的日常，比统一口号更容易讲出细节。',
  },
  {
    id: 'lailai-first-bowl', title: '开门后，第一碗饭', category: '门店日常',
    angle: '记录门店开门后的真实动作，从准备餐具到端出第一碗。',
    platform: 'douyin', duration: 20, date: '2026-10-22',
    shots: ['打开店门和灯', '摆好碗与勺', '盛饭浇汁', '端到出餐台'],
    rationale: '开店过程能提供一组有先后关系、便于剪辑的真实素材。',
  },
  {
    id: 'lailai-autumn', title: '秋日里，端上一碗热饭', category: '季节日常',
    angle: '从街边走进店内，用端饭和拌饭衔接室外与餐桌。',
    platform: 'xiaohongshu', duration: 20, date: '2026-10-23',
    shots: ['拍门店外的秋日街景', '镜头跟着端饭的手', '近拍刚端上的饭', '拌饭后停留在桌面'],
    rationale: '用当季环境连接一顿饭，不依赖降价或上新的信息。',
  },
  {
    id: 'lailai-corner', title: '从街角走到这张桌子', category: '到店记录',
    angle: '沿真实路线拍门头、进店和落座，让观众看见这家店的样子。',
    platform: 'xiaohongshu', duration: 30, date: '2026-10-24',
    shots: ['门口附近的街景', '正面拍清门头', '从门口走向座位', '桌上放下一碗饭'],
    rationale: '周末到店内容可以多留一点空间给街景与用餐环境。',
  },
  {
    id: 'lailai-sounds', title: '不用配乐，听一顿饭', category: '门店原声',
    angle: '用勺子、碗筷和出餐的声音串联，试一条不靠旁白的短片。',
    platform: 'douyin', duration: 20, date: '2026-10-26',
    shots: ['勺子轻碰锅沿', '浇汁落在米饭上', '碗放上桌面', '筷子拌开米饭'],
    rationale: '换一种声音表达，避免连续几条都变成相同口播。',
  },
  {
    id: 'lailai-month-review', title: '十月，留下这几个饭点', category: '月末回看',
    angle: '从本月实际拍到的画面中，选三组不同的人、饭和门店片刻。',
    platform: 'channels', duration: 30, date: '2026-10-28',
    shots: ['一段开店或备餐画面', '一段浇汁与拌饭特写', '一段已获同意的人物画面', '门头或餐桌作结'],
    rationale: '月底复用真实素材，并回看哪些表达值得下月继续尝试。',
  },
];

export const SIGNALS = [
  {
    id: 'signal-local-food', title: '地方风味，成为到店理由',
    observation: '国庆餐饮报道关注地方特色美食，地方风味与出游需求相互带动。',
    date: '2026-10-10', sourceName: '新华网 · 央广网',
    url: 'https://www1.xinhuanet.com/food/20261010/e2387b900b8c4938857460e812e8a1e5/c.html',
    angle: '请店员用实际出品演示一碗饭的搭配与吃法。',
    idea: {
      title: '一碗卤肉饭，可以怎么吃', category: '吃法分享',
      angle: '从真实在售的出品里选一碗，请店员边做边讲自己的搭配。',
      platform: 'xiaohongshu', duration: 30,
      shots: ['拍清这一碗的组成', '店员介绍个人搭配', '手部演示吃法', '夹起一口作结'],
      rationale: '从熟悉的吃法里，拍出一碗饭的个人偏好。',
    },
  },
  {
    id: 'signal-daily-lunch', title: '从假日客流，回到日常午餐',
    observation: '暑期结束后，协会报告关注校园周边与写字楼的日常用餐需求。',
    date: '2026-09-24', sourceName: '中国饭店协会 · 8月报告',
    url: 'https://www.chinahotel.org.cn/articles/18011',
    angle: '记录一个普通工作日，从柜台点单到端饭落座。',
    idea: {
      title: '普通工作日的一顿午饭', category: '日常一餐',
      angle: '拍一个真实午餐过程，保留柜台动作和落座后的饭桌细节。',
      platform: 'douyin', duration: 20,
      shots: ['柜台点单的手部', '盛饭并浇卤汁', '端碗走向餐桌', '落座后开吃'],
      rationale: '把工作日的一顿午饭，拍成可被记住的日常。',
    },
  },
  {
    id: 'signal-solo-meal', title: '工作日的一人食，单独讲',
    observation: '平日的工作餐、一人食，与假日聚餐有不同的消费场景。',
    date: '2026-07-24', sourceName: '中国饭店协会 · 半年观察',
    url: 'https://www.chinahotel.org.cn/articles/17915',
    angle: '用一张桌子、一碗饭和几声碗筷，拍完整的一人午餐。',
    idea: {
      title: '一个人，也坐下来吃饭', category: '一人食',
      angle: '镜头留在一个人的餐桌，拍端饭、拌饭和开吃的顺序。',
      platform: 'xiaohongshu', duration: 20,
      shots: ['把碗放到单人餐位', '拆开筷子', '拌饭时拍清质感', '夹起一口并留一点原声'],
      rationale: '给一个人吃饭的时刻，留下一点完整的空间。',
    },
  },
];
