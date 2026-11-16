"""Independent schema contract captured from baseline d107436 plus reviewed V20 tables.

No connection creation: the caller supplies its newly initialized local test DB.
"""
import json
import re
from pathlib import Path

CONTRACT=json.loads(Path(__file__).with_name('v20_schema_contract.json').read_text())
BASELINE_TABLES=set(CONTRACT['baseline_tables'])
V20_TABLES=set(CONTRACT['v20_tables'])
FOREIGN_KEYS={
 'empty_box_balances':{'branch_id':('branches','id')},
 'empty_box_stocktakes':{'branch_id':('branches','id')},
 'empty_box_requests':{'branch_id':('branches','id')},
 'empty_box_transfers':{'request_id':('empty_box_requests','id'),'branch_id':('branches','id'),'route_id':('routes','id')},
 'empty_box_transfer_sources':{'transfer_id':('empty_box_transfers','id'),'branch_id':('branches','id'),'route_id':('routes','id')},
 'empty_box_handoffs':{'transfer_id':('empty_box_transfers','id'),'source_id':('empty_box_transfer_sources','id'),'branch_id':('branches','id'),'delivery_id':('deliveries','id')},
 'empty_box_movements':{'event_key':('empty_box_handoffs','event_key'),'branch_id':('branches','id')},
 'empty_box_reminders':{'branch_id':('branches','id')},
}
UNIQUE_KEYS={
 'empty_box_stocktakes':{('branch_id','version')},
 'empty_box_transfer_sources':{('transfer_id','branch_id')},
 'empty_box_handoffs':{('event_key',),('source_id',)},
}
CHECK_TERMS={
 'empty_box_balances':('unused >= 0','loaded >= 0','damaged >= 0','version > 0'),
 'empty_box_stocktakes':('unused >= 0','loaded >= 0','damaged >= 0','version > 0'),
 'empty_box_requests':('quantity > 0','fulfilled >= 0','fulfilled <= quantity',"'OPEN'","'COMPLETED'","'CANCELLED'"),
 'empty_box_transfers':('quantity > 0',"'PLANNED'","'IN_TRANSIT'","'COMPLETED'","'CANCELLED'"),
 'empty_box_transfer_sources':('quantity > 0',),
 'empty_box_handoffs':('quantity >= 0',"'PICKUP'","'RECEIVE'",'source_id IS NOT NULL','source_id IS NULL'),
}
INDEX_KEYS={
 'empty_box_stocktakes':{('branch_id','created_at')},
 'empty_box_requests':{('branch_id','status')},
 'empty_box_transfers':{('request_id','status')},
 'empty_box_transfer_sources':{('branch_id','planned_date')},
 'empty_box_handoffs':{('branch_id','confirmed_at')},
 'empty_box_movements':{('branch_id','created_at')},
 'empty_box_reminders':{('branch_id','created_at')},
}


def normalized(value):
    return re.sub(r'\s+',' ',re.sub(r'::[a-zA-Z_][a-zA-Z_0-9]*(?:\[\])?','',value or '')).strip()


def assert_postgres_schema(test,c):
    tables={r['tablename'] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall()}
    test.assertEqual(tables,BASELINE_TABLES|V20_TABLES,'Unexpected or missing table names')
    for table,columns in (CONTRACT['baseline_tables']|CONTRACT['v20_tables']).items():
        actual={r['column_name']:r for r in c.execute('''SELECT column_name,data_type,is_nullable,column_default
          FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?''',(table,)).fetchall()}
        test.assertEqual(set(actual),{col['name'] for col in columns},table+' columns')
        for col in columns:
            row=actual[col['name']]
            test.assertIn(row['data_type'],('integer','bigint') if col['type'] in ('INTEGER','BIGINT') else ('text',),table+'.'+col['name']+' type')
            test.assertEqual(row['is_nullable']=='NO',bool(col['notnull'] or col['pk']),table+'.'+col['name']+' nullability')
            if col['dflt_value'] is not None:
                test.assertEqual(normalized(row['column_default']),normalized(col['dflt_value']),table+'.'+col['name']+' default')
        constraints=c.execute('''SELECT k.contype,pg_get_constraintdef(k.oid) definition,
            ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY AS key(attnum,ord)
                  JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attnum=key.attnum ORDER BY key.ord) cols,
            ref.relname ref_table,
            ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY AS key(attnum,ord)
                  JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=key.attnum ORDER BY key.ord) ref_cols
          FROM pg_constraint k JOIN pg_class t ON t.oid=k.conrelid
          JOIN pg_namespace ns ON ns.oid=t.relnamespace LEFT JOIN pg_class ref ON ref.oid=k.confrelid
          WHERE ns.nspname=current_schema() AND t.relname=?''',(table,)).fetchall()
        primary=[tuple(r['cols']) for r in constraints if r['contype']=='p']
        expected_pk=tuple(col['name'] for col in sorted(columns,key=lambda col:col['pk']) if col['pk'])
        test.assertEqual(primary,[expected_pk],table+' primary key')
        for column,target in FOREIGN_KEYS.get(table,{}).items():
            test.assertTrue(any(r['contype']=='f' and r['cols']==[column] and r['ref_table']==target[0] and r['ref_cols']==[target[1]] for r in constraints),table+'.'+column+' FK')
        unique={tuple(r['cols']) for r in constraints if r['contype'] in ('p','u')}
        test.assertTrue(UNIQUE_KEYS.get(table,set())<=unique,table+' unique keys')
        checks=' '.join(normalized(r['definition']) for r in constraints if r['contype']=='c')
        for term in CHECK_TERMS.get(table,()):test.assertIn(term,checks,table+' CHECK')
        for key in INDEX_KEYS.get(table,set()):
            indexes=c.execute('SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename=?',(table,)).fetchall()
            test.assertTrue(any('('+', '.join(key)+')' in r['indexdef'] for r in indexes),table+' index '+str(key))
    return {'baseline_tables':sorted(BASELINE_TABLES),'v20_tables':sorted(V20_TABLES),'columns_keys_checks_indexes_verified':True}
