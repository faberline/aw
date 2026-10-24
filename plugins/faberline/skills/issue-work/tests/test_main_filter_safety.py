"""No-filter-execution fixtures for the main-only attributes absence proof."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('filter_fixture', Path(__file__).with_name('test_main_sync.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
work = fixture.work


class MainFilterSafetyTests(unittest.TestCase):
    setUp = fixture.MainSyncTests.setUp
    command = fixture.MainSyncTests.command
    git = fixture.MainSyncTests.git
    publish = fixture.MainSyncTests.publish
    sync = fixture.MainSyncTests.sync
    assert_held_unchanged = fixture.MainSyncTests.assert_held_unchanged

    def driver(self):
        marker = Path(self.tmp.name)/'driver-must-not-run'
        script = Path(self.tmp.name)/'filter-driver'
        script.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
        script.chmod(0o755)
        for field in ('clean', 'smudge', 'process'):
            self.git(self.main, 'config', 'filter.sentinel.'+field, str(script))
        self.git(self.main, 'config', 'filter.sentinel.required', 'true')
        return marker

    def no_status(self, reason):
        original = work.git
        def audited(*args):
            if 'status' in args or 'merge' in args:
                raise AssertionError('must hold before any status/FF')
            return original(*args)
        with patch.object(work, 'git', side_effect=audited):
            self.assert_held_unchanged(reason)

    def test_global_driver_definitions_without_attributes_allow_ff_without_execution(self):
        marker = self.driver()
        config = Path(self.tmp.name)/'global.gitconfig'
        for field in ('clean', 'smudge', 'process', 'required'):
            value = self.git(self.main, 'config', '--get', 'filter.sentinel.'+field)
            self.command('git', 'config', '--file', str(config), 'filter.sentinel.'+field, value)
            self.git(self.main, 'config', '--unset', 'filter.sentinel.'+field)
        before = config.read_bytes()
        with patch.dict(os.environ, GIT_CONFIG_GLOBAL=str(config)):
            self.assertEqual(self.sync()['status'], 'updated')
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.git(self.main, 'rev-parse', 'HEAD'), self.fetched)
        self.assertFalse(marker.exists())

    def test_root_attributes_even_nonfilter_or_empty_hold(self):
        marker = self.driver()
        for content in ('', '* text=auto\n', '[attr]danger filter=sentinel\n'):
            with self.subTest(content=content):
                (self.main/'.gitattributes').write_text(content)
                self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_nested_incoming_ancestor_attributes_hold(self):
        (self.seed/'nested').mkdir()
        (self.seed/'nested/file').write_text('incoming')
        self.git(self.seed, 'add', 'nested/file'); self.publish()
        (self.main/'nested').mkdir()
        (self.main/'nested/.gitattributes').write_text('* filter=sentinel\n')
        marker = self.driver()
        self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_tracked_incoming_attributes_hold_before_smudge_or_process(self):
        (self.seed/'.gitattributes').write_text('tracked filter=sentinel\n')
        self.git(self.seed, 'add', '.gitattributes'); self.publish()
        marker = self.driver()
        self.no_status('attributes tracked source')
        self.assertFalse(marker.exists())

    def incoming_alias(self, filename):
        # Seed has no configured driver. Only the isolated destination defines
        # malicious global drivers; no incoming attributes are checked out there.
        source = self.seed / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('* filter=sentinel\n')
        self.git(self.seed, 'add', filename); self.publish()
        self.git(self.main, 'config', 'core.ignorecase', 'true')
        self.assertFalse((self.main / filename).exists())
        marker = self.driver()
        config = Path(self.tmp.name)/'alias-global.gitconfig'
        for field in ('clean', 'smudge', 'process', 'required'):
            value = self.git(self.main, 'config', '--get', 'filter.sentinel.'+field)
            self.command('git', 'config', '--file', str(config), 'filter.sentinel.'+field, value)
            self.git(self.main, 'config', '--unset', 'filter.sentinel.'+field)
        before = config.read_bytes()
        with patch.dict(os.environ, GIT_CONFIG_GLOBAL=str(config)):
            self.no_status('attributes tracked source or case alias')
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(marker.exists())
        self.assertFalse((self.main / filename).exists())

    def test_incoming_only_uppercase_root_attributes_hold_without_execution(self):
        self.incoming_alias('.GITATTRIBUTES')

    def test_incoming_only_mixedcase_root_attributes_hold_without_execution(self):
        self.incoming_alias('.GitAttributes')

    def test_incoming_only_uppercase_nested_attributes_hold_without_execution(self):
        self.incoming_alias('nested/.GITATTRIBUTES')

    def test_incoming_only_mixedcase_nested_attributes_hold_without_execution(self):
        self.incoming_alias('nested/.gItAtTrIbUtEs')

    def test_index_only_case_alias_holds_without_worktree_source(self):
        source = self.main/'nested/.GitAttributes'
        source.parent.mkdir()
        source.write_text('* filter=sentinel\n')
        self.git(self.main, 'add', 'nested/.GitAttributes')
        source.unlink()
        marker = self.driver()
        self.no_status('attributes tracked source or case alias')
        self.assertFalse(marker.exists())

    def test_head_only_case_alias_holds_after_index_and_worktree_removal(self):
        source = self.main/'.GITATTRIBUTES'
        source.write_text('* filter=sentinel\n')
        self.git(self.main, 'add', '.GITATTRIBUTES')
        self.git(self.main, 'commit', '-qm', 'fixture head-only alias')
        self.git(self.main, 'update-index', '--force-remove', '.GITATTRIBUTES')
        source.unlink()
        marker = self.driver()
        self.no_status('attributes tracked source or case alias')
        self.assertFalse(marker.exists())

    def test_uncertain_unicode_or_control_name_equivalence_holds(self):
        marker = self.driver()
        oid = self.git(self.main, 'rev-parse', 'HEAD:tracked')
        # Raw index plumbing creates no checkout and invokes no filters.
        for filename in ('.gitattributes\u200c', 'nested/naïve', 'nested/control\tname'):
            with self.subTest(filename=filename):
                self.git(self.main, 'update-index', '--add', '--cacheinfo', '100644,'+oid+','+filename)
                self.no_status('uncertain filesystem name equivalence')
                self.git(self.main, 'update-index', '--force-remove', filename)
        self.assertFalse(marker.exists())

    def test_index_attributes_hold_even_when_worktree_copy_absent(self):
        (self.main/'.gitattributes').write_text('tracked filter=sentinel\n')
        self.git(self.main, 'add', '.gitattributes')
        (self.main/'.gitattributes').unlink()
        marker = self.driver()
        self.no_status('attributes tracked source')
        self.assertFalse(marker.exists())

    def test_info_attributes_hold_before_clean(self):
        (self.main/'.git/info/attributes').write_text('* filter=sentinel\n')
        marker = self.driver()
        self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_global_and_system_default_sources_hold(self):
        source = Path(self.tmp.name)/'external-attributes'
        source.write_text('* filter=sentinel\n')
        marker = self.driver()
        original = work.main_filters.git
        for variable in ('GIT_ATTR_GLOBAL', 'GIT_ATTR_SYSTEM'):
            with self.subTest(variable=variable):
                def location(path, *args, **kwargs):
                    if args == ('var', variable):
                        return (str(source)+'\n').encode()
                    return original(path, *args, **kwargs)
                with patch.object(work.main_filters, 'git', side_effect=location):
                    self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_configured_attributesfile_override_holds_even_empty_or_absent(self):
        marker = self.driver()
        for value in ('', str(Path(self.tmp.name)/'absent-custom-attributes')):
            with self.subTest(value=value):
                self.git(self.main, 'config', 'core.attributesFile', value)
                self.no_status('attributesFile override')
        self.assertFalse(marker.exists())

    def test_environment_attribute_and_index_config_overrides_hold(self):
        marker = self.driver()
        for key in work.main_filters.OVERRIDES:
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                with patch.object(work.main_filters, 'git', side_effect=AssertionError('overrides must hold before Git reads')):
                    with self.assertRaisesRegex(ValueError, 'environment override'):
                        work.main_filters.inspect(self.main, self.fetched)
        self.assertFalse(marker.exists())

    def test_dangling_source_and_repository_ancestor_symlinks_hold(self):
        marker = self.driver()
        source = self.main/'.gitattributes'
        source.symlink_to('missing-attributes')
        self.no_status('attributes source present')
        source.unlink()
        (self.seed/'nested').mkdir()
        (self.seed/'nested/file').write_text('incoming')
        self.git(self.seed, 'add', 'nested/file'); self.publish()
        (self.main/'nested').symlink_to(Path(self.tmp.name)/'missing-directory')
        self.no_status('symlink/non-directory path ancestor')
        self.assertFalse(marker.exists())

    def test_attribute_created_after_last_attribute_read_stops_first_status(self):
        marker = self.driver()
        original = work.main_filters.git
        def changing(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if 'check-attr' in args and '--source='+self.fetched in args:
                (self.main/'.gitattributes').write_text('tracked filter=sentinel\n')
            return result
        with patch.object(work.main_filters, 'git', side_effect=changing):
            self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_attribute_created_during_final_config_read_stops_status(self):
        marker = self.driver()
        original = work.main_filters.git
        calls = [0]
        def changing(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if args[0] == 'config' and '--show-scope' in args:
                calls[0] += 1
                if calls[0] == 2:
                    (self.main/'.git/info/attributes').write_text('* filter=sentinel\n')
            return result
        with patch.object(work.main_filters, 'git', side_effect=changing):
            self.no_status('attributes source present')
        self.assertFalse(marker.exists())

    def test_attribute_appearing_at_immediate_preff_check_holds(self):
        marker = self.driver()
        original = work.main_filters.inspect
        calls = [0]
        def appearing(*args):
            calls[0] += 1
            if calls[0] == 3:
                (self.main/'.git/info/attributes').write_text('* filter=sentinel\n')
            return original(*args)
        original_git = work.git
        def no_merge(*args):
            if 'merge' in args:
                raise AssertionError('FF must not run after attributes appear')
            return original_git(*args)
        with patch.object(work.main_filters, 'inspect', side_effect=appearing), patch.object(work, 'git', side_effect=no_merge):
            self.assert_held_unchanged('attributes source present')
        self.assertEqual(calls[0], 3)
        self.assertFalse(marker.exists())

    def test_effective_config_change_between_snapshots_holds_without_execution(self):
        marker = self.driver()
        original = work.main_filters.inspect
        calls = [0]
        def changing(*args):
            calls[0] += 1
            if calls[0] == 2:
                self.git(self.main, 'config', 'filter.sentinel.required', 'false')
            return original(*args)
        with patch.object(work.main_filters, 'inspect', side_effect=changing):
            self.assert_held_unchanged('changed before fast-forward')
        self.assertFalse(marker.exists())

    def test_environment_change_between_snapshots_holds(self):
        original = work.main_filters.inspect
        calls = [0]
        def changing(*args):
            calls[0] += 1
            if calls[0] == 2:
                os.environ['GIT_CONFIG_NOSYSTEM'] = '0'
            return original(*args)
        with patch.dict(os.environ), patch.object(work.main_filters, 'inspect', side_effect=changing):
            self.assert_held_unchanged('changed before fast-forward')

    def test_index_path_identity_change_immediately_preff_holds(self):
        marker = self.driver()
        original = work.main_filters.inspect
        calls = [0]
        oid = self.git(self.main, 'rev-parse', 'HEAD:tracked')
        def changing(*args):
            calls[0] += 1
            if calls[0] == 3:
                self.git(self.main, 'update-index', '--cacheinfo', '100755,'+oid+',tracked')
            return original(*args)
        with patch.object(work.main_filters, 'inspect', side_effect=changing):
            self.assert_held_unchanged('index/filter visibility changed')
        self.assertFalse(marker.exists())

    def test_submodule_index_holds_before_recursive_status(self):
        marker = self.driver()
        self.git(self.main, 'update-index', '--add', '--cacheinfo', '160000,'+self.base+',nested-repo')
        self.no_status('submodule/unknown index mode')
        self.assertFalse(marker.exists())

    def test_unknown_or_incomplete_attribute_results_hold(self):
        marker = self.driver()
        original = work.main_filters.git
        for incomplete in (True, False):
            with self.subTest(incomplete=incomplete):
                def unknown(path, *args, **kwargs):
                    value = original(path, *args, **kwargs)
                    if 'check-attr' in args:
                        return b'' if incomplete else value.replace(b'unspecified', b'unknown')
                    return value
                with patch.object(work.main_filters, 'git', side_effect=unknown):
                    self.no_status('no absence proof')
        self.assertFalse(marker.exists())

    def test_git_read_error_holds_before_status(self):
        original = work.main_filters.git
        def failing(path, *args, **kwargs):
            if args == ('var', 'GIT_ATTR_SYSTEM'):
                raise OSError('fixture read denied')
            return original(path, *args, **kwargs)
        with patch.object(work.main_filters, 'git', side_effect=failing):
            self.no_status('fixture read denied')


if __name__ == '__main__':
    unittest.main()
