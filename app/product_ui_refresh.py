from __future__ import annotations

import re

from starlette.responses import HTMLResponse


_MARKER = "OLYA_PRODUCT_SURFACE_V2"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def _inject_style(document: str, css: str) -> str:
    nonce = _nonce(document)
    attr = f' nonce="{nonce}"' if nonce else ""
    return document.replace("</head>", f'<style{attr}>/*{_MARKER}*/\n{css}\n</style></head>', 1)


def _inject_script(document: str, script: str) -> str:
    nonce = _nonce(document)
    attr = f' nonce="{nonce}"' if nonce else ""
    return document.replace("</body>", f'<script{attr}>{script}</script></body>', 1)


def _looks_admin(document: str) -> bool:
    lowered = document.casefold()
    return any(
        marker in lowered
        for marker in (
            "x1 admin",
            "olya ai · owner",
            "административное меню",
            "/v1/admin/",
            "x1 closed-beta",
            "x1 beta operations",
            "x1 public launch",
        )
    )


def _looks_user_surface(document: str) -> bool:
    lowered = document.casefold()
    if _looks_admin(document):
        return False
    return any(
        marker in lowered
        for marker in (
            'id="features"',
            'class="form-shell"',
            "sessionstorage.getitem('x1_access_token')",
            'id="view-chat"',
            "начало работы — x1 ai",
            "поддержка — x1 ai",
            "api console",
            "редактор фото",
        )
    )


def _universal_light_css() -> str:
    return r'''
:root{
  color-scheme:light!important;
  --bg:#ffffff!important;--panel:#ffffff!important;--panel2:#f7f7f8!important;--card:#ffffff!important;
  --line:#e5e5e7!important;--text:#202123!important;--muted:#6e6e73!important;--accent:#111827!important;
  --accent2:#111827!important;--ok:#15805d!important;--warn:#9a6700!important;--bad:#c24141!important;--code:#111827!important
}
html{background:#fff!important;color-scheme:light!important}
body{background:#fff!important;color:#202123!important;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif!important;-webkit-font-smoothing:antialiased}
a{color:inherit}button,input,select,textarea{font-family:inherit!important}
button,a,input,select,textarea{transition:border-color .16s ease,background-color .16s ease,box-shadow .16s ease,transform .16s ease,opacity .16s ease}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid #111827!important;outline-offset:2px}
.card,.auth-card,.price,.faq details{background:#fff!important;border-color:#e5e5e7!important;box-shadow:0 1px 2px rgba(0,0,0,.025)!important;color:#202123!important}
.muted,.lead,.meta,.state,.status,.connection,.contract,.placeholder,p{color:#6e6e73}
input,select,textarea,.field{background:#fff!important;color:#202123!important;border-color:#d9d9dc!important}
input::placeholder,textarea::placeholder{color:#9a9aa0!important}
input:focus,select:focus,textarea:focus{border-color:#8b8b91!important;box-shadow:0 0 0 3px rgba(17,24,39,.07)!important}
.primary,.btn.primary{background:#111827!important;color:#fff!important;border-color:#111827!important;box-shadow:none!important}
.primary:hover,.btn.primary:hover{background:#262d3a!important;transform:translateY(-1px)}
.secondary,.ghost,.skip,.btn:not(.primary){background:#fff!important;color:#242424!important;border-color:#dedee2!important}
.secondary:hover,.ghost:hover,.skip:hover,.btn:not(.primary):hover{background:#f7f7f8!important;border-color:#cfcfd4!important}
.top,header.top{background:rgba(255,255,255,.92)!important;color:#202123!important;border-color:#ededee!important;backdrop-filter:blur(16px)}
.brand{color:#202123!important;letter-spacing:-.025em!important}
.bad{color:#c24141!important}.ok{color:#15805d!important}.warn{color:#9a6700!important}

/* Authorization surfaces: intentionally minimal, centered and distraction-free. */
body:has(.form-shell){min-height:100vh;background:#fff!important}
body:has(.form-shell) nav{border:0!important;background:transparent!important;justify-content:center!important;min-height:78px!important}
body:has(.form-shell) nav .navlinks,body:has(.form-shell) nav .actions{display:none!important}
body:has(.form-shell) nav .brand{font-size:19px!important;font-weight:700!important}
body:has(.form-shell) .form-shell{width:min(420px,100%)!important;margin:46px auto 100px!important}
body:has(.form-shell) .auth-card{padding:24px 28px!important;border:0!important;box-shadow:none!important;border-radius:0!important}
body:has(.form-shell) .auth-card h2{font-size:30px!important;letter-spacing:-.04em!important;text-align:center!important;margin:0 0 9px!important;color:#202123!important}
body:has(.form-shell) .auth-card>p{text-align:center!important;margin:0 auto 28px!important;max-width:340px!important}
body:has(.form-shell) label{color:#343438!important;font-weight:600!important;font-size:14px!important}
body:has(.form-shell) input{min-height:52px!important;border-radius:12px!important;padding:0 14px!important;font-size:16px!important}
body:has(.form-shell) .full{min-height:50px!important;border-radius:12px!important;font-weight:700!important}
body:has(.form-shell) .fine{text-align:center!important;margin-top:22px!important;color:#77777d!important}
body:has(.form-shell) .oauth-sep{color:#96969b!important}
body:has(.form-shell) .oauth-sep:before,body:has(.form-shell) .oauth-sep:after{background:#e4e4e7!important}
body:has(.form-shell) .oauth-btn{background:#fff!important;color:#202123!important;border:1px solid #d9d9dc!important}
body:has(.form-shell) .verify-box{background:#fffaf0!important;color:#725b24!important;border-color:#ead8aa!important}

/* Other signed-in product screens inherit the same calm surface language. */
body:not(:has(.x1-admin-global)) .wrap,body:not(:has(.x1-admin-global)) main{color:#202123}
body:not(:has(.x1-admin-global)) .grid .card{border-radius:16px!important}
body:not(:has(.x1-admin-global)) .top a,body:not(:has(.x1-admin-global)) .back{background:#fff!important;color:#2c2c2f!important;border-color:#dedee2!important}
body:not(:has(.x1-admin-global)) .ticket,body:not(:has(.x1-admin-global)) .item{background:#fff!important;color:#202123!important;border-color:#e5e5e7!important}
body:not(:has(.x1-admin-global)) .ticket:hover,body:not(:has(.x1-admin-global)) .ticket.active{background:#f5f5f6!important;border-color:#cdCDD2!important}
body:not(:has(.x1-admin-global)) .msg{background:transparent;color:#202123;border-color:#e6e6e8}
body:not(:has(.x1-admin-global)) .msg.admin{background:#f7f7f8!important;border-color:#e4e4e7!important}
body:not(:has(.x1-admin-global)) .drop,body:not(:has(.x1-admin-global)) .result{background:#f7f7f8!important;border-color:#d8d8dc!important}
body:not(:has(.x1-admin-global)) .codebox,body:not(:has(.x1-admin-global)) .secret-code code{background:#111827!important;color:#f7f7f8!important;border-color:#283244!important}
body:not(:has(.x1-admin-global)) .scope{background:#f8f8f9!important;border-color:#e1e1e4!important}
body:not(:has(.x1-admin-global)) .secret{background:#fffbeb!important;border-color:#ead8a6!important;color:#6f571c!important}
@media(max-width:560px){body:has(.form-shell) .form-shell{margin-top:24px!important}body:has(.form-shell) .auth-card{padding:18px 12px!important}}
'''


def _refresh_workspace(document: str) -> str:
    if 'id="view-chat"' not in document or 'id="project-chat"' not in document:
        return document

    document = document.replace("<title>X1 — рабочее пространство</title>", "<title>OLYA AI</title>", 1)
    document = document.replace('<div class="brand">X1 AI</div>', '<div class="brand">OLYA AI</div>', 1)
    document = document.replace(
        "Локальная Qwen, файлы проекта, интернет-поиск и проверка ответа в одном рабочем пространстве.",
        "Чем я могу помочь?",
        1,
    )
    # Fast is intentionally absent from the consumer UI. The backend still
    # accepts legacy `fast` requests and upgrades them to Work.
    document = document.replace('<option value="fast">Fast</option>', "")
    document = document.replace('<option value="work">Work</option>', '<option value="work">Стандарт</option>')
    document = document.replace('<option value="deep">Deep</option>', '<option value="deep">Глубокий</option>')

    composer_marker = '<div class="composer-foot"><span class="health" id="counter"></span>'
    if composer_marker in document:
        document = document.replace(
            composer_marker,
            '<div class="composer-foot"><button class="attach" id="chat-attach" type="button" title="Прикрепить файл" aria-label="Прикрепить файл">+</button><span class="attachment-state" id="chat-attachment-state"></span><span class="health" id="counter"></span>',
            1,
        )
    if '<div class="state" id="state"></div>' in document:
        document = document.replace(
            '<div class="state" id="state"></div>',
            '<div class="state" id="state"></div><div class="composer-note">OLYA AI может ошибаться. Важные данные лучше перепроверять.</div>',
            1,
        )

    css = r'''
:root{--bg:#fff!important;--side:#f7f7f8!important;--panel:#fff!important;--panel2:#f7f7f8!important;--line:#e6e6e8!important;--text:#202123!important;--muted:#727277!important;--accent:#111827!important;--code:#111827!important}
html,body{background:#fff!important;color:#202123!important;color-scheme:light!important}
body{overflow:hidden}.shell{grid-template-columns:260px minmax(0,1fr)!important;background:#fff!important}.side{background:#f7f7f8!important;border-right:0!important;padding:10px 9px!important}.brand{order:0;padding:10px 10px 13px!important;font-size:17px!important;font-weight:700!important}.new{order:1;margin:0 0 8px!important;border:0!important;background:transparent!important;color:#242424!important;text-align:left!important;font-weight:600!important;padding:10px!important;border-radius:9px!important}.new:hover{background:#ececee!important}.nav{order:2;gap:2px!important}.navbtn{color:#303033!important;border-radius:9px!important;padding:9px 10px!important}.navbtn:hover,.navbtn.active{background:#ececee!important;color:#151515!important}.navbtn small{color:#85858b!important}.side-section{order:3;color:#8a8a90!important;text-transform:none!important;letter-spacing:0!important;font-size:12px!important;margin:18px 8px 6px!important}.history{order:4}.conv{color:#39393d!important;border-radius:8px!important}.conv:hover,.conv.active{background:#ececee!important}.account-mini{order:5;border-color:#e2e2e4!important;color:#77777c!important}.linkbtn{color:#36363a!important}
.main{background:#fff!important}.top{min-height:52px!important;border:0!important;background:rgba(255,255,255,.94)!important;backdrop-filter:blur(14px)!important;padding:7px 15px!important}.top .budget,.top>.health{display:none!important}.page-title{font-size:15px!important;font-weight:600!important;color:#343438!important}.chat-controls{display:none!important}.mobile-menu{background:#fff!important;color:#222!important;border-color:#dedee2!important}
.view{background:#fff!important}.content{width:min(980px,100%)!important;padding:34px 24px 70px!important}.content h1{font-size:29px!important;color:#202123!important}.lead,.muted{color:#747479!important}.card{background:#fff!important;border:1px solid #e7e7e9!important;border-radius:16px!important;box-shadow:0 1px 2px rgba(0,0,0,.02)!important}.item{background:#fff!important;border-color:#e7e7e9!important}.primary{background:#111827!important;color:#fff!important}.secondary{background:#fff!important;color:#252529!important;border-color:#dedee2!important}.danger{background:#fff5f5!important;color:#a92727!important}
.messages{padding:22px max(20px,calc((100% - 800px)/2)) 34px!important;scroll-behavior:smooth!important}.older{background:#fff!important;color:#45454a!important;border-color:#dedee2!important}.chat-empty{max-width:760px!important;margin:17vh auto 0!important;text-align:center!important}.chat-empty h1{font-size:32px!important;font-weight:600!important;letter-spacing:-.04em!important;color:#202123!important;margin-bottom:10px!important}.chat-empty p{font-size:16px!important;color:#8a8a90!important}.prompt-suggestions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;max-width:660px;margin:28px auto 0}.prompt-suggestion{border:1px solid #e1e1e4;background:#fff;color:#39393d;border-radius:14px;padding:13px 14px;text-align:left;font-size:13px;line-height:1.35;cursor:pointer}.prompt-suggestion:hover{background:#f7f7f8;border-color:#d3d3d7;transform:translateY(-1px)}
.msg{max-width:800px!important;margin:0 auto 28px!important;animation:olya-message-in .2s ease-out}.role{display:none!important}.user{display:flex!important;justify-content:flex-end!important}.user .bubble{max-width:min(78%,680px)!important;background:#f4f4f4!important;border:0!important;color:#252525!important;padding:10px 15px!important;border-radius:20px!important;white-space:pre-wrap}.assistant .bubble{color:#202123!important;font-size:16px!important;line-height:1.72!important}.assistant .bubble p{margin:.68em 0!important}.assistant .bubble h1,.assistant .bubble h2,.assistant .bubble h3{color:#202123!important}.bubble blockquote{border-color:#d6d6da!important;color:#57575c!important}.tablewrap{border-color:#e1e1e4!important}.bubble th{background:#f7f7f8!important}.codewrap{background:#111827!important;border-color:#202938!important}.codehead{color:#c8ced8!important;border-color:#2b3544!important}.copy{background:#202938!important;color:#fff!important;border-color:#344156!important}.meta{opacity:.72!important;margin-top:8px!important}.badge{border-color:#e1e1e4!important;background:#fff!important}.sources{border-color:#e4e4e7!important;background:#fff!important}.source{border-color:#ececee!important}.source a{color:#2457a6!important}
.composer-wrap{border:0!important;padding:10px 20px 14px!important;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 24%)!important}.progress{max-width:790px!important;margin-bottom:7px!important}.step{background:#fff!important;color:#8b8b91!important;border-color:#e0e0e3!important}.step.active{color:#4d4d52!important;border-color:#bdbdc2!important;animation:olya-pulse 1.2s ease-in-out infinite}.step.done{color:#15805d!important}.composer{max-width:790px!important;margin:auto!important;border:1px solid #d9d9dd!important;background:#fff!important;border-radius:26px!important;padding:9px 10px!important;box-shadow:0 8px 30px rgba(0,0,0,.075)!important}.composer textarea{color:#202123!important;min-height:48px!important;padding:8px 10px!important}.composer-foot{min-height:38px!important;gap:7px!important;flex-wrap:wrap!important}.composer-tools{display:flex;gap:6px;align-items:center;min-width:0}.composer-select,.composer-more summary{min-height:34px!important;border:1px solid transparent!important;background:#fff!important;color:#4b4b50!important;border-radius:999px!important;padding:6px 9px!important;font-size:12px!important;cursor:pointer}.composer-select:hover,.composer-more summary:hover{background:#f3f3f5!important}.composer-more{position:relative}.composer-more summary{list-style:none}.composer-more summary::-webkit-details-marker{display:none}.composer-more-panel{position:absolute;left:0;bottom:42px;width:250px;padding:10px;background:#fff;border:1px solid #dedee2;border-radius:14px;box-shadow:0 14px 40px rgba(0,0,0,.12);display:grid;gap:8px;z-index:20}.composer-more-panel label{display:grid;gap:4px;color:#6f6f74;font-size:11px}.composer-more-panel select{width:100%;min-height:36px;border:1px solid #dedee2;border-radius:9px;background:#fff;color:#27272a;padding:6px 8px}.attach{width:34px!important;height:34px!important;border-radius:999px!important;border:1px solid #dedee2!important;background:#fff!important;color:#303034!important;font-size:22px!important;line-height:1!important;padding:0!important;display:inline-grid!important;place-items:center!important}.attach:hover{background:#f3f3f5!important}.attachment-state{max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#737378;font-size:12px}.composer-foot>.health{margin-left:auto;color:#8a8a90!important}.send{margin-left:0!important;background:#111827!important;color:#fff!important;border:0!important;border-radius:999px!important;padding:8px 14px!important;font-weight:650!important;min-width:92px!important}.send:hover{background:#262d3a!important}.send.stop{background:#b63d3d!important}.state{max-width:790px!important;color:#77777c!important}.composer-note{max-width:790px;margin:5px auto 0;text-align:center;color:#9a9aa0;font-size:11px}
#nav-files,#view-files,#workspace-files,.card:has(#workspace-files-list){display:none!important}.account-plan.current{border-color:#aeb4bd!important}.account-progress{accent-color:#111827!important}
@keyframes olya-message-in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}@keyframes olya-pulse{0%,100%{opacity:.58}50%{opacity:1}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media(max-width:760px){.shell{grid-template-columns:1fr!important}.side{background:#f7f7f8!important;box-shadow:28px 0 70px rgba(0,0,0,.13)!important}.top{background:#fff!important;padding:7px 9px!important}.messages{padding:16px 12px 26px!important}.composer-wrap{padding:7px 8px 10px!important}.composer{border-radius:22px!important}.user .bubble{max-width:90%!important}.chat-empty{margin-top:12vh!important}.chat-empty h1{font-size:29px!important}.prompt-suggestions{grid-template-columns:1fr;margin:22px 12px 0}.content{padding:22px 13px 55px!important}.composer-tools{max-width:230px;overflow:auto}.composer-select{max-width:126px}.attachment-state{max-width:90px}.composer-note{display:none}}
'''
    document = _inject_style(document, css)

    script = r'''
(function(){
  const $=id=>document.getElementById(id);
  const attach=$('chat-attach'),input=$('file-input'),chatProject=$('project-chat'),filesProject=$('files-project');
  const state=$('state'),attachState=$('chat-attachment-state'),mode=$('mode'),verify=$('verify'),web=$('web');
  const controls=$('chat-controls'),foot=document.querySelector('.composer-foot'),counter=$('counter');

  if(mode){
    for(const option of [...mode.options]){
      if(option.value==='fast')option.remove();
      if(option.value==='auto')option.textContent='Авто';
      if(option.value==='work')option.textContent='Стандарт';
      if(option.value==='deep')option.textContent='Глубокий';
    }
    if(mode.value==='fast')mode.value='auto';
    mode.title='Мощность ответа';
  }

  if(foot && mode && chatProject && !document.querySelector('.composer-tools')){
    const tools=document.createElement('div');tools.className='composer-tools';
    chatProject.classList.add('composer-select');chatProject.title='Проект';
    mode.classList.add('composer-select');
    tools.append(chatProject,mode);
    if(web && verify){
      const more=document.createElement('details');more.className='composer-more';
      const summary=document.createElement('summary');summary.textContent='Инструменты';
      const panel=document.createElement('div');panel.className='composer-more-panel';
      const webLabel=document.createElement('label');webLabel.textContent='Интернет';webLabel.append(web);
      const verifyLabel=document.createElement('label');verifyLabel.textContent='Проверка ответа';verifyLabel.append(verify);
      panel.append(webLabel,verifyLabel);more.append(summary,panel);tools.append(more);
      document.addEventListener('click',e=>{if(more.open&&!more.contains(e.target))more.open=false});
    }
    foot.insertBefore(tools,counter||foot.firstChild);
    if(controls)controls.style.display='none';
  }

  const empty=$('empty');
  if(empty && !empty.querySelector('.prompt-suggestions')){
    const ideas=[
      'Помоги разобраться с задачей и предложи лучший следующий шаг',
      'Проанализируй документ и выдели главное',
      'Сравни варианты и дай конкретную рекомендацию',
      'Помоги написать или улучшить текст'
    ];
    const root=document.createElement('div');root.className='prompt-suggestions';
    for(const text of ideas){const b=document.createElement('button');b.type='button';b.className='prompt-suggestion';b.textContent=text;b.onclick=()=>{const p=$('prompt');p.value=text;p.dispatchEvent(new Event('input'));p.focus()};root.append(b)}
    empty.append(root);
  }

  if(attach&&input&&chatProject&&filesProject&&attach.dataset.bound!=='1'){
    attach.dataset.bound='1';
    attach.addEventListener('click',function(){
      if(!chatProject.value){
        state.textContent='Для файла выберите проект рядом с полем ввода. Так документ сохранится в контексте проекта.';
        return;
      }
      filesProject.value=chatProject.value;input.click();
    });
    input.addEventListener('change',async function(){
      if(!input.files||!input.files.length)return;
      if(!chatProject.value){state.textContent='Выберите проект перед загрузкой файла.';input.value='';return;}
      filesProject.value=chatProject.value;const name=input.files[0].name;attachState.textContent='Загрузка: '+name;
      try{await uploadProjectFile();const fileState=$('file-state');attachState.textContent=name;state.textContent=(fileState&&fileState.textContent)||'Файл добавлен. Можно сразу задать вопрос по нему.'}
      catch(e){attachState.textContent='';state.textContent=e&&e.message?e.message:'Не удалось загрузить файл.'}
    });
  }
})();
'''
    return _inject_script(document, script)


def _refresh_landing(document: str) -> str:
    if 'id="features"' not in document or 'id="pricing"' not in document or 'js-register-cta' not in document:
        return document

    document = document.replace('<a class="brand" href="/">X1 AI</a>', '<a class="brand" href="/">OLYA AI</a>', 1)
    hero = '''<section class="wrap hero"><div class="hero-copy"><span class="eyebrow">OLYA AI · единая среда для работы с ИИ</span><h1>От вопроса до готового результата <span>в одном диалоге.</span></h1><p class="lead">Общайтесь с ИИ, прикладывайте файлы, ищите свежую информацию, работайте с проектами, кодом и изображениями. Интерфейс остаётся простым, даже когда внутри выполняется сложная работа.</p><div class="actions hero-actions"><a class="btn primary large js-register-cta" href="/register">Начать бесплатно</a><a class="btn large ghost" href="/#features">Посмотреть возможности</a></div><div class="hero-note">30 запросов в день бесплатно · без привязки карты</div></div><div class="hero-card future-window"><div class="mock-top"><b>OLYA AI</b><span>Авто</span></div><div class="mock-body"><div class="mock-prompt">Проанализируй этот документ, проверь актуальные данные и предложи лучший план.</div><div class="mock-answer"><strong>Собрала решение и проверила ключевые вводные</strong><p>Ниже — вывод, риски и конкретная последовательность действий.</p><div class="mock-lines"><i></i><i></i><i></i></div></div><div class="mock-composer"><span>＋</span><em>Спросите что-нибудь</em><b>Авто</b><i>↑</i></div></div></div></section>'''
    document = re.sub(r'<section class="wrap hero">.*?</section>', hero, document, count=1, flags=re.S)

    trust = '''<section class="wrap trust-row"><div class="trust"><b>30 запросов в день бесплатно</b><span>Достаточно, чтобы проверить продукт на реальных задачах.</span></div><div class="trust"><b>Файлы прямо в чате</b><span>Документ становится частью рабочего контекста.</span></div><div class="trust"><b>Автовыбор мощности</b><span>Простые задачи идут быстрее, сложные получают больше вычислений.</span></div><div class="trust"><b>Проекты и память</b><span>Продолжайте работу без повторного объяснения контекста.</span></div></section>'''
    document = re.sub(r'<section class="wrap trust-row">.*?</section>', trust, document, count=1, flags=re.S)

    features = '''<section class="section" id="features"><div class="wrap"><div class="section-head"><span class="kicker">ВОЗМОЖНОСТИ</span><h2>Один интерфейс вместо набора разрозненных AI-сервисов</h2><p>Вы формулируете задачу обычным языком. OLYA AI подключает нужный контекст и инструменты, не заставляя разбираться во внутренней архитектуре.</p></div><div class="cards feature-cards"><div class="card"><span class="feature-no">01</span><h3>Файлы внутри диалога</h3><p>Прикладывайте документы и продолжайте работу с ними прямо в чате.</p></div><div class="card"><span class="feature-no">02</span><h3>Свежий интернет-поиск</h3><p>Для актуальных задач система подключает внешние источники и проверку фактов.</p></div><div class="card"><span class="feature-no">03</span><h3>Автоматическая мощность</h3><p>По умолчанию качество не опускается ниже полноценного рабочего режима, а сложные задачи уходят в глубокую обработку.</p></div><div class="card"><span class="feature-no">04</span><h3>Агенты и сложные задачи</h3><p>Исследование, выполнение и проверка могут собираться в единую цепочку действий.</p></div><div class="card"><span class="feature-no">05</span><h3>Код и проекты</h3><p>Разработка и рабочий контекст живут рядом с обычными диалогами.</p></div><div class="card"><span class="feature-no">06</span><h3>Изображения</h3><p>Визуальные инструменты развиваются внутри того же аккаунта и той же среды.</p></div></div></div></section>'''
    document = re.sub(r'<section class="section" id="features">.*?</section>', features, document, count=1, flags=re.S)
    document = document.replace("Тарифы без скрытых списаний", "Начните бесплатно и переходите выше только когда это нужно", 1)
    document = document.replace("Фактические лимиты показываются в аккаунте. Оплата активирует тариф только после серверного подтверждения.", "Лимиты видны заранее. Free подходит для знакомства и повседневных задач, а платный тариф включается только после подтверждённой оплаты.", 1)
    document = document.replace("X1 AI · local-first AI platform", "OLYA AI · рабочая AI-среда", 1)

    css = r'''
:root{--bg:#fff!important;--panel:#fff!important;--panel2:#f7f7f8!important;--muted:#6e6e73!important;--text:#202123!important;--accent:#111827!important;--accent2:#111827!important;--line:#e7e7e9!important}
body{background:radial-gradient(circle at 79% 7%,rgba(99,102,241,.09),transparent 24%),radial-gradient(circle at 17% 25%,rgba(14,165,233,.055),transparent 26%),#fff!important;color:#202123!important}nav{background:rgba(255,255,255,.82)!important;backdrop-filter:blur(15px)!important;border-color:#ededee!important}.brand{font-size:17px!important;font-weight:750!important;color:#202123!important}.navlinks{color:#5f5f64!important}.btn{background:#fff!important;color:#242424!important;border-color:#dedee2!important;border-radius:12px!important}.btn.primary{background:#111827!important;color:#fff!important;border-color:#111827!important}.hero{padding:94px 0 78px!important;grid-template-columns:minmax(0,1.05fr) minmax(360px,.95fr)!important;gap:58px!important}.eyebrow{background:#fff!important;border-color:#e1e1e4!important;color:#5a5a61!important;box-shadow:0 8px 26px rgba(0,0,0,.035)!important}.hero h1{color:#202123!important;font-size:clamp(48px,6.7vw,80px)!important;line-height:.98!important;letter-spacing:-.058em!important}.hero h1 span{background:linear-gradient(90deg,#4f46e5,#2563eb 54%,#0891b2);-webkit-background-clip:text;background-clip:text;color:transparent}.lead{color:#63636a!important}.hero-note{color:#8a8a90!important}.future-window{padding:0!important;overflow:hidden!important;border:1px solid #e5e5e8!important;background:rgba(255,255,255,.94)!important;box-shadow:0 32px 95px rgba(32,40,70,.14)!important;transform:perspective(1100px) rotateY(-3deg) rotateX(1deg)!important}.mock-top{height:50px;display:flex;align-items:center;gap:8px;padding:0 16px;border-bottom:1px solid #ededf0}.mock-top b{color:#303034}.mock-top span{margin-left:auto;border:1px solid #e1e1e4;border-radius:999px;padding:4px 8px;color:#77777d;font-size:11px}.mock-body{padding:22px}.mock-prompt{margin-left:10%;padding:12px 15px;background:#f4f4f4;border-radius:18px;color:#343438;font-size:14px}.mock-answer{padding:22px 4px 16px;color:#343438}.mock-answer strong{color:#202123}.mock-answer p{font-size:14px}.mock-lines i{display:block;height:8px;margin:8px 0;border-radius:8px;background:#ececef}.mock-lines i:nth-child(2){width:87%}.mock-lines i:nth-child(3){width:63%}.mock-composer{height:52px;margin-top:14px;border:1px solid #dedee2;border-radius:22px;display:flex;align-items:center;gap:9px;padding:0 10px;box-shadow:0 5px 18px rgba(0,0,0,.06);color:#77777d}.mock-composer>span{font-size:20px}.mock-composer em{font-style:normal;flex:1;font-size:12px}.mock-composer b{font-size:10px;font-weight:600}.mock-composer>i{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#111827;color:#fff;font-style:normal}.trust{border-color:#e4e4e7!important}.trust b{color:#28282c!important}.trust span{color:#707076!important}.section{padding:78px 0!important}.section-head{max-width:790px!important}.kicker{display:block;margin-bottom:11px;color:#55555c;font-size:11px;font-weight:800;letter-spacing:.13em}.card,.price,.faq details{background:rgba(255,255,255,.92)!important;border-color:#e4e4e7!important;border-radius:20px!important;box-shadow:0 8px 30px rgba(0,0,0,.035)!important}.feature-cards{gap:14px!important}.feature-no{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:9px;background:#f2f2f4;color:#57575c;font-size:11px;font-weight:800;margin-bottom:28px}.card h3{color:#202123!important}.card p,.section-head p{color:#6e6e73!important}.price.featured{border-color:#b9b9c1!important;box-shadow:0 18px 55px rgba(0,0,0,.065)!important}.cta{border-color:#e2e2e5!important;background:linear-gradient(135deg,#fafafa,#f5f7ff)!important}footer{border-color:#ececee!important;color:#85858b!important}
@media(max-width:980px){.hero{grid-template-columns:1fr!important}.future-window{transform:none!important;max-width:720px!important}}@media(max-width:560px){.hero{padding-top:56px!important}.future-window{border-radius:20px!important}.hero-actions{align-items:stretch!important}.hero-actions .btn{width:100%!important}}
'''
    return _inject_style(document, css)


def _refresh_all_user_surfaces(document: str) -> str:
    if _MARKER in document:
        return document
    if not _looks_user_surface(document):
        return document

    # Product naming cleanup on user-facing surfaces only.
    document = document.replace("X1 AI", "OLYA AI")
    document = document.replace("← X1", "← OLYA AI")
    document = document.replace("В X1", "В OLYA AI")
    document = document.replace("X1 — API Console", "OLYA AI — API Console")

    if 'id="view-chat"' in document and 'id="project-chat"' in document:
        document = _refresh_workspace(document)
    elif 'id="features"' in document and 'id="pricing"' in document and 'js-register-cta' in document:
        document = _refresh_landing(document)

    # Remove Fast from any remaining user-facing test/model selectors, including
    # API Console. Legacy programmatic API calls remain accepted server-side.
    document = document.replace('<option value="fast">Fast</option>', "")
    document = _inject_style(document, _universal_light_css())
    return document


def install_product_ui_refresh() -> None:
    current = HTMLResponse.__init__
    if getattr(current, "_olya_product_ui_refresh_v2", False):
        return

    def refreshed(self, content, *args, **kwargs):
        if isinstance(content, str):
            content = _refresh_all_user_surfaces(content)
        return current(self, content, *args, **kwargs)

    refreshed._olya_product_ui_refresh_v2 = True  # type: ignore[attr-defined]
    HTMLResponse.__init__ = refreshed
