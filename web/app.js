'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v === null || v === undefined ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(v);
const compact = v => v === null || v === undefined ? '—' : new Intl.NumberFormat('ru-RU', {notation:'compact',maximumFractionDigits:1}).format(v);
const date = v => v ? new Date(typeof v === 'number' ? v * 1000 : v).toLocaleDateString('ru-RU') : '—';
const time = v => v ? new Date(v*1000).toLocaleString('ru-RU', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const url = v => { try { const u = new URL(v); return u.protocol === 'https:' ? esc(u.href) : ''; } catch { return ''; } };
const signed = n => `${n > 0 ? '+' : ''}${fmt(n)}`;
let data = {games:[],meta:[],settings:{},runs:[],counts:{}};
let view = 'radar', platform = 'all', query = '', sort = 'score', onlyGrowth = false, mechanic = '', currentKey = '', wasRunning = false, lastLoad = 0;
let busy = false, toastTimer;
// Static mode: the GitHub Pages copy reads prebuilt JSON (tools/build_static.py) instead of the local Python API.
const STATIC = document.documentElement.dataset.static === '1';
const fileKey = key => String(key).replace(/[^A-Za-z0-9_-]/g, '_'); // must match file_key() in tools/build_static.py
const views = {
  radar:['Радар игр','Что набирает игроков','Сравнивайте размер аудитории, свежесть и измеренный рост.'],
  new:['Новые хиты','Новые игры. Заметный спрос.','Релизы и проекты моложе выбранного порога, а также подборка Roblox Up-and-Coming.'],
  upcoming:['Ожидаемые релизы','Спрос до первого запуска','Steam Most Wishlisted: позиция в выбранной витрине и её изменение за сутки.'],
  meta:['Механики и мета','Какие игровые циклы востребованы','Сопоставляйте сигналы Roblox и Steam и выбирайте гипотезу для прототипа.'],
  watchlist:['Наблюдение','Ваш список наблюдения','Игры продолжают собираться, даже когда покидают витринные подборки.'],
  logs:['Журнал источников','Проверка качества данных','Каждый сбор: охват подборок, полученные игры и ошибки источников.'],
  method:['Как считаем','От цифр к гипотезе','Что измеряет радар, откуда берутся оценки и как читать сигналы.']
};

async function staticApi(path, values) {
  if (values !== undefined) throw new Error('Это публичная копия только для чтения');
  const u = new URL(path, location.href.replace(/[^/]*$/, ''));
  const route = u.pathname.slice(u.pathname.indexOf('/api/'));
  const files = {'/api/dashboard':'data/dashboard.json', '/api/status':'data/status.json'};
  const file = route === '/api/history' ? `data/history/${fileKey(u.searchParams.get('key') || '')}.json` : files[route];
  if (!file) throw new Error('Не найдено');
  const response = await fetch(file, {cache:'no-cache'});
  if (route === '/api/history' && response.status === 404) return [];
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const result = await response.json();
  if (route !== '/api/history') return result;
  const since = Date.now() / 1000 - Number(u.searchParams.get('days') || 30) * 86400;
  return result.filter(p => p.ts >= since);
}
async function api(path, values) {
  if (STATIC) return staticApi(path, values);
  const response = await fetch(path, values === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden=true,5000); }
async function load() { data = await api('api/dashboard'); lastLoad = Date.now(); render(); }
function overview() {
  const c=data.counts;
  $('#new-count').textContent=fmt(c.new_hits || 0);
  $('#watch-count').textContent=fmt(data.games.filter(g=>g.watched).length);
  $('#overview').innerHTML=[
    ['В поле зрения', fmt(c.total||0), 'уникальных игр в локальной базе'],
    ['Roblox',fmt(c.roblox||0),'Charts + ваш список наблюдения'],
    ['Steam',fmt(c.steam||0),'новинки, продажи и ожидание'],
    ['Сигналы роста',fmt(c.breakouts||0),'прирост за сутки выше порога']
  ].map(x=>`<div><div class="stat-label">${x[0]}</div><div class="stat-value">${x[1]}</div><div class="stat-note">${x[2]}</div></div>`).join('');
}
function setView(next) { view=next; query=''; mechanic=''; onlyGrowth=false; platform=next==='upcoming'?'steam':'all'; render(); }
function render() {
  const v=views[view]; $('#breadcrumb').textContent=v[0]; $('#page-title').textContent=v[1]; $('#page-description').textContent=v[2];
  $$('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view)); overview();
  const last = data.runs[0];
  const errors = last?.result?.errors || [];
  $('#notice').hidden = errors.length===0;
  $('#notice').textContent=`Последний сбор: ${errors.length} ошибок источников. Часть данных может быть неполной; подробности в журнале.`;
  if(view==='method') return renderMethod();
  if(view==='logs') return renderLogs();
  if(view==='meta') return renderMeta();
  renderTable();
}
function filtered() {
  let games=data.games.filter(g=>{
    if(view==='upcoming' && !g.upcoming) return false;
    if(view==='radar' && g.upcoming) return false;
    if(view==='new' && (!((g.recent||g.recent_chart)&&!g.upcoming) || (g.latest.ccu||0)<data.settings.min_players))return false;
    if(view==='watchlist'&&!g.watched)return false;
    if(platform!=='all'&&g.platform!==platform)return false;
    if(onlyGrowth&&!g.breakout)return false;
    if(mechanic&&!g.mechanics.some(m=>m.id===mechanic))return false;
    return `${g.name} ${g.creator} ${g.mechanics.map(m=>m.label).join(' ')}`.toLowerCase().includes(query.toLowerCase());
  });
  games.sort((a,b)=>{
    if(sort==='players')return (b.latest.ccu??-1)-(a.latest.ccu??-1);
    if(sort==='growth')return (b.growth['24h']?.delta??-Infinity)-(a.growth['24h']?.delta??-Infinity);
    if(sort==='age')return (a.age_days??Infinity)-(b.age_days??Infinity);
    if(sort==='rank')return (a.latest.wishlist_rank??Infinity)-(b.latest.wishlist_rank??Infinity);
    return b.score-a.score;
  }); return games;
}
function sparkline(points) {
  const valid=points.filter(p=>p.ccu!==null&&p.ccu!==undefined);
  if(valid.length<2)return '<svg class="sparkline" viewBox="0 0 100 35" aria-label="Недостаточно истории"><text x="4" y="22">Копим историю</text></svg>';
  const min=Math.min(...valid.map(p=>p.ccu)),max=Math.max(...valid.map(p=>p.ccu)),start=valid[0].ts,end=valid.at(-1).ts;
  const interval = (data.settings.interval_minutes||60)*60;
  let previous=null;
  const d=valid.map(p=>{const move=previous===null||p.ts-previous>interval*2.5; previous=p.ts;return `${move?'M':'L'}${(3+(p.ts-start)/(end-start||1)*94).toFixed(1)},${(29-(p.ccu-min)/(max-min||1)*24).toFixed(1)}`;}).join(' ');
  return `<svg class="sparkline" viewBox="0 0 100 35" aria-label="Онлайн за последние замеры"><path d="${d}"/></svg>`;
}
function growthCell(g) {
  if(g.upcoming){const r=g.rank_change;return r?`<span class="number ${r.delta<0?'positive':r.delta>0?'negative':''}">${r.delta<0?'↑':r.delta>0?'↓':'→'} ${fmt(Math.abs(r.delta))}</span><span class="sub-number">мест за ${fmt(r.hours)} ч</span>`:'<span class="sub-number">Копим историю</span>';}
  const d=g.growth['24h'];
  if(!d)return '<span class="sub-number">Копим историю</span>';
  return `<span class="number ${d.delta>=0?'positive':'negative'}">${d.pct===null?'с нуля':signed(d.pct)+'%'}</span><span class="sub-number">${signed(d.delta)} · ${fmt(d.hours)} ч</span>`;
}
function renderTable() {
  const upcoming=view==='upcoming';
  $('#content').innerHTML=`<div class="panel"><div class="panel-header"><div><h3>${esc(views[view][0])}</h3><p>${upcoming?'Места относятся к выбранной витрине Steam, категория «игры».':'Каждая строка — наблюдение. Откройте игру, чтобы увидеть историю и основания сигнала.'}</p></div><span class="panel-label">LIVE OBSERVATIONS</span></div><div class="filters">
  <div class="segmented" aria-label="Платформа">${[['all','Все'],['roblox','Roblox'],['steam','Steam']].map(([id,label])=>`<button data-platform="${id}" class="${platform===id?'active':''}" aria-pressed="${platform===id}">${label}</button>`).join('')}</div>
  <select id="mechanic-filter" aria-label="Механика"><option value="">Все механики</option>${data.meta.map(m=>`<option value="${esc(m.id)}" ${mechanic===m.id?'selected':''}>${esc(m.label)}</option>`).join('')}</select>
  <select id="sort-filter" aria-label="Сортировка">${[['score','По силе сигнала'],['players','По онлайну'],['growth','По приросту игроков'],['age','По новизне'],['rank','По месту в ожидании']].map(([id,label])=>`<option value="${id}" ${sort===id?'selected':''}>${label}</option>`).join('')}</select>
  ${!upcoming?`<label class="filter-check"><input type="checkbox" id="growth-filter" ${onlyGrowth?'checked':''}> Только рост</label>`:''}<div class="search"><input id="search" type="search" placeholder="Поиск по играм и механикам" aria-label="Поиск игр" value="${esc(query)}"></div></div>
  <div id="table-area"></div></div>`;
  $$('[data-platform]').forEach(b=>b.onclick=()=>{platform=b.dataset.platform;renderTable();});
  $('#sort-filter').onchange=e=>{sort=e.target.value;renderRows();};
  $('#mechanic-filter').onchange=e=>{mechanic=e.target.value;renderRows();};
  if($('#growth-filter'))$('#growth-filter').onchange=e=>{onlyGrowth=e.target.checked;renderRows();};
  $('#search').oninput=e=>{query=e.target.value;renderRows();};
  renderRows();
}
function renderRows() {
  const games=filtered();
  if(!games.length){$('#table-area').innerHTML=`<div class="empty"><div class="empty-mark">⌁</div><h2>${data.games.length?'Нет игр по этим условиям':'Первый сигнал начинается с замера'}</h2><p>${data.games.length?'Измените платформу, поиск или фильтры. Для сигналов роста нужны замеры с разницей примерно в сутки.':'Нажмите «Собрать данные». Радар найдёт игры в публичных подборках Roblox и Steam и начнёт сохранять историю.'}</p>${!data.games.length?'<button class="button primary" id="empty-scan">Собрать первый срез</button>':''}</div>`;if($('#empty-scan'))$('#empty-scan').onclick=startScan;return;}
  const upcoming=view==='upcoming';
  $('#table-area').innerHTML=`<div class="table-scroll"><table><thead><tr><th>Игра / механика</th><th>${upcoming?'Релиз':'Возраст'}</th><th class="num">${upcoming?'Most Wishlisted':'Игроков сейчас'}</th><th class="num">${upcoming?'Движение за сутки':'Рост за сутки'}</th><th class="optional-col">История онлайна</th><th class="num">Сигнал ⓘ</th><th><span aria-label="Наблюдать">☆</span></th></tr></thead><tbody>${games.map(g=>`<tr class="${g.stale?'stale-row':''}"><td><div class="game-cell">${g.image?`<img class="game-image ${g.platform}" src="${url(g.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<div class="game-image"></div>'}<div><button class="game-name" data-detail="${esc(g.key)}">${esc(g.name)}</button><div class="game-sub"><span>${g.platform==='roblox'?'Roblox':'Steam'}</span>${g.mechanics.slice(0,2).map(m=>`<span class="tag">${esc(m.label)}</span>`).join('')}${g.breakout?`<span class="tag green">${g.sustained?'Устойчивый рост':'Взлёт'}</span>`:''}${g.stale?'<span class="tag coral">Устарело</span>':''}</div></div></div></td>
  <td>${g.upcoming?`<span>${esc(g.release_text||'Не объявлен')}</span><span class="sub-number">Ещё не вышла</span>`:`<span>${g.age_days===null?'Неизвестен':fmt(Math.floor(g.age_days))+' дн.'}</span><span class="sub-number">${g.date_is_proxy?'создание проекта':'с релиза'}${g.release_override?' · вручную':''}</span>`}</td>
  <td class="num"><span class="number">${g.upcoming?(g.latest.wishlist_rank?'№'+fmt(g.latest.wishlist_rank):'—'):fmt(g.latest.ccu)}</span><span class="sub-number">${g.upcoming?'косвенный спрос':time(g.latest.ts)}</span></td><td class="num">${growthCell(g)}</td><td class="optional-col">${g.upcoming?'<span class="sub-number">До релиза</span>':sparkline(g.sparkline)}</td><td class="num"><span class="score" title="${g.upcoming?'Оценка ожидания':'Оценка популярности, роста и свежести'}; откройте «Как считаем»">${fmt(g.score)}</span></td><td><button class="star ${g.watched?'saved':''}" data-watch="${esc(g.key)}" aria-label="${g.watched?'Убрать из наблюдения':'Наблюдать за игрой'}" aria-pressed="${g.watched}">${g.watched?'★':'☆'}</button></td></tr>`).join('')}</tbody></table></div><div class="table-foot"><span>${fmt(games.length)} игр · ${esc(data.settings.country)} · последние доступные замеры</span><span>CCU = игроки одновременно. Сигнал 0–100 — эвристика, не прогноз.</span></div>`;
  bindGames($('#table-area'));
}
function bindGames(root) {
  $$('[data-detail]',root).forEach(b=>b.onclick=()=>openDetail(b.dataset.detail));
  $$('[data-watch]',root).forEach(b=>b.onclick=async()=>{const g=data.games.find(x=>x.key===b.dataset.watch);try{await api('/api/game',{key:g.key,watched:!g.watched});g.watched=!g.watched;render();if($('#detail-dialog').open)detailHeaderWatch();}catch(e){toast(e.message);}});
}
function renderMeta() {
  if(!data.meta.length){$('#content').innerHTML='<div class="panel empty"><h2>Сначала нужны игры</h2><p>Соберите данные: механики определяются по открытым тегам, названию и описанию игр.</p></div>';return;}
  $('#content').innerHTML=`<div class="meta-intro">Это срез собранной выборки. «Новый хит» = свежая игра с онлайном от ${fmt(data.settings.min_players)}. Классификация по словам и тегам может ошибаться; несколько механик у одной игры пересекаются. Идеи ниже — гипотезы для проверки.</div><div class="meta-grid">${data.meta.map(m=>`<article class="mechanic-card"><div class="mechanic-heading"><h3>${esc(m.label)}</h3>${m.cross_platform?'<span class="tag green">Спрос на обеих платформах</span>':`<span class="index">${m.signals} сигн.</span>`}</div><div class="mechanic-stats">${['roblox','steam'].map(p=>{const s=m.stats[p];return `<div class="mechanic-platform"><b>${p==='roblox'?'Roblox':'Steam'}</b><strong>${s.new_hits} <small>новых хитов</small></strong><span>${s.games} игр в выборке · ${compact(s.ccu)} онлайн</span><span>${s.growing} сигналов роста${p==='steam'?` · ${s.upcoming} ожидаемых`:''}</span><span>Медиана роста: ${s.median_growth===null?'нет истории':signed(s.median_growth)+'%'} · n=${s.growth_sample}</span></div>`;}).join('')}</div><div class="idea"><small>ГИПОТЕЗА ДЛЯ ROBLOX</small>${esc(m.idea)}</div><div class="examples">${m.examples.map(g=>`<button class="example" data-detail="${esc(g.key)}">${esc(g.name)} ↗</button>`).join('')}</div></article>`).join('')}</div>`;bindGames($('#content'));
}
function renderLogs() {
  if(!data.runs.length){$('#content').innerHTML='<div class="panel empty"><h2>Сбор ещё не запускался</h2><p>После первого сбора здесь появятся источники, охват и ошибки.</p></div>';return;}
  $('#content').innerHTML=`<div class="panel">${data.runs.map(run=>{const r=run.result,s=r.saved||{},errors=r.errors||[];return `<article class="log-entry"><div class="log-title"><h3>${time(run.started)}</h3><span class="tag ${!run.finished?'blue':errors.length?'coral':'green'}">${!run.finished?'Не завершён':errors.length?'Частичные данные':'Сбор завершён'}</span></div><p>Roblox: ${fmt(s.roblox||0)} · Steam: ${fmt(s.steam||0)} · ${run.finished?Math.round((run.finished-run.started)/60)+' мин.':'Идёт сбор или процесс был остановлен'}</p>${(r.feeds||[]).map(f=>`<span class="tag">${esc(f)}</span> `).join('')}${errors.length?`<details><summary>${errors.length} ошибок источников</summary><ul>${errors.map(e=>`<li class="error-text">${esc(e)}</li>`).join('')}</ul></details>`:''}</article>`;}).join('')}</div>`;
}
function renderMethod() {
  $('#content').innerHTML=`<article class="panel method"><h2>Сначала измерение, затем решение</h2><p>Радар обнаруживает кандидатов в публичных подборках и затем отслеживает их во времени. Это ограниченная выборка, а не каталог всех игр. Место в витрине зависит от региона и устройства; онлайн относится ко всей игре.</p>
  <h3>Откуда берутся игры</h3><ul><li>Roblox: Top Trending, Up-and-Coming, Top Playing Now и другие доступные Charts. Рекламные карточки исключаются. Метаданные, онлайн, визиты, избранное и голоса — публичные API Roblox.</li><li>Steam: popularnew, новые релизы по дате, Most Wishlisted, topsellers. У каждой ленты свой лимит. Онлайн — Steam Web API; описание, дата, жанры и отзывы — Steam Store.</li><li>Избранные отслеживаются постоянно, до 300 прочих игр на платформу продолжают отслеживаться в течение 14 дней после последнего успешного сбора. Полный охват рынка не гарантируется.</li></ul>
  <h3>Свежесть и ожидание</h3><p>В Steam используем дату из карточки магазина; Early Access, повторный запуск и выход из раннего доступа могут менять её смысл. Для Roblox известна дата создания проекта: это приблизительный ориентир. Если знаете фактическую дату запуска — укажите её в карточке. Up-and-Coming выделяется отдельно как сигнал подборки.</p><p>У ожидаемых Steam-игр показываем позицию в Most Wishlisted для выбранного региона и категории «игры». Точные чужие wishlist не публикуются. Разница мест — изменение позиции, а не прирост числа ожидающих. Отсутствие позиции означает «вне полученной выборки или нет данных».</p>
  <h3>Что считается ростом</h3><p>Сравниваем ближайший собственный замер около 1 ч / 24 ч / 7 дн. Допуск — 25% окна, минимум 5 минут. В карточке видна фактическая длина сравнения. При нулевой базе проценты не вычисляются. Пропущенные значения не превращаются в нули. Повторные сборы чаще 10 минут не создают новые точки.</p><p>«Взлёт»: за сутки прирост не меньше ${data.settings.growth_alert_pct}% и минимум ${fmt(data.settings.min_players)} игроков, текущий онлайн не меньше ${fmt(data.settings.min_players)}. «Устойчивый рост»: дополнительно последние три замера за 6 часов выше вчерашней базы и покрывают минимум 2 часа. Это не проверка отсутствия ботов, рекламы или сезонных всплесков.</p>
  <h3>Оценка сигнала: 0–100</h3><p>Для вышедших игр: <code>40% размер + 35% рост + 15% свежесть + 10% качество</code>. Размер: <code>min(1, log10(CCU+1)/5)</code>. Рост: <code>min(1, gain/max(100,base)/2) × min(1,gain/500)</code>. Свежесть линейно снижается за выбранный срок новизны. Качество — доля положительных оценок сверх 50%, нормированная до 1. Без истории вклад роста равен нулю.</p><p>Для ожидаемых игр: <code>80 / rank^0.30</code> плюс до 20 баллов за улучшение места за сутки (2 балла за место). Эти две оценки имеют разный смысл. Сравнивайте их в своих разделах; оценка не является вероятностью успеха.</p>
  <h3>Как читать мету</h3><p>Механики определяются явными правилами по Steam tag ID, жанрам, названию и описанию. Основания видны в карточке. Одна игра может входить в несколько групп; суммировать группы нельзя. Для каждого рынка отдельно показаны новые хиты, растущие игры, онлайн и медианный рост с размером выборки. Это не измерение насыщенности рынка и не рекомендация копировать чужие материалы.</p>
  <h3>История и работа по расписанию</h3><p>SQLite хранится в <code>data/radar.sqlite3</code>. Онлайн — моментальный срез, а не DAU, продажи или уникальные игроки. Пики — только максимум собранных точек. Свежесть теряется через максимум из 3 часов и двух интервалов. Устаревшие игры исключаются из сводки меты. Для сравнения одинакового времени суток оставьте сбор работать хотя бы 48–72 часа.</p><p>Автосбор работает, пока запущена программа. Команда <code>python app.py --collect</code> делает один сбор для внешнего планировщика. Исторические значения до первого запуска не выдумываются. Публичные витринные API могут менять формат; ошибки видны в журнале.</p>
  <h3>Первоисточники</h3><p><a href="https://partner.steamgames.com/doc/webapi/ISteamUserStats" target="_blank" rel="noopener noreferrer">Steam: число текущих игроков ↗</a> · <a href="https://partner.steamgames.com/doc/marketing/wishlist/reporting" target="_blank" rel="noopener noreferrer">Steam: отчётность wishlist ↗</a> · <a href="https://create.roblox.com/docs/discovery" target="_blank" rel="noopener noreferrer">Roblox Discovery ↗</a></p></article>`;
}

async function openDetail(key) {
  const g=data.games.find(x=>x.key===key);if(!g)return;currentKey=key;
  const dialog=$('#detail-dialog');
  $('#detail-content').innerHTML=`<div class="detail-top"><div class="dialog-heading"><div><div class="eyebrow">${g.platform.toUpperCase()} · ${esc(g.creator)}</div><h2>${esc(g.name)}</h2><div>${g.breakout?'<span class="tag green">Сигнал роста</span> ':''}${g.recent_chart?'<span class="tag blue">Up-and-Coming</span> ':''}${g.upcoming?'<span class="tag blue">Ожидаемый релиз</span>':''}</div></div><button data-close="detail-dialog" class="icon-button" aria-label="Закрыть">×</button></div><div class="detail-links"><a class="button quiet" href="${url(g.url)}" target="_blank" rel="noopener noreferrer">Открыть игру ↗</a><button id="detail-watch" class="button quiet"></button></div></div>
  <div class="detail-body"><div class="detail-desc">${esc(g.description||'Описание недоступно')}</div><div class="detail-metrics"><div><small>${g.upcoming?'Место Most Wishlisted':'Игроков сейчас'}</small><b>${g.upcoming?(g.latest.wishlist_rank?'№'+fmt(g.latest.wishlist_rank):'—'):fmt(g.latest.ccu)}</b></div><div><small>${g.platform==='roblox'?'Визитов всего':'Отзывов всего'}</small><b>${compact(g.platform==='roblox'?g.latest.visits:g.latest.reviews)}</b></div><div><small>Положительных оценок</small><b>${g.latest.positive_pct==null?'—':fmt(g.latest.positive_pct)+'%'}</b></div><div><small>Оценка сигнала</small><b>${fmt(g.score)} <small>/ 100</small></b></div></div>
  <div class="chart-box"><div class="chart-title"><b>${g.upcoming?'Позиция Most Wishlisted (меньше — выше)':'История одновременного онлайна'}</b><select id="history-days" aria-label="Период истории"><option value="2">48 часов</option><option value="7">7 дней</option><option value="30" selected>30 дней</option></select></div><div id="history-chart" class="loader">Загрузка истории…</div><div id="history-caption" class="muted"></div></div>
  <div class="detail-columns"><div><h3>Основания сигнала</h3><ul>${g.reasons.map(r=>`<li>${esc(r)}</li>`).join('')||'<li>Пока только первичный срез</li>'}${g.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul><div class="detail-source">Замер: ${time(g.latest.ts)} · впервые замечена: ${date(g.first_seen)}<br>${esc(g.scope||'')}<br>${g.platform==='roblox'?'Избранное: '+fmt(g.latest.favorites):'Прирост отзывов за сутки: '+(g.review_growth?signed(g.review_growth.delta):'нет истории')}</div><h3>Распознанные механики</h3>${g.mechanics.map(m=>`<div class="evidence"><b>${esc(m.label)}</b><span>Основания: ${esc(m.evidence.join(', '))}</span><span>${esc(m.idea)}</span></div>`).join('')||'<p class="muted">Правила не нашли явных признаков. Изучите игру вручную.</p>'}</div>
  <form id="note-form"><h3>Заметки для прототипа</h3><label>Игровой цикл, крючок, что проверить<textarea name="note" maxlength="10000" placeholder="Что игрок делает в первые 30 секунд?">${esc(g.note)}</textarea></label>${g.platform==='roblox'?`<label>Фактическая дата запуска, если известна<input type="date" name="release_override" value="${esc(g.release_override||'')}" max="${new Date().toISOString().slice(0,10)}"></label><p class="muted">Пустое поле оставит дату создания проекта как ориентир.</p>`:''}<button class="button primary" type="submit">Сохранить заметки</button><p id="note-message" class="form-message" role="status"></p></form></div></div>`;
  if(!dialog.open)dialog.showModal();
  bindClose(dialog); detailHeaderWatch();
  $('#history-days').onchange=()=>loadHistory(g);
  if(STATIC){const f=$('#note-form');if(!g.note)f.remove();else $$('textarea,input',f).forEach(x=>x.readOnly=true);}
  if($('#note-form'))$('#note-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const values={key:g.key,note:f.get('note')};if(g.platform==='roblox')values.release_override=f.get('release_override')||null;try{await api('/api/game',values);await load();$('#note-message').textContent='Сохранено';}catch(err){$('#note-message').textContent=err.message;}};
  await loadHistory(g);
}
function detailHeaderWatch() {
  const g=data.games.find(x=>x.key===currentKey),button=$('#detail-watch');if(!g||!button)return;
  button.textContent=g.watched?'★ В наблюдении':'☆ Наблюдать';button.setAttribute('aria-pressed',String(g.watched));
  button.onclick=async()=>{try{await api('/api/game',{key:g.key,watched:!g.watched});g.watched=!g.watched;detailHeaderWatch();render();}catch(e){toast(e.message);}};
}
async function loadHistory(g) {
  const days=$('#history-days').value;
  try {const points=await api(`api/history?key=${encodeURIComponent(g.key)}&days=${days}`);if(currentKey!==g.key)return;
    const field=g.upcoming?'wishlist_rank':'ccu';
    const valid=points.filter(p=>p[field]!==null&&p[field]!==undefined);
    $('#history-chart').className='';$('#history-chart').innerHTML=chart(valid,field,g.upcoming);
    $('#history-caption').textContent=valid.length?`${valid.length} замеров · ${time(valid[0].ts)} — ${time(valid.at(-1).ts)}${!g.upcoming?' · наблюдаемый пик: '+fmt(Math.max(...valid.map(p=>p.ccu))):''}`:'Доступных замеров нет. Оставьте сбор работать.';
  }catch(e){if(currentKey===g.key)$('#history-chart').textContent=e.message;}
}
function chart(points,field,invert) {
  if(!points.length)return '<svg class="chart" viewBox="0 0 820 210"><text class="empty-text" x="270" y="105">История пока не накоплена</text></svg>';
  let low=Math.min(...points.map(p=>p[field])),high=Math.max(...points.map(p=>p[field]));
  if(high===low){low=Math.max(0,low-1);high+=1;}
  const start=points[0].ts,end=points.at(-1).ts;
  const x=p=>60+(p.ts-start)/(end-start||1)*735;
  const y=p=>invert?22+(p[field]-low)/(high-low)*153:175-(p[field]-low)/(high-low)*153;
  let prev=null;const d=points.map(p=>{const move=prev===null||p.ts-prev>(data.settings.interval_minutes||60)*60*2.5;prev=p.ts;return `${move?'M':'L'}${x(p).toFixed(2)},${y(p).toFixed(2)}`;}).join(' ');
  return `<svg class="chart" viewBox="0 0 820 210" role="img" aria-label="${invert?'Рейтинг ожидания':'Онлайн'} по времени">${[0,.5,1].map(t=>`<line class="grid" x1="60" x2="795" y1="${22+t*153}" y2="${22+t*153}"/><text x="3" y="${26+t*153}">${esc(compact(invert?low+(high-low)*t:high-(high-low)*t))}</text>`).join('')}<path class="line" d="${d}"/>${points.filter((p,i)=>i===0||i===points.length-1||points.length<60).map(p=>`<circle cx="${x(p)}" cy="${y(p)}" r="2.7"><title>${time(p.ts)}: ${fmt(p[field])}</title></circle>`).join('')}<text x="60" y="204">${time(start)}</text><text x="795" y="204" text-anchor="end">${time(end)}</text></svg>`;
}
function bindClose(root=document){$$('[data-close]',root).forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());}
async function startScan(){try{const r=await api('/api/scan',{});if(!r.started)toast('Сбор уже идёт');await pollStatus();}catch(e){toast(e.message);}}
async function pollStatus(){
  try{const s=await api('api/status');busy=s.running;$('#scan-button').disabled=busy;$('#scan-button').textContent=busy?'↻ Сбор идёт…':'↻ Собрать данные';
    $('#status-dot').className=`status-dot ${busy?'running':s.errors?.length?'error':''}`;
    $('#status-label').textContent=busy?s.message:(s.finished?s.message:'Готов к сбору');
    $('#status-time').textContent=busy?`Roblox ${s.saved.roblox} · Steam ${s.saved.steam}`:s.next_scan?`Следующий сбор: ${time(s.next_scan)}`:'Автосбор выключен';
    if(STATIC){if(s.generated_at!==data.generated_at)await load().catch(e=>toast('Ошибка обновления: '+e.message));}
    else if((wasRunning&&!busy)||(!busy&&Date.now()-lastLoad>60000))await load().catch(e=>toast('Ошибка обновления: '+e.message));
    wasRunning=busy;
  }catch(e){$('#status-label').textContent=STATIC?'Не удалось получить статус':'Нет связи с программой';$('#status-time').textContent=STATIC?'Обновите страницу позже':'Запустите start.bat и обновите страницу';$('#status-dot').className='status-dot error';}
}
$$('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));
bindClose();
$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('#scan-button').onclick=startScan;
$('#add-button').onclick=()=>{$('#add-message').textContent='';$('#add-dialog').showModal();};
$('#settings-button').onclick=()=>{const form=$('#settings-form');Object.entries(data.settings).forEach(([k,v])=>{const el=form.elements.namedItem(k);if(el){if(el.type==='checkbox')el.checked=v;else el.value=v;}});$('#settings-message').textContent='';$('#settings-dialog').showModal();};
$('#settings-form').onsubmit=async e=>{e.preventDefault();const form=e.target,values={};new FormData(form).forEach((v,k)=>values[k]=['country','device'].includes(k)?v:Number(v));values.auto_scan=form.elements.auto_scan.checked;try{await api('/api/settings',values);await load();$('#settings-dialog').close();toast('Настройки сохранены');await pollStatus();}catch(err){$('#settings-message').textContent=err.message;}};
$('#add-form').onsubmit=async e=>{e.preventDefault();try{const r=await api('/api/add',{url:new FormData(e.target).get('url')});if(!r.started){$('#add-message').textContent='Дождитесь окончания текущего сбора';return;}$('#add-dialog').close();toast('Получаем игру. Результат появится в наблюдении; ошибки — в журнале.');await pollStatus();}catch(err){$('#add-message').textContent=err.message;}};
load().then(pollStatus).catch(e=>{$('#content').innerHTML=`<div class="panel empty"><h2>Не удалось загрузить данные</h2><p>${esc(e.message)}</p></div>`;});
if(STATIC){
  $('#csv-link').href='data/export.csv';
  $('.local-label').innerHTML='<i></i> Публичная копия только для чтения<br><span>Сбор раз в час через GitHub Actions</span>';
}
setInterval(pollStatus,STATIC?120000:3000);
