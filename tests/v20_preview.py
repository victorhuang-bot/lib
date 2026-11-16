"""Disposable loopback browser fixture. Synthetic data only; never uses .env."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
for key in list(os.environ):
    if key.startswith(('PG','RENDER','GMAIL','GOOGLE')) or key=='DATABASE_URL':os.environ.pop(key,None)
with tempfile.TemporaryDirectory(prefix='movingmatch-v20-browser-') as folder:
    os.environ.update(DATA_DIR=folder,APP_ENV='test',MOVINGMATCH_V20_ENABLED='1')
    from app import main
    main.init_db()
    c=main.db()
    c.execute("INSERT INTO routes(id,code,name,active) VALUES(1,'1','合成路線一',1),(2,'2','合成路線二',1)")
    c.execute("INSERT INTO drivers(id,name,active) VALUES(1,'合成司機',1)")
    for bid in (1,2,3):
        c.execute("INSERT INTO branches(id,code,name,route_id,stop_order,active,delivery_weekdays) VALUES(?,?,?,?,?,1,'1,2,3,4,5,6,7')",(bid,'SYNTHETIC'+str(bid),'合成分館'+str(bid),1 if bid<3 else 2,bid))
    c.execute("UPDATE branches SET active=1,delivery_weekdays='1,2,3,4,5,6,7' WHERE id IN (1,2,3)")
    c.execute("INSERT INTO users(username,role,is_active) VALUES('合成測試管理者','ADMIN',1)")
    user=c.execute("SELECT id FROM users WHERE username='合成測試管理者'").fetchone()['id']
    c.execute('INSERT INTO sessions VALUES(?,?,?)',(main.thash('synthetic-v20-admin'),user,main.future_iso(hours=1)))
    for bid in (1,2,3):
        c.execute('INSERT INTO branch_sessions(token_hash,branch_id,expires_at) VALUES(?,?,?)',(main.thash('synthetic-v20-branch-'+str(bid)),bid,main.future_iso(hours=1)))
        c.execute('INSERT INTO branch_signers(branch_id,name,active,created_at,updated_at) VALUES(?,?,1,?,?)',(bid,'合成測試人員',main.now(),main.now()))
        main.empty_boxes.stocktake(c,bid,dict(unused=10+bid,loaded=2,damaged=1,actor='合成盤點人員',version=0))
    request=main.empty_boxes.request(c,1,dict(quantity=8,actor='合成申請人'))
    main.empty_boxes.transfer(c,'合成管理者',dict(request_id=request,planned_date=main.today(),sources=[dict(branch_id=2,quantity=3,planned_date=main.today()),dict(branch_id=3,quantity=2,planned_date=main.today())]))
    c.commit();c.close()
    import uvicorn
    uvicorn.run(main.app,host='127.0.0.1',port=58120,log_level='warning')
