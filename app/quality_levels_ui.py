from __future__ import annotations

import re

_MARKER = "OLYA_QUALITY_LEVELS_V1"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_quality_levels(document: str) -> str:
    if _MARKER in document or 'id="mode"' not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_QUALITY_LEVELS_V1 */
#mode.composer-select{min-width:112px!important;max-width:126px!important;font-weight:520!important}
#mode[data-level="fast"]{letter-spacing:-.01em}
#mode[data-level="deep"]{font-weight:600!important}
'''
    js = r'''
(function(){
  const mode=document.getElementById('mode');
  if(!mode)return;
  const STORAGE='olya_quality_level_v1';
  const levels=[
    {value:'fast',label:'Простой',title:'Быстрее: для обычных вопросов и быстрых ответов'},
    {value:'work',label:'Средний',title:'Баланс скорости и глубины для большинства задач'},
    {value:'deep',label:'Высокий',title:'Максимум внутреннего анализа для сложных задач'}
  ];
  let selected='work';
  try{
    const saved=localStorage.getItem(STORAGE);
    if(levels.some(x=>x.value===saved))selected=saved;
    else if(levels.some(x=>x.value===mode.value))selected=mode.value;
  }catch(_e){if(levels.some(x=>x.value===mode.value))selected=mode.value}
  mode.replaceChildren();
  for(const level of levels){
    const option=document.createElement('option');
    option.value=level.value;option.textContent=level.label;option.title=level.title;
    mode.append(option);
  }
  mode.value=selected;
  mode.dataset.level=selected;
  mode.title=levels.find(x=>x.value===selected)?.title||'';
  mode.setAttribute('aria-label','Уровень качества ответа');
  mode.addEventListener('change',()=>{
    const level=levels.find(x=>x.value===mode.value)||levels[1];
    mode.dataset.level=level.value;mode.title=level.title;
    try{localStorage.setItem(STORAGE,level.value)}catch(_e){}
  });
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
