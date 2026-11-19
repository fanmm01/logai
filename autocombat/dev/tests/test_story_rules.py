import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from story_rules import execute_rule,validate_rule,validate_input,cancel_execution
from story_game import parse_game
from story_models import StoryError


class StoryRulesTests(unittest.TestCase):
    def test_process_contract_and_failure(self):
        rule=dict(schema=1,syntax='json-v1',returns=['w'],executor=dict(kind='exec',argv=[sys.executable,'-c','import sys,json;d=json.load(sys.stdin);print(json.dumps({"result":"w","data":d}))']))
        response=execute_rule(rule,{'value':3},'execution')
        self.assertEqual(response['data']['execution_id'],'execution');self.assertEqual(response['data']['input']['value'],3)
        self.assertEqual(parse_game('rule OTHER\n{"x":1}',rules={'OTHER':rule})['payload'],{'x':1})
        for code in ['print("not json")','print(\'{"result":"bad"}\')','import sys;sys.exit(2)']:
            rule['executor']['argv']=[sys.executable,'-c',code]
            with self.assertRaises(StoryError):execute_rule(rule,{},'x')
    def test_http_contract(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                result=json.dumps(dict(result='w',data={'id':self.headers['Idempotency-Key'],'payload':payload})).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(result)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            rule=dict(syntax='json-v1',returns=['w'],executor=dict(kind='http',url='http://127.0.0.1:'+str(server.server_port)))
            self.assertEqual(execute_rule(rule,{'a':1},'stable-id')['data']['id'],'stable-id')
        finally:server.shutdown();server.server_close();thread.join()
    def test_validation(self):
        for rule in [[],{},{'syntax':'bad'},{'syntax':'json-v1','executor':[]},{'syntax':'json-v1','executor':{'kind':'builtin','target':'OTHER'}},{'syntax':'autocombat-v1','executor':{'kind':'http','url':'http://localhost'}},{'syntax':'json-v1','executor':{'kind':'http','url':'bad'}},{'syntax':'json-v1','executor':{'kind':'exec','argv':'bad'}},{'syntax':'json-v1','executor':{'kind':'exec','argv':['test']},'returns':[1]}]:
            with self.assertRaises(StoryError):validate_rule(rule)

    def test_input_schema(self):
        schema={'type':'object','required':['levels'],'properties':{'levels':{'type':'array','items':{'type':'integer','enum':[1,2]}}}}
        validate_input({'levels':[1,2]},schema)
        for payload in [{},{'levels':'x'},{'levels':[True]},{'levels':[3]}]:
            with self.subTest(payload=payload),self.assertRaises(StoryError):validate_input(payload,schema)
        cancel_execution('not-running')

    def test_process_timeout_and_cancel(self):
        import time
        rule=dict(syntax='json-v1',returns=['w'],executor=dict(kind='exec',timeout=1,argv=[sys.executable,'-c','import time;time.sleep(10)']))
        with self.assertRaises(StoryError):execute_rule(rule,{},'timeout')
        errors=[]
        def run():
            try:execute_rule(rule,{},'cancel')
            except StoryError as e:errors.append(str(e))
        worker=threading.Thread(target=run);worker.start();time.sleep(.15);cancel_execution('cancel');worker.join(3);self.assertFalse(worker.is_alive());self.assertTrue(errors)
