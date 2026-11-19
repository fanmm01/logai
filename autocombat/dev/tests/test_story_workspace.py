import json
import tempfile
import unittest
from pathlib import Path
from ui_store import Store
from story_workspace import Workspaces,compile_project
from story_models import StoryError


class StoryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.source=root/'source';self.source.mkdir();(self.source/'story.project.json').write_text('{"id":"test-project"}')
        (self.source/'A.flow').write_text('@START:\n->a\n->END');(self.source/'a.txt').write_text(':hello')
        self.workspaces=Workspaces(root/'runtime',Store(root/'store.db'));self.workspace=self.workspaces.open_workspace(self.source)
    def test_write_conflict_and_immutable_publish(self):
        before=self.workspaces.publish_version(self.workspace);file=self.workspaces.read_file(self.workspace,'a.txt')
        changed=self.workspaces.apply_file_transaction(self.workspace,[dict(path='a.txt',baseHash=file['hash'],content=':changed')])
        with self.assertRaises(StoryError):self.workspaces.apply_file_transaction(self.workspace,[dict(path='a.txt',baseHash=file['hash'],content=':stale')])
        after=self.workspaces.publish_version(self.workspace);self.assertNotEqual(before['version'],after['version'])
        self.assertEqual(self.workspaces.load_version(before['id'],before['version'])[0]['texts']['a.txt'],':hello')
        self.assertEqual(changed[0]['content'],':changed')

    def test_create_story_workspace(self):
        parent=Path(self.temp.name)/'stories'
        first=self.workspaces.create_workspace(parent,'覆辙')
        second=self.workspaces.create_workspace(parent,'覆辙')
        self.assertNotEqual(first['path'],second['path'])
        project=self.workspaces.read_project(first)
        self.assertEqual(project['config']['name'],'覆辙')
        self.assertIn('XU.flow',project['flows'])
        self.assertEqual((Path(first['path'])/'MAIN').read_text().strip(),'XU')
        for name in ('', '  ', None, 12, 'x'*101):
            with self.subTest(name=name),self.assertRaises(StoryError):self.workspaces.create_workspace(parent,name)
    def test_rename_updates_references_and_keeps_identity(self):
        (self.source/'a.txt').write_text(':a cat remains a cat',encoding='utf-8')
        file=self.workspaces.read_file(self.workspace,'a.txt')
        self.workspaces.apply_file_transaction(self.workspace,[dict(op='rename',path='a.txt',to='b.txt',baseHash=file['hash'])])
        self.assertIn('->b',self.workspaces.read_file(self.workspace,'A.flow')['content'])
        self.assertEqual(self.workspaces.read_file(self.workspace,'b.txt')['content'],':a cat remains a cat')
        config=json.loads(self.workspaces.read_file(self.workspace,'story.project.json')['content']);self.assertEqual(config['content_ids']['b.txt'],'a.txt')
        self.workspaces.read_project(self.workspace)
    def test_paths_preview_delete_and_invalid_project(self):
        with self.assertRaises(StoryError):self.workspaces.read_file(self.workspace,'../outside')
        preview=self.workspaces.publish_version(self.workspace,{'a.txt':':draft'},preview=True)
        self.assertTrue(preview['preview']);self.assertEqual(self.workspaces.read_file(self.workspace,'a.txt')['content'],':hello')
        file=self.workspaces.read_file(self.workspace,'a.txt');self.workspaces.apply_file_transaction(self.workspace,[dict(op='delete',path='a.txt',baseHash=file['hash'])])
        with self.assertRaises(StoryError):self.workspaces.read_project(self.workspace)
        with self.assertRaises(StoryError):compile_project({'A.flow':'@START:\n->END','C.flow':'@A.CONTINUE-missing:\n->END'})

    def test_recovery_rolls_back_interrupted_multi_file_save(self):
        txn=self.source/'.story-transactions/crash';txn.mkdir(parents=True)
        original=(self.source/'a.txt').read_bytes();(txn/'0').write_bytes(original)
        (txn/'journal.json').write_text(json.dumps({'status':'pending','files':[{'path':'a.txt','existed':True},{'path':'partial.txt','existed':False}]}))
        (self.source/'a.txt').write_text(':incomplete');(self.source/'partial.txt').write_text('partial')
        self.workspaces.open_workspace(self.source)
        self.assertEqual((self.source/'a.txt').read_bytes(),original);self.assertFalse((self.source/'partial.txt').exists())

    def test_rename_preserves_unqualified_precedence_and_rewrites_native_refs(self):
        (self.source/'a.chos').write_text('\\c{\n1“选项”\n}',encoding='utf-8')
        file=self.workspaces.read_file(self.workspace,'a.chos');self.workspaces.apply_file_transaction(self.workspace,[dict(op='rename',path='a.chos',to='b.chos',baseHash=file['hash'])])
        self.assertIn('->a',self.workspaces.read_file(self.workspace,'A.flow')['content'])
        (self.source/'native.txt').write_text('callScene:a.txt;\n：a cat',encoding='utf-8')
        file=self.workspaces.read_file(self.workspace,'a.txt');self.workspaces.apply_file_transaction(self.workspace,[dict(op='rename',path='a.txt',to='renamed.txt',baseHash=file['hash'])])
        self.assertEqual(self.workspaces.read_file(self.workspace,'native.txt')['content'],'callScene:renamed.txt;\n：a cat')

    def test_invalid_json_and_missing_files_are_diagnostics(self):
        for text in ['[1]','{bad','{"figures":{"x":{}}}']:
            with self.subTest(text=text),self.assertRaises(StoryError):compile_project({'story.project.json':text,'A.flow':'@START:\n->END'})
        with self.assertRaises(StoryError):self.workspaces.read_file(self.workspace,'missing.txt')
        (self.source/'bad.txt').write_bytes(b'\xff\xfe')
        with self.assertRaises(StoryError):self.workspaces.read_file(self.workspace,'bad.txt')
