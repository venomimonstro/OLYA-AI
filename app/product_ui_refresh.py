from __future__ import annotations

import re

from starlette.responses import HTMLResponse


_MARKER = "OLYA_UI_REFRESH_V1"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def _inject_style(document: str, css: str) -> str:
    nonce = _nonce(document)
    attr = f' nonce="{nonce}"' if nonce else ""
    return document.replace(
        "</head>",
        f'<style{attr}>/*{_MARKER}*/\n{css}\n</style></head>',
        1,
    )


def _inject_script(document: str, script: str) -> str:
    nonce = _nonce(document)
    attr = f' nonce="{nonce}"' if nonce else ""
    return document.replace("</body>", f'<script{attr}>{script}</script></body>', 1)


def _refresh_workspace(document: str) -> str:
    if _MARKER in document:
        return document
    if 'id="view-chat"' not in document or 'id="project-chat"' not in document:
        return document

    document = document.replace("<title>X1 — рабочее пространство</title>", "<title>OLYA AI — чат и рабочее пространство</title>", 1)
    document = document.replace('<div class="brand">X1 AI</div>', '<div class="brand">OLYA AI</div>', 1)
    document = document.replace(
        "Локальная Qwen, файлы проекта, интернет-поиск и проверка ответа в одном рабочем пространстве.",
        "Пишите, прикладывайте файлы, подключайте интернет и продолжайте работу в одном диалоге.",
        1,
    )

    composer_marker = '<div class="composer-foot"><span class="health" id="counter"></span>'
    if composer_marker in document:
        document = document.replace(
            composer_marker,
            '<div class="composer-foot"><button class="attach" id="chat-attach" type="button" title="Прикрепить файл к выбранному проекту" aria-label="Прикрепить файл">+</button><span class="attachment-state" id="chat-attachment-state"></span><span class="health" id="counter"></span>',
            1,
        )

    css = r'''
:root{--bg:#ffffff;--side:#f7f7f8;--panel:#ffffff;--panel2:#f7f7f8;--line:#e5e7eb;--text:#171717;--muted:#6b7280;--accent:#111827;--ok:#16825d;--warn:#a16207;--bad:#c24141;--code:#111827}
html,body{background:#fff;color:var(--text);color-scheme:light}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.shell{grid-template-columns:260px minmax(0,1fr);background:#fff}.side{background:#f7f7f8;border-right:0;padding:10px 9px}.brand{letter-spacing:-.02em;font-size:16px;padding:10px 10px 13px;color:#111827}.nav{gap:2px}.navbtn{color:#242424;border-radius:9px;padding:9px 10px}.navbtn:hover,.navbtn.active{background:#ececee;color:#111}.new{order:-1;margin:0 0 9px;border:0;background:transparent;color:#202020;text-align:left;font-weight:600;padding:10px;border-radius:9px}.new:hover{background:#ececee}.side-section{color:#8b8b92;margin-top:17px;text-transform:none;letter-spacing:0;font-size:12px}.conv{color:#34343a}.conv:hover,.conv.active{background:#ececee}.account-mini{border-color:#e6e6e8;color:#777}.linkbtn{color:#36363a}.main{background:#fff}.top{min-height:56px;border-bottom:0;background:rgba(255,255,255,.92);backdrop-filter:blur(12px);padding:8px 18px}.page-title{color:#202124}.top select,.field,input[type=text],input[type=file]{background:#fff;color:#252525;border:1px solid #dedee3;border-radius:10px}.health,.budget{color:#7b7b82}.content{width:min(1040px,100%);padding-top:34px}.content h1{font-size:30px;color:#161616}.lead,.muted{color:#6f6f76}.card{background:#fff;border:1px solid #e7e7ea;border-radius:16px;box-shadow:0 1px 2px rgba(0,0,0,.025)}.item{background:#fff;border-color:#e7e7ea}.primary,.secondary,.danger{color:#202020;border-color:#dedee3}.primary{background:#111827;color:#fff}.secondary{background:#fff}.danger{background:#fff5f5;color:#a92727}.messages{padding-top:24px}.chat-empty{margin-top:17vh}.chat-empty h1{font-size:38px;font-weight:650;letter-spacing:-.045em;color:#202020}.chat-empty p{font-size:16px;color:#7a7a80}.role{display:none}.msg{max-width:800px;margin-bottom:28px}.user{display:flex;justify-content:flex-end}.user .bubble{max-width:min(76%,680px);background:#f4f4f4;border:0;color:#232323;padding:10px 15px;border-radius:20px}.assistant .bubble{color:#202124;font-size:16px;line-height:1.72}.bubble blockquote{color:#5f6368}.codewrap{background:#111827;border-color:#202938}.codehead{color:#c8ced8;border-color:#2b3544}.copy{background:#202938;color:#f5f7fa;border-color:#344156}.tablewrap{border-color:#e2e3e6}.bubble th{background:#f8f8f9}.source a{color:#2457a6}.composer-wrap{border-top:0;padding:12px 20px 20px;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 28%)}.composer{max-width:790px;background:#fff;border:1px solid #d9d9de;border-radius:26px;padding:9px 10px;box-shadow:0 7px 28px rgba(0,0,0,.08)}textarea{color:#202124;min-height:46px;padding:8px 10px}.composer-foot{min-height:38px}.send{background:#111827;border-radius:999px;min-width:92px;padding:8px 14px}.send.stop{background:#b73e3e}.attach{width:34px;height:34px;border-radius:999px;border:1px solid #dedee3;background:#fff;color:#303034;font-size:22px;line-height:1;padding:0;display:inline-grid;place-items:center}.attach:hover{background:#f3f3f5}.attachment-state{max-width:270px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#6f6f76;font-size:12px}.progress,.state{max-width:790px}.step{color:#8b8b92;border-color:#e0e0e4}.step.active{color:#8b5b00;border-color:#dec892}.step.done{color:#16825d}.status{border-color:#dedee3}#nav-files,#view-files,#workspace-files,.card:has(#workspace-files-list){display:none!important}.support-link{color:#242424!important}.account-plan.current{border-color:#aeb4bd!important}.account-progress{accent-color:#111827!important}
@media(max-width:760px){.shell{grid-template-columns:1fr}.side{background:#f7f7f8;box-shadow:25px 0 70px rgba(0,0,0,.12)}.mobile-menu{color:#222;border-color:#dedee3}.top{background:#fff}.composer-wrap{padding:8px 9px 12px}.composer{border-radius:22px}.user .bubble{max-width:88%}.chat-empty{margin-top:12vh}.chat-empty h1{font-size:32px}.attachment-state{max-width:140px}}
'''
    document = _inject_style(document, css)

    script = r'''
(function(){
  const attach=document.getElementById('chat-attach');
  const input=document.getElementById('file-input');
  const chatProject=document.getElementById('project-chat');
  const filesProject=document.getElementById('files-project');
  const state=document.getElementById('state');
  const attachState=document.getElementById('chat-attachment-state');
  if(!attach||!input||!chatProject||!filesProject||attach.dataset.bound==='1')return;
  attach.dataset.bound='1';
  attach.addEventListener('click',function(){
    if(!chatProject.value){
      state.textContent='Чтобы прикрепить файл, сначала выберите проект в верхней панели. Файл станет частью контекста этого проекта.';
      return;
    }
    filesProject.value=chatProject.value;
    input.click();
  });
  input.addEventListener('change',async function(){
    if(!input.files||!input.files.length)return;
    if(!chatProject.value){state.textContent='Выберите проект перед загрузкой файла.';input.value='';return;}
    filesProject.value=chatProject.value;
    const name=input.files[0].name;
    attachState.textContent='Загрузка: '+name;
    try{
      await uploadProjectFile();
      const fileState=document.getElementById('file-state');
      attachState.textContent=name;
      state.textContent=(fileState&&fileState.textContent)||'Файл добавлен в контекст проекта. Можно задавать вопрос по нему прямо в чате.';
    }catch(e){
      attachState.textContent='';
      state.textContent=e&&e.message?e.message:'Не удалось загрузить файл.';
    }
  });
})();
'''
    return _inject_script(document, script)


def _refresh_landing(document: str) -> str:
    if _MARKER in document:
        return document
    if 'id="features"' not in document or 'id="pricing"' not in document or 'js-register-cta' not in document:
        return document

    document = document.replace('<a class="brand" href="/">X1 AI</a>', '<a class="brand" href="/">OLYA AI</a>', 1)

    hero = '''<section class="wrap hero"><div class="hero-copy"><span class="eyebrow">OLYA AI · одна среда вместо десятка AI-инструментов</span><h1>Не просто отвечает. <span>Доводит работу до результата.</span></h1><p class="lead">Чат, свежий интернет-поиск, файлы, проекты, код, изображения и агентные сценарии в одном интерфейсе. Начните бесплатно и подключайте сложность только тогда, когда она действительно нужна.</p><div class="actions hero-actions"><a class="btn primary large js-register-cta" href="/register">Попробовать бесплатно</a><a class="btn large ghost" href="/#features">Что умеет OLYA AI</a></div><div class="hero-note">30 запросов в день на Free · без привязки карты · лимит виден заранее</div></div><div class="hero-card future-window"><div class="mock-top"><span></span><span></span><span></span><b>OLYA AI</b></div><div class="mock-body"><div class="mock-prompt">Проанализируй этот файл, проверь свежие данные и подготовь план запуска продукта.</div><div class="mock-tools"><span>Файл</span><span>Интернет</span><span>Проверка</span><span>Агенты</span></div><div class="mock-answer"><strong>Готово. Собрала решение в 4 шага</strong><p>Сверила вводные, нашла актуальные данные, выделила риски и сформировала план действий.</p><div class="mock-lines"><i></i><i></i><i></i></div></div></div></div></section>'''
    document = re.sub(r'<section class="wrap hero">.*?</section>', hero, document, count=1, flags=re.S)

    trust = '''<section class="wrap trust-row"><div class="trust"><b>30 запросов в день бесплатно</b><span>Можно нормально протестировать продукт до оплаты.</span></div><div class="trust"><b>Файлы прямо в чате</b><span>Загружайте документ и сразу продолжайте диалог по нему.</span></div><div class="trust"><b>Проекты и память</b><span>Контекст не нужно объяснять заново в каждом сообщении.</span></div><div class="trust"><b>Локальное AI-ядро</b><span>Основная модель работает без покупки чужого LLM API.</span></div></section>'''
    document = re.sub(r'<section class="wrap trust-row">.*?</section>', trust, document, count=1, flags=re.S)

    features = '''<section class="section" id="features"><div class="wrap"><div class="section-head"><span class="kicker">ВОЗМОЖНОСТИ</span><h2>Один чат — много способов выполнить задачу</h2><p>Не нужно думать, какой отдельный сервис открыть. Вы описываете результат, OLYA AI подключает нужный режим и инструменты.</p></div><div class="cards feature-cards"><div class="card"><span class="feature-no">01</span><h3>Работа с файлами в диалоге</h3><p>Документы становятся частью контекста проекта: можно анализировать, сравнивать и продолжать работу без отдельного файлового раздела.</p></div><div class="card"><span class="feature-no">02</span><h3>Свежий поиск в интернете</h3><p>Для актуальных задач система подключает поиск и отделяет найденные факты от предположений.</p></div><div class="card"><span class="feature-no">03</span><h3>Fast / Work / Deep</h3><p>Быстрые вопросы не тратят лишние ресурсы, а сложные получают более глубокую обработку.</p></div><div class="card"><span class="feature-no">04</span><h3>Агенты и команды агентов</h3><p>Задачу можно разложить на роли: исследование, проверка, исполнение и контроль результата.</p></div><div class="card"><span class="feature-no">05</span><h3>Код и разработка проектов</h3><p>Работа с кодом, изолированное выполнение и развитие проекта остаются частью общей AI-среды.</p></div><div class="card"><span class="feature-no">06</span><h3>Изображения и мультимодальность</h3><p>Визуальные инструменты развиваются внутри того же продукта, без переключения между разными кабинетами.</p></div></div></div></section>'''
    document = re.sub(r'<section class="section" id="features">.*?</section>', features, document, count=1, flags=re.S)

    flow = '''<section class="section flow-section"><div class="wrap"><div class="flow-shell"><div class="section-head"><span class="kicker">КАК ЭТО РАБОТАЕТ</span><h2>От сообщения до готового результата</h2><p>Интерфейс остаётся простым, даже когда внутри выполняется сложная цепочка действий.</p></div><div class="flow-grid"><div><span>1</span><h3>Опишите задачу</h3><p>Обычным языком, как человеку. Можно сразу приложить файл.</p></div><div><span>2</span><h3>OLYA AI подключит нужное</h3><p>Режим ответа, проектный контекст, интернет, проверку или агентный сценарий.</p></div><div><span>3</span><h3>Продолжайте в том же чате</h3><p>Уточняйте, исправляйте и развивайте результат без потери контекста.</p></div></div></div></div></section>'''
    document = document.replace('<section class="section" id="pricing">', flow + '<section class="section" id="pricing">', 1)
    document = document.replace("Тарифы без скрытых списаний", "Начните бесплатно. Платите только когда OLYA AI уже полезна.", 1)
    document = document.replace("Фактические лимиты показываются в аккаунте. Оплата активирует тариф только после серверного подтверждения.", "Free подходит для знакомства и повседневных задач. Лимиты каждого плана видны заранее, а платный тариф активируется только после подтверждённой оплаты.", 1)

    css = r'''
:root{--bg:#f7f9fc;--panel:#ffffff;--panel2:#f6f8fc;--muted:#667085;--text:#101828;--accent:#4f46e5;--accent2:#2563eb;--line:#e5e9f2;--good:#15805d;--warn:#a16207}
html{background:#f7f9fc}body{background:radial-gradient(circle at 78% 6%,rgba(99,102,241,.16),transparent 23%),radial-gradient(circle at 18% 28%,rgba(14,165,233,.10),transparent 24%),linear-gradient(180deg,#fff 0,#f8faff 46%,#f5f8fc 100%);color:var(--text)}nav{border-color:rgba(229,233,242,.9);background:rgba(255,255,255,.66);backdrop-filter:blur(14px)}.brand{font-size:16px;letter-spacing:-.02em;color:#111827}.navlinks{color:#667085}.btn{background:#fff;color:#1f2937;border-color:#dfe4ec;box-shadow:0 1px 2px rgba(16,24,40,.03)}.btn:hover{border-color:#b8c1d1}.btn.primary{background:linear-gradient(135deg,#4f46e5,#2563eb 58%,#0891b2);color:#fff;box-shadow:0 12px 28px rgba(79,70,229,.22)}.btn.ghost{background:rgba(255,255,255,.72)}.hero{padding:92px 0 74px;grid-template-columns:minmax(0,1.04fr) minmax(360px,.96fr);gap:54px}.eyebrow{background:#fff;border-color:#dfe4f2;color:#4f46e5;box-shadow:0 8px 28px rgba(35,55,100,.05)}h1{color:#101828;font-size:clamp(46px,6.8vw,82px);line-height:.96}.hero h1 span{background:linear-gradient(90deg,#4f46e5,#2563eb 48%,#0891b2);-webkit-background-clip:text;background-clip:text;color:transparent}.lead{color:#5d687a}.hero-note{color:#7a8495}.future-window{padding:0;overflow:hidden;border-color:#e4e8f1;background:rgba(255,255,255,.88);box-shadow:0 30px 90px rgba(45,55,100,.16),0 1px 0 #fff inset;transform:perspective(1000px) rotateY(-3deg) rotateX(1deg)}.mock-top{height:48px;display:flex;align-items:center;gap:7px;padding:0 16px;border-bottom:1px solid #edf0f5;background:rgba(249,250,252,.9)}.mock-top span{width:9px;height:9px;border-radius:50%;background:#d6dbe5}.mock-top b{margin-left:auto;font-size:12px;color:#667085}.mock-body{padding:22px}.mock-prompt{margin-left:12%;padding:13px 15px;background:#f2f4f8;border-radius:16px;color:#344054;font-size:14px}.mock-tools{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0}.mock-tools span{padding:5px 9px;border:1px solid #e2e7ef;border-radius:999px;background:#fff;color:#667085;font-size:11px}.mock-answer{padding:18px 4px 4px;color:#344054}.mock-answer strong{color:#101828}.mock-answer p{font-size:14px}.mock-lines i{display:block;height:8px;margin:8px 0;border-radius:8px;background:linear-gradient(90deg,#e5e9f2,#f3f5f8)}.mock-lines i:nth-child(2){width:88%}.mock-lines i:nth-child(3){width:64%}.trust-row{padding-bottom:72px}.trust{border-color:#dfe5ee}.trust b{color:#1d2939}.trust span{color:#667085}.section{padding:76px 0}.section-head{max-width:790px}.kicker{display:block;margin-bottom:11px;color:#4f46e5;font-size:12px;font-weight:800;letter-spacing:.12em}.card,.price,.faq details,.auth-card{background:rgba(255,255,255,.88);border-color:#e2e7ef;box-shadow:0 8px 30px rgba(34,52,90,.055)}.card{border-radius:20px;padding:23px}.feature-cards{gap:14px}.feature-no{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:9px;background:#eef2ff;color:#4f46e5;font-size:11px;font-weight:800;margin-bottom:28px}.card h3{color:#1d2939}.card p,.section-head p{color:#667085}.flow-section{padding-top:30px}.flow-shell{padding:46px;border:1px solid #dde5f0;border-radius:30px;background:linear-gradient(135deg,rgba(255,255,255,.95),rgba(242,247,255,.9));box-shadow:0 30px 80px rgba(47,65,110,.08)}.flow-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.flow-grid>div{padding:20px;border-radius:18px;background:rgba(255,255,255,.8);border:1px solid #e5eaf2}.flow-grid span{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#101828;color:#fff;font-weight:800;font-size:12px}.flow-grid h3{margin-top:30px;color:#1d2939}.flow-grid p{margin-bottom:0;color:#667085}.price.featured{border-color:#9aa7ff;box-shadow:0 18px 55px rgba(79,70,229,.12)}.price-name{color:#667085}.price-value{color:#101828}.price ul{color:#596579}.cta{border-color:#dce4f2;background:linear-gradient(135deg,#f7f8ff,#eef7ff)}footer{border-color:#e3e8ef;color:#7a8495}input{background:#fff;color:#202124;border-color:#dce2eb}.oauth-btn{border-color:#dce2eb}.verify-box{background:#fffbeb;border-color:#f4dfa2;color:#725b24}
@media(max-width:980px){.hero{grid-template-columns:1fr}.future-window{transform:none;max-width:720px}.flow-grid{grid-template-columns:1fr}}
@media(max-width:560px){.hero{padding-top:54px}.future-window{border-radius:20px}.flow-shell{padding:24px}.hero-actions{align-items:stretch}.hero-actions .btn{width:100%}}
'''
    return _inject_style(document, css)


def install_product_ui_refresh() -> None:
    current = HTMLResponse.__init__
    if getattr(current, "_olya_product_ui_refresh", False):
        return

    def refreshed(self, content, *args, **kwargs):
        if isinstance(content, str):
            if 'id="view-chat"' in content and 'id="project-chat"' in content:
                content = _refresh_workspace(content)
            elif 'id="features"' in content and 'id="pricing"' in content and 'js-register-cta' in content:
                content = _refresh_landing(content)
        return current(self, content, *args, **kwargs)

    refreshed._olya_product_ui_refresh = True  # type: ignore[attr-defined]
    HTMLResponse.__init__ = refreshed
