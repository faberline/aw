"""A passing control receipt must name the real clean checkout."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('control_source',Path(__file__).parents[1]/'scripts/control_source.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)


class ControlSourceTests(unittest.TestCase):
    def test_clean_checkout_records_actual_commit_and_tree(self):
        with patch.object(c.subprocess,'check_output',side_effect=['','a'*40+'\n','b'*40+'\n'])as command:
            self.assertEqual(c.capture(Path('/source'),'faberline/aw'),dict(version=1,repo='faberline/aw',commit='a'*40,tree='b'*40))
        self.assertIn('--untracked-files=all',command.call_args_list[0].args[0])

    def test_dirty_checkout_refuses_a_source_receipt(self):
        with patch.object(c.subprocess,'check_output',return_value=' M source.py\n')as command:
            with self.assertRaisesRegex(RuntimeError,'changed'):c.capture(Path('/source'),'faberline/aw')
        self.assertEqual(command.call_count,1)

    def test_foreign_repository_and_unknown_tree_refuse(self):
        with self.assertRaises(RuntimeError):c.capture(Path('/source'),'fork/aw')
        with patch.object(c.subprocess,'check_output',side_effect=['','a'*40,'unknown']):
            with self.assertRaises(RuntimeError):c.capture(Path('/source'),'faberline/aw')
