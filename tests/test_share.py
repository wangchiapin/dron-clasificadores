# 量詞空戰：結算畫面「分享成績」＋「從分享進來」記號（ref）。用法：python3 tests/test_share.py
# 測：按鈕位置（再玩一次之後、回選單之前）／樣式／0 分不顯示／一句話／連結／系統分享（成功、玩家關掉、出錯）／
#     複製備案（成功、失敗）／連點只送一次／再玩一次仍可用；?ref= 進來 → visits、plays 紀錄帶 ref，沒有就不帶，不合格的值忽略，暫存壞掉不出錯
# 做法同 test_game.py：不改遊戲本檔，測試端換掉 three.js、題庫網址，並補一個只供測試的出入口（endGame、設分數）。
import sys, re, os
from playwright.sync_api import sync_playwright
import common
from common import *

R = Runner()
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, '..', 'index.html'), encoding='utf-8').read()
MARK = '/* ---------- 開始 ---------- */'
assert SRC.count(MARK) == 1
PATCHED = SRC.replace(MARK, "window.__dron={endGame,setScore:v=>{score=v},get score(){return score},get state(){return state}};\n" + MARK, 1)
assert PATCHED.count("await fetch(path, {cache:'no-cache'})") == 1
PATCHED = PATCHED.replace("await fetch(path, {cache:'no-cache'})", "await fetch('https://game.test/' + path, {cache:'no-cache'})")
THREE = open('/tmp/pp/node_modules/three/build/three.module.js', encoding='utf-8').read()
DATA = {n: open(os.path.join(HERE, '..', 'data', n), encoding='utf-8').read() for n in ('classifiers.csv', 'measure-words.csv', 'words.csv')}
CORS = {'access-control-allow-origin': '*'}
URL_SHARE = 'https://wangchiapin.github.io/dron-clasificadores/?ref=share'

def route(r):
    u = r.request.url
    if u.startswith('file:'):
        if u.split('?')[0].endswith('/index.html'):
            return r.fulfill(status=200, content_type='text/html; charset=utf-8', body=PATCHED)
        return r.continue_()
    if u.startswith('https://game.test/'):
        m = re.search(r'/data/([^/]+\.csv)$', u.split('?')[0])
        if m: return r.fulfill(status=200, content_type='text/csv; charset=utf-8', body=DATA[m.group(1)], headers=CORS)
        return r.abort()
    if 'three@0.160.0' in u:
        return r.fulfill(status=200, content_type='application/javascript', body=THREE, headers=CORS)
    if 'gstatic.com/firebasejs' in u:
        return r.fulfill(status=200, content_type='application/javascript', body=APP if 'firebase-app' in u else FS if 'firestore' in u else AUTH if 'auth' in u else '')
    r.abort()

STUB = """(m) => {
  window.__shared=[];window.__copied=[];
  const def=(k,v)=>Object.defineProperty(navigator,k,{value:v,configurable:true});
  def('share',(m==='none'||m==='copyfail')?undefined:async d=>{window.__shared.push(d);
    if(m==='abort'){const e=new Error('c');e.name='AbortError';throw e;}
    if(m==='error'){const e=new Error('b');e.name='NotAllowedError';throw e;}});
  def('clipboard',m==='copyfail'?{writeText:async()=>{throw new Error('no');}}:{writeText:async s=>{window.__copied.push(s);}});
  if(m==='copyfail')document.execCommand=()=>false;
}"""

def open_game(p, query='', extra=None):
    b = p.chromium.launch(args=['--no-sandbox'])
    ctx = b.new_context(viewport={'width': 900, 'height': 480})
    ctx.add_init_script(FAKE_SPEECH)
    if extra: ctx.add_init_script(extra)
    pg = ctx.new_page(); errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)[:150]))
    ctx.route('**/*', route)
    pg.goto(HTML + query); pg.wait_for_timeout(2500)
    return b, pg, errs

def to_end(pg, score=120, stub='ok'):
    pg.evaluate("(" + STUB + ")(" + json.dumps(stub) + ")")
    pg.click('#go'); pg.wait_for_timeout(500)
    pg.evaluate("s=>{window.__dron.setScore(s);window.__dron.endGame(true)}", score); pg.wait_for_timeout(200)

def label(pg): return ' '.join(pg.inner_text('#shareBtn').split())
def ops(pg): return pg.evaluate("window.__ops||[]")

with sync_playwright() as p:
    print('[S1] 按鈕位置、樣式、按下去')
    b, pg, errs = open_game(p); to_end(pg)
    order = pg.evaluate("[...document.querySelectorAll('#end .btns > button')].filter(e=>!e.hidden).map(e=>e.id)")
    R.check(order == ['again', 'shareBtn', 'toMenu'], '分享鈕在「再玩一次」之後、「回選單」之前：%s' % order)
    R.check(pg.evaluate("(()=>{const e=document.getElementById('shareBtn');const c=getComputedStyle(e);return !e.classList.contains('on')&&c.backgroundColor==='rgba(0, 0, 0, 0)'&&parseFloat(c.borderTopWidth)>=1})()"), '空心外框樣式（透明底、有框、不是實心 on）')
    R.check(label(pg) == '分享成績 Compartir resultado', '按鈕文字：' + label(pg))
    pg.evaluate("(" + STUB + ")('ok')"); pg.click('#shareBtn'); pg.wait_for_timeout(250)
    sh = pg.evaluate("window.__shared")
    R.check(len(sh) == 1 and sh[0]['url'] == URL_SHARE, '系統分享：連結是公開頁加 ?ref=share：%s' % sh)
    R.check(sh and sh[0]['title'] == '量詞空戰 · ¡Dispara al clasificador!' and sh[0]['text'] == '¡Saqué 120 puntos en 量詞空戰 (¡Dispara al clasificador!)! ¿Puedes superarme?', '系統分享：標題與一句話：%s' % sh)
    R.check(pg.evaluate("window.__copied") == [] and label(pg) == '分享成績 Compartir resultado', '成功時不複製、按鈕文字不變')
    pg.evaluate("(" + STUB + ")('abort')"); pg.click('#shareBtn'); pg.wait_for_timeout(250)
    R.check(pg.evaluate("window.__copied") == [] and label(pg) == '分享成績 Compartir resultado', '玩家關掉選單：什麼都不做')
    pg.evaluate("(" + STUB + ")('error')"); pg.click('#shareBtn'); pg.wait_for_timeout(250)
    cp = pg.evaluate("window.__copied")
    R.check(len(cp) == 1 and cp[0].endswith('\n' + URL_SHARE) and '120 puntos' in cp[0], '分享出錯：改複製「一句話＋連結」：%s' % cp)
    R.check(label(pg) == '已複製，可以貼給朋友 Copiado, ¡pégalo a un amigo!', '顯示已複製：' + label(pg))
    pg.wait_for_timeout(2400)
    R.check(label(pg) == '分享成績 Compartir resultado', '2 秒後復原：' + label(pg))
    pg.evaluate("(" + STUB + ")('none')"); pg.click('#shareBtn'); pg.wait_for_timeout(250)
    R.check(len(pg.evaluate("window.__copied")) == 1, '不支援分享選單：改複製'); pg.wait_for_timeout(2400)
    pg.evaluate("(" + STUB + ")('copyfail')"); pg.click('#shareBtn'); pg.wait_for_timeout(250)
    R.check(label(pg) == '複製失敗 No se pudo copiar', '複製也失敗：顯示失敗：' + label(pg)); pg.wait_for_timeout(2400)
    pg.evaluate("(" + STUB + ")('ok')")
    pg.evaluate("()=>{const b=document.getElementById('shareBtn');b.click();b.click()}"); pg.wait_for_timeout(250)
    R.check(len(pg.evaluate("window.__shared")) == 1, '連點兩下只送一次')
    pg.click('#again'); pg.wait_for_timeout(500)
    R.check(pg.evaluate("window.__dron.state") == 'play' and not pg.is_visible('#end'), '按「再玩一次」仍然正常開新局')
    R.check(not errs, '沒有頁面錯誤：%s' % errs); b.close()

    print('[S2] 0 分不顯示')
    b, pg, errs = open_game(p); to_end(pg, score=0)
    R.check(pg.inner_text('#endS') == '0' and not pg.is_visible('#shareBtn'), '0 分：看不到分享鈕'); b.close()

    print('[R] 從分享進來的記號（ref）寫進 visits／plays')
    def played(query='', extra=None):
        b, pg, errs = open_game(p, query, extra)
        pg.click('#go'); pg.wait_for_timeout(3000)
        o = ops(pg)
        v = [x['d'] for x in o if x['p'] == 'visits/' + x['p'].split('/')[-1] and x['k'] == 'set' and x['p'].startswith('visits/')]
        pl = [x['d'] for x in o if x['p'] == 'plays' and x['k'] == 'add']
        return b, pg, errs, v, pl
    b, pg, errs, v, pl = played('?ref=share')
    R.check(len(v) == 1 and v[0].get('ref') == 'share' and v[0].get('game') == 'dron', '?ref=share：訪客紀錄帶 ref：%s' % v)
    R.check(len(pl) == 1 and pl[0].get('ref') == 'share', '?ref=share：遊玩紀錄帶 ref：%s' % pl)
    R.check(not errs, '沒有頁面錯誤：%s' % errs); b.close()
    b, pg, errs, v, pl = played('')
    R.check(len(v) == 1 and 'ref' not in v[0] and len(pl) == 1 and 'ref' not in pl[0], '沒有 ref：紀錄不帶 ref 欄位：%s %s' % (v, pl)); b.close()
    for q, want in [('?ref=SHARE', 'share'), ('?ref=Ig_story-1', 'ig_story-1'), ('?ref=' + 'a' * 17, None), ('?ref=', None), ('?ref=a%20b', None), ('?ref=%3Cscript%3E', None), ('?ref=a.b', None)]:
        b, pg, errs, v, pl = played(q)
        R.check(len(v) == 1 and (('ref' not in v[0]) if want is None else v[0].get('ref') == want), 'ref 參數 %s → %s：%s' % (q, want, v)); b.close()
    b, pg, errs, v, pl = played('', "try{sessionStorage.setItem('hg.ref','share')}catch(e){}")
    R.check(len(v) == 1 and v[0].get('ref') == 'share' and len(pl) == 1 and pl[0].get('ref') == 'share', '網址沒有 ref、分頁暫存有 → 也帶：%s' % v); b.close()
    b, pg, errs, v, pl = played('?ref=share', "Object.defineProperty(window,'sessionStorage',{get(){throw new Error('blocked')}})")
    R.check(len(v) == 1 and v[0].get('ref') == 'share' and not errs, '暫存壞掉但網址有 ref：仍帶 ref、不出錯：%s %s' % (v, errs)); b.close()

print('FAILS', R.fails); sys.exit(1 if R.fails else 0)
