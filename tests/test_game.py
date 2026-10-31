# 量詞空戰測試：不改遊戲本檔。測試端把 three.js 換成本機檔、題庫 CSV 從本機送出（直接開 html 讀不到 data/），
# 並在載入時補一個「檢查用出入口」（只讀狀態、呼叫遊戲自己的函式），其餘用真的鍵盤、滑鼠、按鈕操作。
import sys, re, json, os, math
from playwright.sync_api import sync_playwright
import common
from common import *

R = Runner()
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, '..', 'index.html'), encoding='utf-8').read()
MARK = '/* ---------- 開始 ---------- */'
assert SRC.count(MARK) == 1
HOOK = ("window.__dron={get state(){return state},get round(){return round},get score(){return score},get lives(){return lives},"
        "get correct(){return correct},get target(){return target},get curAns(){return curAns},get targets(){return targets},get qs(){return qs},"
        "get qT(){return qT},setQT(v){qT=v},get ammo(){return ammo},get reloadAt(){return reloadAt},get hintUsed(){return hintUsed},"
        "get gates(){return gates},get playT(){return playT},get gHead(){return gHead},get gFar(){return gFar},D,cfg,tilt,"
        "get MBANK(){return MBANK},get MPOOL(){return MPOOL},get SETS(){return SETS},get GROUPS(){return GROUPS},get MWLIST(){return MWLIST},"
        "pickArena,buildArena,lineBlocked,groundH,wallDist,scopeLabel,get ring(){return hintRing},TOTAL,ARENA_N,HIT,TRAVEL,SPEEDS,GTURN};\n" + MARK)
PATCHED = SRC.replace(MARK, HOOK, 1)
# Chrome 不讓 file:// 頁面用 fetch 讀 file:// 檔，所以測試版把題庫網址換成假網址再由測試端送出（遊戲其餘程式不動）
assert PATCHED.count("await fetch(path, {cache:'no-cache'})") == 1
PATCHED = PATCHED.replace("await fetch(path, {cache:'no-cache'})", "await fetch('https://game.test/' + path, {cache:'no-cache'})")
# 測試瀏覽器用軟體繪圖，3D 畫面一秒只畫得出約 5 張，遊戲時間會變得很慢；測試版改成每 12 個畫面才真的畫一次（遊戲計算照常每個畫面跑）
assert PATCHED.count("update(dt, now); renderer.render(scene, camera);") == 1
PATCHED = PATCHED.replace("update(dt, now); renderer.render(scene, camera);", "update(dt, now); window.__rc=(window.__rc||0)+1; if (window.__rc % 12 === 0) renderer.render(scene, camera);")
THREE = open('/tmp/pp/node_modules/three/build/three.module.js', encoding='utf-8').read()
DATA = {n: open(os.path.join(HERE, '..', 'data', n), encoding='utf-8').read() for n in ('classifiers.csv', 'measure-words.csv', 'words.csv')}
FLAGS = {'data': dict(DATA), 'status': {}}
CORS = {'access-control-allow-origin': '*'}

def route(r):
    u = r.request.url
    if u.startswith('file:'):
        base = u.split('?')[0]
        if base.endswith('/index.html'):
            return r.fulfill(status=200, content_type='text/html; charset=utf-8', body=PATCHED)
        return r.continue_()
    if u.startswith('https://game.test/'):
        base = u.split('?')[0]
        m = re.search(r'/data/([^/]+\.csv)$', base)
        if m:
            name = m.group(1)
            if FLAGS['status'].get(name): return r.fulfill(status=FLAGS['status'][name], body='', headers=CORS)
            return r.fulfill(status=200, content_type='text/csv; charset=utf-8', body=FLAGS['data'][name], headers=CORS)
        return r.abort()
    if 'three@0.160.0' in u:
        return r.fulfill(status=200, content_type='application/javascript', body=THREE, headers=CORS)
    if 'gstatic.com/firebasejs' in u:
        return r.fulfill(status=200, content_type='application/javascript', body=APP if 'firebase-app' in u else FS if 'firestore' in u else AUTH if 'auth' in u else '')
    r.abort()
common.route = route

def page(p, extra=None, width=900, height=480, wait=2500):
    return open_page(p, settings=None, width=width, height=height, wait=wait, extra_init=extra)

def J(pg, expr, arg=None): return pg.evaluate("a=>{const J=window.__dron;%s}" % expr, arg)
def wait_state(pg, st, ms=8000):
    pg.wait_for_function("s=>window.__dron.state===s", arg=st, timeout=ms)

def aim_at(pg, idx_expr):
    """把無人機放到目標前 30 公尺、同高度、正對著它（試 8 個方向，找沒被岩壁擋住的）"""
    return J(pg, """const t=J.targets.find(x=>x.m===a&&!x.dead)||J.targets.find(x=>x.m===a);if(!t)return null;const P=t.obj.position;
      for(let k=0;k<16;k++){const ang=k*Math.PI/8;const dx=P.x+Math.cos(ang)*30,dz=P.z+Math.sin(ang)*30;
        if(J.lineBlocked(dx,dz,P.x,P.z))continue;
        const D=J.D;D.x=dx;D.z=dz;D.y=P.y;D.vf=D.vs=D.vy=0;D.yaw=Math.atan2(P.x-dx,P.z-dz)+Math.PI;
        // 前方向量 (-sin yaw,-cos yaw) 要指向目標
        return {ok:true,ang:k}}
      return {ok:false}""", idx_expr)

def fire(pg, hit=True):
    """開火。打中時等到畫面出現回饋（子彈飛行用遊戲內時間，測試瀏覽器畫面慢時要等久一點）"""
    pg.keyboard.press('Space')
    if hit: pg.wait_for_function("document.getElementById('fb').classList.contains('show')", timeout=15000)
    pg.wait_for_timeout(150)

def hold(pg, key, gsec):
    """按住某個鍵，直到遊戲內時間過了 gsec 秒"""
    t0 = J(pg, "return J.playT"); pg.keyboard.down(key)
    pg.wait_for_function("window.__dron.playT>%f" % (t0 + gsec), timeout=30000); pg.keyboard.up(key)

def start(pg, mode='shoot', **opts):
    pg.evaluate("document.getElementById('menu').hidden=false")
    pg.click(f'#modes .chip[data-v="{mode}"]')
    pg.click('#go'); pg.wait_for_timeout(500)

with sync_playwright() as p:
    # ───── 1) 題庫載入與選單 ─────
    b, pg, errs = page(p)
    R.check(pg.is_enabled('#go') and pg.inner_text('#bankNote') == '', '題庫載入成功：開始鈕可按、沒有警告：%s' % pg.inner_text('#bankNote'))
    nb = J(pg, "return [J.MBANK.length,J.GROUPS.length,Object.keys(J.SETS).length,J.MPOOL.length,J.MWLIST.length]")
    R.check(nb[0] == 100 and nb[1] == 5 and nb[2] == 5, '量詞題 100、分類 5、主題 5：%s' % nb)
    R.check(pg.evaluate("document.querySelectorAll('#groups .chip').length") == 6 and pg.evaluate("document.querySelectorAll('#sets .chip').length") == 5, '選單：分類鈕 6（含全部）、主題鈕 5')
    R.check(pg.evaluate("document.querySelectorAll('#mwSel option').length") == nb[4] + 1, '單一量詞下拉：%d 項' % (nb[4] + 1))
    R.check(pg.evaluate("document.getElementById('shootOpts').style.display") == 'block' and pg.evaluate("document.getElementById('gateOpts').style.display") == 'none', '預設玩法＝射擊：顯示量詞範圍')
    pg.click('#modes .chip[data-v="gate"]'); pg.wait_for_timeout(100)
    R.check(pg.evaluate("document.getElementById('gateOpts').style.display") == 'block' and pg.evaluate("document.getElementById('shootOpts').style.display") == 'none', '切到飛過門：顯示主題與速度')
    for sp in ('slow', 'mid', 'fast'):
        pg.click(f'#speeds .chip[data-v="{sp}"]'); R.check(J(pg, "return J.cfg.speed") == sp and 'on' in pg.get_attribute(f'#speeds .chip[data-v="{sp}"]', 'class'), f'速度鈕：{sp}')
    for k in J(pg, "return Object.keys(J.SETS)"):
        pg.click(f'#sets .chip[data-v="{k}"]'); R.check(J(pg, "return J.cfg.set") == k, f'主題鈕：{k}')
    pg.click('#modes .chip[data-v="shoot"]')
    for g in J(pg, "return J.GROUPS"):
        pg.click(f'#groups .chip[data-v="{g}"]'); R.check(J(pg, "return J.cfg.group") == g and J(pg, "return J.cfg.mw") == 'all', f'分類鈕：{g}')
    pg.select_option('#mwSel', '隻'); R.check(J(pg, "return [J.cfg.mw,J.cfg.group]") == ['隻', 'all'], '選單一量詞「隻」：分類回到全部')
    pg.click('#groups .chip[data-v="all"]'); R.check(J(pg, "return J.cfg.mw") == 'all', '點「全部」：量詞回到全部')
    pg.click('#ctrls .chip[data-v="tilt"]'); R.check(pg.evaluate("document.getElementById('tiltOpts').style.display") == 'flex', '選傾斜：出現反轉選項')
    pg.click('#ctrls .chip[data-v="stick"]'); R.check(pg.evaluate("document.getElementById('tiltOpts').style.display") == 'none', '選搖桿：隱藏')
    R.check(not errs, '沒有網頁錯誤 %s' % errs)
    b.close()

    # ───── 2) 題庫壞掉／缺檔 ─────
    FLAGS['status'] = {'words.csv': 404}
    b, pg, errs = page(p)
    R.check(pg.is_disabled('#go') and '題庫載入失敗' in pg.inner_text('#bankNote'), '缺一個 CSV：開始鈕停用、顯示載入失敗：' + pg.inner_text('#bankNote')[:40])
    b.close(); FLAGS['status'] = {}
    bad = dict(DATA)
    bad['classifiers.csv'] = DATA['classifiers.csv'].rstrip('\n') + "\n動物,Animales,,x,y,隻,\n動物,Animales,怪,gua,raro,\n動物,Animales,新,xin,nuevo,咪,\n"
    bad['words.csv'] = DATA['words.csv'] + "植物,Plantas,花,huā,flor\n植物,Plantas,草,cǎo,hierba\n"
    FLAGS['data'] = bad
    b, pg, errs = page(p)
    note = pg.inner_text('#bankNote')
    R.check(pg.is_enabled('#go') and '缺資料' in note, '有缺欄位的列：略過並提醒：' + note[:60])
    R.check('不在 measure-words.csv' in note, '用到沒登記的量詞「咪」：提醒')
    R.check('少於 3 個詞' in note, '主題少於 3 個詞：略過並提醒')
    R.check('植物' not in J(pg, "return Object.keys(J.SETS)") and len(J(pg, "return Object.keys(J.SETS)")) == 5, '不足 3 詞的主題沒進選單')
    R.check(J(pg, "return J.MBANK.length") == 101 and J(pg, "return J.MBANK.some(i=>i.n==='怪')") is False, '缺量詞的題目略過、完整的新題收進來')
    b.close()
    FLAGS['data'] = dict(DATA)

    # ───── 3) 出題規則：每題只有一個標準答案、答案一定在場上（跑 40 次） ─────
    b, pg, errs = page(p)
    res = J(pg, """const out={bad:[],n:0,maxField:0,minField:99};
      for(const grp of ['all',...J.GROUPS]){ J.cfg.group=grp; J.cfg.mw='all';
        for(let i=0;i<8;i++){ const {qs,field}=J.pickArena(); out.n++;
          out.maxField=Math.max(out.maxField,field.length);out.minField=Math.min(out.minField,field.length);
          if(qs.length!==10)out.bad.push(['題數',grp,qs.length]);
          if(new Set(field).size!==field.length)out.bad.push(['場上量詞重複',grp]);
          for(const q of qs){
            if(!field.includes(q.ans))out.bad.push(['答案不在場上',q.it.n,q.ans]);
            if(!q.it.a.includes(q.ans))out.bad.push(['答案不在正確清單',q.it.n]);
            const others=field.filter(m=>m!==q.ans&&(q.it.a.includes(m)||q.it.x.includes(m)));
            if(others.length)out.bad.push(['場上有也算對/不可當干擾項的量詞',q.it.n,others]);
          }}}
      J.cfg.group='all'; return out""")
    R.check(not res['bad'], f'{res["n"]} 次出題：10 題、答案都在場上、場上沒有「也說得通」的量詞：{res["bad"][:3]}')
    R.check(res['maxField'] <= 30 and res['minField'] >= 10, '場上量詞數 %d～%d（上限 30）' % (res['minField'], res['maxField']))
    sing = J(pg, """J.cfg.mw='隻';J.cfg.group='all';const {qs}=J.pickArena();const r=qs.every(q=>q.it.a.includes('隻')&&q.ans==='隻');J.cfg.mw='all';return r""")
    R.check(sing, '只練「隻」：10 題答案全是「隻」')
    sub = J(pg, """J.cfg.group='交通';const {qs}=J.pickArena();const r=qs.every(q=>q.it.g==='交通');J.cfg.group='all';return r""")
    R.check(sub, '只練「交通」分類：題目全來自該分類')
    b.close()

    # ───── 4) 射擊玩法：真的開火 ─────
    b, pg, errs = page(p)
    start(pg, 'shoot')
    R.check(J(pg, "return [J.state,J.round,J.score,J.lives]") == ['play', 1, 0, 3], '開局：第 1 題、0 分、3 條命')
    R.check(not pg.is_visible('#menu') and pg.inner_text('#pz').startswith('一＿'), '提示「%s」' % pg.inner_text('#pz'))
    R.check(pg.inner_text('#ps') == J(pg, "return J.target.es"), '西文提示：' + pg.inner_text('#ps'))
    R.check(J(pg, "return J.targets.filter(t=>!t.dead).length") >= 10, '場上有量詞靶 %d 個' % J(pg, "return J.targets.length"))
    R.check(J(pg, "return J.targets.some(t=>t.gnd)") and J(pg, "return J.targets.some(t=>!t.gnd)"), '一半立在地面、一半在空中')
    R.check(pg.inner_text('#ammo').count('●') == 6, '彈匣 6 發')
    # 答對
    a = aim_at(pg, J(pg, "return J.curAns")); R.check(a and a['ok'], '找到不被擋住的射擊位置')
    pg.wait_for_timeout(200); fire(pg)
    R.check(J(pg, "return [J.score,J.correct,J.lives]") == [100, 1, 3], '射中正確量詞：+100、答對 1、命不變：%s' % J(pg, "return [J.score,J.correct,J.lives]"))
    R.check('good' in (pg.get_attribute('#fb', 'class') or ''), '顯示答對回饋：' + pg.inner_text('#fb').replace('\n', ' ')[:30])
    R.check(pg.inner_text('#ammo').count('●') == 5, '彈匣少一發')
    pg.wait_for_timeout(2300)
    R.check(J(pg, "return J.round") == 2 and J(pg, "return J.state") == 'play', '答對後自動換第 2 題')
    # 射錯
    wrong = J(pg, "const t=J.targets.find(x=>!x.dead&&!J.target.a.includes(x.m));return t.m")
    a = aim_at(pg, wrong); pg.wait_for_timeout(200); fire(pg)
    R.check(J(pg, "return [J.score,J.lives]") == [100, 2], '射中錯的量詞：命 −1、不加分：%s' % J(pg, "return [J.score,J.lives]"))
    R.check('bad' in (pg.get_attribute('#fb', 'class') or '') and '正確是' in pg.inner_text('#fb'), '顯示射錯回饋與正確答案')
    R.check(J(pg, "return !!J.ring"), '射錯後正確量詞出現提示圈')
    R.check(J(pg, "return J.state") == 'fb', '回饋時間內不能再射（鎖定）')
    sc0 = J(pg, "return [J.score,J.lives]"); fire(pg, hit=False)
    R.check(J(pg, "return [J.score,J.lives]") == sc0, '回饋期間按射擊：沒有作用')
    pg.wait_for_timeout(3300)
    R.check(J(pg, "return J.round") == 3, '回饋結束換第 3 題')
    # 提示：20 秒
    J(pg, "J.setQT(19.95)"); pg.wait_for_function("window.__dron.hintUsed", timeout=10000); pg.wait_for_timeout(300)
    R.check(J(pg, "return J.hintUsed"), '20 秒沒找到：進入提示狀態（該題只得 50 分）')
    R.check(pg.evaluate("document.getElementById('hintA').style.display") == 'block' and J(pg, "return !!J.ring"), '出現方向箭頭與提示圈')
    R.check('約' in pg.inner_text('#hintL') and ' m' in pg.inner_text('#hintL'), '方向文字：' + pg.inner_text('#hintL'))
    aim_at(pg, J(pg, "return J.curAns")); pg.wait_for_timeout(200); fire(pg)
    R.check(J(pg, "return J.score") == 150, '有用提示答對只得 50 分：%s' % J(pg, "return J.score"))
    pg.wait_for_timeout(2300)
    # 沒打中任何東西：不判定
    J(pg, "const D=J.D;D.y=60;D.x=0;D.z=0;D.yaw=0")
    s0 = J(pg, "return [J.score,J.lives,J.state]"); fire(pg, hit=False); pg.wait_for_timeout(1500)
    R.check(J(pg, "return [J.score,J.lives,J.state]") == s0, '打空：不扣分、不扣命、仍可作答')
    R.check(not errs, '沒有網頁錯誤 %s' % errs)
    b.close()

    # 彈匣與換彈
    b, pg, errs = page(p)
    start(pg, 'shoot')
    J(pg, "const D=J.D;D.y=60;D.x=0;D.z=0;D.yaw=0")
    for i in range(6): pg.keyboard.press('Space'); pg.wait_for_timeout(60)
    R.check(J(pg, "return J.ammo") == 0 and pg.inner_text('#ammo') == 'RELOAD…', '打完 6 發：自動換彈')
    pg.keyboard.press('Space'); pg.wait_for_timeout(50)
    R.check(J(pg, "return J.ammo") == 0, '換彈中不能射')
    pg.wait_for_timeout(1700)
    R.check(J(pg, "return J.ammo") == 6 and '●●●●●●' == pg.inner_text('#ammo'), '約 1.4 秒後補滿')
    pg.keyboard.press('Space'); pg.wait_for_timeout(60)
    pg.keyboard.press('r'); pg.wait_for_timeout(100)
    R.check(pg.inner_text('#ammo') == 'RELOAD…', '按 R 手動換彈')
    pg.wait_for_timeout(1700)
    pg.keyboard.press('r'); pg.wait_for_timeout(100)
    R.check(pg.inner_text('#ammo') == '●●●●●●', '滿彈時按 R 沒有作用')
    # 滑鼠與「射擊」鈕也能射
    pg.mouse.click(450, 240); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.ammo") == 5, '電腦滑鼠點畫面射擊')
    pg.dispatch_event('#c', 'pointerdown', {'pointerType': 'touch'}); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.ammo") == 5, '手機觸控點畫面不會射擊')
    pg.dispatch_event('#c', 'pointerdown', {'pointerType': 'pen'}); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.ammo") == 5, '手寫筆點畫面不會射擊')
    pg.dispatch_event('#fire', 'pointerdown', {'pointerType': 'touch'}); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.ammo") == 4, '手機按「射擊」鈕射擊')
    b.close()

    # 全部答對 → 任務完成
    b, pg, errs = page(p)
    start(pg, 'shoot')
    for i in range(10):
        wait_state(pg, 'play', 6000)
        aim_at(pg, J(pg, "return J.curAns")); pg.wait_for_timeout(120); fire(pg)
        pg.wait_for_timeout(2200)
    wait_state(pg, 'end', 6000)
    R.check(pg.inner_text('#endT') == '任務完成！' and pg.inner_text('#endS') == '1000', '10 題全對：任務完成、1000 分')
    R.check('答對 10 / 10' in pg.inner_text('#endC') and '全部量詞' in pg.inner_text('#endC'), '結算：' + pg.inner_text('#endC'))
    pg.click('#again'); pg.wait_for_timeout(500)
    R.check(J(pg, "return [J.state,J.round,J.score,J.lives]") == ['play', 1, 0, 3] and not pg.is_visible('#end'), '再玩一次：歸零重來')
    pg.click('#menuBtn'); pg.wait_for_timeout(200)
    R.check(pg.is_visible('#menu') and J(pg, "return J.state") == 'menu', '遊戲中按選單：回選單')
    pg.select_option('#mwSel', '條'); pg.click('#go'); pg.wait_for_timeout(500)
    R.check(all(q['it']['a'].count('條') for q in J(pg, "return J.qs.map(q=>({it:{a:q.it.a}}))")), '選單改成只練「條」後開始：題目都是「條」')
    R.check(not errs, '沒有網頁錯誤 %s' % errs)
    b.close()

    # 三次射錯 → 任務失敗
    b, pg, errs = page(p)
    start(pg, 'shoot'); pg.select_option('#mwSel', 'all') if False else None
    for i in range(3):
        wait_state(pg, 'play', 6000)
        wrong = J(pg, "return J.targets.find(x=>!x.dead&&!J.target.a.includes(x.m)).m")
        aim_at(pg, wrong); pg.wait_for_timeout(120); fire(pg)
        pg.wait_for_timeout(3300)
    wait_state(pg, 'end', 6000)
    R.check(pg.inner_text('#endT') == '任務失敗' and pg.inner_text('#endS') == '0', '三次射錯：任務失敗')
    pg.click('#toMenu'); R.check(pg.is_visible('#menu') and not pg.is_visible('#end'), '結算畫面「回選單」')
    b.close()

    # ───── 5) 飛過門 ─────
    b, pg, errs = page(p)
    start(pg, 'gate')
    R.check(J(pg, "return [J.state,J.round,J.gates.length]") == ['play', 1, 3], '開局：3 個門')
    g = J(pg, "return J.gates.map(g=>({w:g.word.zh,air:g.air,x:g.obj.position.x,y:g.obj.position.y,z:g.obj.position.z}))")
    tz = J(pg, "return J.target.zh")
    R.check(sorted(set(x['w'] for x in g)) == sorted(x['w'] for x in g) and tz in [x['w'] for x in g], '三個門詞不同、其中一個是題目：' + tz)
    R.check(all(125 < math.hypot(x['x'], x['z'] - 6) < 175 for x in g), '門在前方 130～170 公尺')
    R.check(pg.inner_text('#pz') == f'飛過「{tz}」' and pg.inner_text('#pp') == '', '提示：' + pg.inner_text('#pz'))
    R.check(not pg.is_visible('#fire') and not pg.is_visible('#ammoP'), '飛過門：沒有射擊鈕與彈匣')
    # 自動前進速度
    for sp, v in (('slow', 10), ('mid', 14), ('fast', 20)):
        b2, pg2, e2 = page(p)
        pg2.click('#modes .chip[data-v="gate"]'); pg2.click(f'#speeds .chip[data-v="{sp}"]'); pg2.click('#go'); pg2.wait_for_timeout(300)
        pg2.wait_for_function("window.__dron.playT>1.5", timeout=30000)
        z0 = J(pg2, "return [J.D.x,J.D.z,J.playT]"); pg2.wait_for_function("window.__dron.playT>%f" % (z0[2] + 1.5), timeout=30000); z1 = J(pg2, "return [J.D.x,J.D.z,J.playT]")
        d = math.hypot(z1[0] - z0[0], z1[1] - z0[1]) / (z1[2] - z0[2])
        R.check(0.55 * v < d < 1.1 * v, f'速度「{sp}」：每秒約 {d:.1f} 公尺（設定 {v}）')
        b2.close()
    # 不能回頭：按 S 沒有後退；轉向限制 ±60°（先重開一局，避免前面等待時無人機已自己飛過頭）
    pg.click('#menuBtn'); pg.click('#go'); pg.wait_for_timeout(300)
    y0 = J(pg, "return J.D.yaw"); pg.keyboard.down('ArrowLeft'); t0 = J(pg, "return J.playT"); pg.wait_for_function("window.__dron.playT>%f" % (t0 + 1.6), timeout=30000); pg.keyboard.up('ArrowLeft')
    dy = J(pg, "return Math.abs(J.D.yaw-J.gHead)")
    R.check(dy <= 1.0471976 + 0.01 and dy > 0.9, '一直往左轉：最多轉到 60°（%.2f rad）' % dy)
    # 錯過
    J(pg, "const D=J.D;const f=[-Math.sin(J.gHead),-Math.cos(J.gHead)];D.x+=f[0]*(J.gFar+40);D.z+=f[1]*(J.gFar+40)"); pg.wait_for_timeout(300)
    R.check(J(pg, "return [J.state,J.score,J.lives]") == ['fb', 0, 3] and '錯過答題機會' in pg.inner_text('#fb'), '飛過最遠的門還沒選：錯過，不扣命不加分')
    try: pg.wait_for_function("window.__dron.round===2&&window.__dron.state==='play'", timeout=15000); ok2 = True
    except Exception: ok2 = False
    R.check(ok2, '自動換下一題：%s' % J(pg, "return [J.state,J.round,J.lives]"))
    # 飛錯
    def fly_through(word_ok):
        info = J(pg, """const g=J.gates.find(g=>(J.target.zh===g.word.zh)===a);const o=g.obj;
          const v=o.localToWorld(new (o.position.constructor)(0,0,6));const v2=o.localToWorld(new (o.position.constructor)(0,0,-6));return [v.x,v.y,v.z,v2.x,v2.y,v2.z]""", word_ok)
        J(pg, "const D=J.D;D.x=a[0];D.y=a[1];D.z=a[2];D.vf=D.vs=D.vy=0", info[:3]); pg.wait_for_timeout(120)
        J(pg, "const D=J.D;D.x=a[0];D.y=a[1];D.z=a[2]", info[3:]); pg.wait_for_timeout(250)
    fly_through(False)
    R.check(J(pg, "return [J.score,J.lives]") == [0, 2] and '飛錯了' in pg.inner_text('#fb'), '飛過錯的門：命 −1、不加分')
    pg.wait_for_timeout(2200)
    fly_through(True)
    R.check(J(pg, "return [J.score,J.correct]") == [100, 1] and '答對了' in pg.inner_text('#fb'), '飛過對的門：+100')
    R.check(not errs, '沒有網頁錯誤 %s' % errs)
    b.close()

    # 飛過門打完 10 題
    b, pg, errs = page(p)
    start(pg, 'gate')
    for i in range(10):
        wait_state(pg, 'play', 6000)
        info = J(pg, """const g=J.gates.find(g=>J.target.zh===g.word.zh);const o=g.obj;const V=o.position.constructor;
          const v=o.localToWorld(new V(0,0,6));const v2=o.localToWorld(new V(0,0,-6));return [v.x,v.y,v.z,v2.x,v2.y,v2.z]""")
        J(pg, "const D=J.D;D.x=a[0];D.y=a[1];D.z=a[2]", info[:3]); pg.wait_for_timeout(120)
        J(pg, "const D=J.D;D.x=a[0];D.y=a[1];D.z=a[2]", info[3:]); pg.wait_for_timeout(2100)
    wait_state(pg, 'end', 6000)
    R.check(pg.inner_text('#endT') == '任務完成！' and pg.inner_text('#endS') == '1000', '飛過門 10 題全對：1000 分')
    R.check(J(pg, "return Object.keys(J.SETS)")[0] in pg.inner_text('#endC'), '結算顯示主題：' + pg.inner_text('#endC'))
    b.close()

    # 三個主題的詞：題目一定來自所選主題
    for k in ('交通', '動物', '水果', '顏色', '食物'):
        b, pg, errs = page(p)
        pg.click('#modes .chip[data-v="gate"]'); pg.click(f'#sets .chip[data-v="{k}"]'); pg.click('#go'); pg.wait_for_timeout(400)
        ok = J(pg, "return J.gates.every(g=>J.SETS[J.cfg.set].w.includes(g.word))")
        R.check(ok and J(pg, "return J.cfg.set") == k, f'主題「{k}」：3 個門的詞都來自該主題')
        b.close()

    # ───── 6) 選項：拼音、聽力模式 ─────
    b, pg, errs = page(p)
    pg.check('#optPy'); start(pg, 'shoot')
    R.check(pg.inner_text('#pp') == J(pg, "return J.target.py") and pg.inner_text('#pp') != '', '顯示拼音：' + pg.inner_text('#pp'))
    b.close()
    b, pg, errs = page(p)
    pg.check('#optListen'); start(pg, 'shoot')
    R.check(pg.inner_text('#pz') == '一＿？' and pg.inner_text('#ps') != J(pg, "return J.target.es"), '聽力模式：不顯示題目文字')
    sp = pg.evaluate("window.__spoken.map(s=>s.text)")
    R.check(J(pg, "return J.target.n") in sp, '聽力模式：念出名詞「%s」' % J(pg, "return J.target.n"))
    sp0 = pg.evaluate("window.__spoken.length")
    aim_at(pg, J(pg, "return J.curAns")); pg.wait_for_timeout(150); fire(pg)
    sp2 = pg.evaluate("window.__spoken.map(s=>s.text)")
    R.check(any(s.startswith('一') and J(pg, "return J.target.n") in s for s in sp2[sp0:]), '射完後念出完整答案：%s' % sp2[sp0:])
    pg.click('#spk'); R.check(pg.evaluate("window.__spoken.at(-1).text") == J(pg, "return J.target.n"), '喇叭鈕：重念名詞')
    R.check(all(abs(s['rate'] - 0.85) < 1e-6 and s['lang'] == 'zh-TW' for s in pg.evaluate("window.__spoken.filter(s=>s.text)")), '念的語言 zh-TW、速度 0.85')
    b.close()
    b, pg, errs = page(p)
    pg.check('#optListen'); pg.click('#modes .chip[data-v="gate"]'); pg.click('#go'); pg.wait_for_timeout(400)
    R.check(pg.inner_text('#pz') == '聽，然後飛過正確的字' and J(pg, "return J.target.zh") in pg.evaluate("window.__spoken.map(s=>s.text)"), '飛過門聽力：不顯示題目、念出詞語')
    b.close()
    # 找不到中文語音／瀏覽器不能朗讀
    b, pg, errs = page(p, extra="Object.defineProperty(window,'speechSynthesis',{get(){return {getVoices:()=>[],speak(){},cancel(){}}},configurable:true});")
    R.check('找不到中文語音' in pg.inner_text('#voiceNote'), '沒有中文聲音：提示 ' + pg.inner_text('#voiceNote'))
    b.close()
    b, pg, errs = page(p, extra="Object.defineProperty(window,'speechSynthesis',{get(){return {getVoices:()=>[{lang:'zh-CN',name:'x'}],speak(){},cancel(){}}},configurable:true});")
    R.check('口音可能不是台灣' in pg.inner_text('#voiceNote'), '只有大陸中文聲音：提示口音')
    b.close()

    # ───── 7) 傾斜操作 ─────
    b, pg, errs = page(p, extra="window.DeviceOrientationEvent=function(){};")
    pg.click('#ctrls .chip[data-v="tilt"]'); start(pg, 'shoot')
    R.check(J(pg, "return J.cfg.ctrl") == 'tilt' and not pg.is_visible('#rs') and pg.is_visible('#calib'), '傾斜：右搖桿隱藏、出現校準鈕')
    pg.wait_for_timeout(2800)
    R.check(J(pg, "return J.cfg.ctrl") == 'stick' and pg.is_visible('#rs') and '沒偵測到傾斜感應' in pg.inner_text('#toast'), '2.5 秒收不到感應資料：自動改回搖桿並提示')
    b.close()
    b, pg, errs = page(p, extra="window.DeviceOrientationEvent=function(){};")
    pg.click('#ctrls .chip[data-v="tilt"]'); start(pg, 'shoot')
    ev = "window.dispatchEvent(Object.assign(new Event('deviceorientation'),{beta:%s,gamma:%s}))"
    pg.evaluate(ev % (0, 0)); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.tilt.got"), '收到傾斜資料')
    pg.evaluate(ev % (-30, 0)); pg.wait_for_timeout(100)    # 螢幕直放：p=-beta
    f1 = J(pg, "return J.tilt.f"); R.check(f1 > 0.2, '往前傾：前進量為正（%.2f）' % f1)
    pg.evaluate(ev % (0, 25)); pg.wait_for_timeout(100)
    r1 = J(pg, "return J.tilt.r"); R.check(r1 > 0.2, '往右傾：橫移量為正（%.2f）' % r1)
    pg.evaluate(ev % (0, 1.5)); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.tilt.r") == 0, '小角度在死區內：不動')
    pg.evaluate(ev % (20, 0)); pg.click('#calib'); pg.evaluate(ev % (20, 0)); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.tilt.f") == 0, '按「校準」：目前姿勢當作中立')
    pg.evaluate(ev % (-30, 0)); pg.wait_for_timeout(1200)
    R.check(J(pg, "return J.D.vf") != 0, '傾斜真的讓無人機動起來')
    b.close()
    b, pg, errs = page(p, extra="window.DeviceOrientationEvent=function(){};")
    pg.click('#ctrls .chip[data-v="tilt"]'); pg.check('#optInvF'); pg.check('#optInvR'); start(pg, 'shoot')
    pg.evaluate(ev % (0, 0)); pg.evaluate(ev % (-30, 25)); pg.wait_for_timeout(100)
    R.check(J(pg, "return J.tilt.f") < -0.2 and J(pg, "return J.tilt.r") < -0.2, '前後／左右反轉：方向相反')
    b.close()
    b, pg, errs = page(p, extra="delete window.DeviceOrientationEvent;")
    pg.click('#ctrls .chip[data-v="tilt"]'); start(pg, 'shoot')
    R.check(J(pg, "return J.cfg.ctrl") == 'stick' and '改用搖桿' in pg.inner_text('#toast'), '裝置沒有傾斜功能：直接改搖桿')
    b.close()

    # ───── 8) 鍵盤操作 ─────
    b, pg, errs = page(p)
    start(pg, 'shoot')
    J(pg, "J.D.x=0;J.D.z=0;J.D.y=30;J.D.yaw=0")
    hold(pg, 'w', 0.9)
    R.check(J(pg, "return J.D.z") < -2, 'W 往前')
    z1 = J(pg, "return J.D.z"); hold(pg, 's', 1.5)
    R.check(J(pg, "return J.D.z") > z1, 'S 往後')
    x0 = J(pg, "return J.D.x"); hold(pg, 'd', 0.9)
    R.check(J(pg, "return J.D.x") > x0 + 1, 'D 往右')
    x1 = J(pg, "return J.D.x"); hold(pg, 'a', 1.2)
    R.check(J(pg, "return J.D.x") < x1, 'A 往左')
    y0 = J(pg, "return J.D.y"); hold(pg, 'ArrowUp', 0.7)
    R.check(J(pg, "return J.D.y") > y0 + 1, '↑ 升高')
    y1 = J(pg, "return J.D.y"); hold(pg, 'ArrowDown', 0.9)
    R.check(J(pg, "return J.D.y") < y1 - 1, '↓ 下降')
    w0 = J(pg, "return J.D.yaw"); hold(pg, 'ArrowRight', 0.5)
    R.check(J(pg, "return J.D.yaw") < w0 - 0.2, '→ 向右轉')
    w1 = J(pg, "return J.D.yaw"); hold(pg, 'q', 0.5)
    R.check(J(pg, "return J.D.yaw") > w1 + 0.2, 'Q 向左轉（E 反向）')
    J(pg, "J.D.y=200"); pg.wait_for_timeout(200); R.check(J(pg, "return J.D.y") <= 70.01, '高度上限 70 公尺')
    J(pg, "J.D.y=-50"); pg.wait_for_timeout(200); R.check(J(pg, "return J.D.y") >= 1.4, '不能穿到地面以下')
    R.check(not errs, '沒有網頁錯誤 %s' % errs)
    b.close()

print('FAILS', R.fails); sys.exit(1 if R.fails else 0)
