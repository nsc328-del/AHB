// Display helpers only. Plans, ideas and source records come from /api/operations.
export const PLATFORMS = {douyin:'抖音',xiaohongshu:'小红书',channels:'视频号'};
export const WEEKDAYS = ['日','一','二','三','四','五','六'];
export const pad = n => String(n).padStart(2,'0');
export const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
export const parseDate = value => new Date(`${value}T12:00:00`);
export const shiftDate = (value,days) => {const date=parseDate(value);date.setDate(date.getDate()+days);return dateKey(date)};
export const chinaToday = () => new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
export const isDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value||'') && dateKey(parseDate(value))===value;
export const dayLabel = value => {const d=parseDate(value);return `${d.getMonth()+1}月${d.getDate()}日`};
export const safeURL = value => {try {const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:''} catch {return ''}};

export const chinaDateTime = (value,timeOnly=false) => {
  if(!value)return '时间未知';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '时间未知';
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hourCycle:'h23',...(timeOnly?{}:{year:'numeric',month:'2-digit',day:'2-digit'}),hour:'2-digit',minute:'2-digit'}).format(date).replace(/\//g,'-');
};

export const prioritizeHotspots = items => items
  .filter(item=>item.source_id==='soso_douyin'&&item.kind==='platform_hotspot'&&item.content_category)
  .sort((a,b)=>Number(a.freshness!=='recent')-Number(b.freshness!=='recent')||(a.rank??999)-(b.rank??999)||String(a.id).localeCompare(String(b.id)));
