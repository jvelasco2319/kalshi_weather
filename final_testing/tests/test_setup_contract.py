"""No installation, networking, numerical forecasts or trade replay in these tests."""
from pathlib import Path
from unittest.mock import patch
import importlib.util,json,os,sys,tempfile,unittest

HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('portable_install_setup',HERE/'setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class SetupContractTests(unittest.TestCase):
    def test_exact_pins_match_observed_numeric_environment(self):
        expected={'numpy':'2.5.3','scipy':'1.18.1','pandas':'3.0.1','pyarrow':'25.0.1','requests':'2.34.2',
            'eccodes':'2.48.0','findlibs':'0.1.3','tzdata':'2026.4','cffi':'2.1.1'}
        pins=setup.dependencies()
        for name,version in expected.items():self.assertEqual(pins[name],version)

    def test_plan_is_clone_relative_and_does_not_execute(self):
        with patch.object(setup.subprocess,'run',side_effect=AssertionError('No subprocess in plan')):
            plan=setup.command_plan()
        self.assertEqual(Path(plan['repo_root']),HERE.parent)
        self.assertEqual(Path(plan['venv_root']),HERE.parent/'.venv')
        self.assertEqual(plan['commands'][2][-1],str(HERE/'tools/install_assets.py'))
        self.assertNotIn('system-site-packages',' '.join(plan['commands'][0]))
        self.assertIn('--isolated',plan['commands'][1]);self.assertIn('--require-virtualenv',plan['commands'][1])
        self.assertEqual(plan['checkpoint_month'],'2026-10');self.assertEqual(plan['fits'],0);self.assertEqual(plan['orders'],0)

    def test_inherited_python_and_pip_paths_are_removed(self):
        with patch.dict(os.environ,{'PYTHONPATH':'old-private-site','PYTHONHOME':'old-home','VIRTUAL_ENV':'old-venv','PIP_EXTRA_INDEX_URL':'private-index'}):
            env=setup.clean_environment()
        for name in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PIP_EXTRA_INDEX_URL'):self.assertNotIn(name,env)
        self.assertEqual(env['PYTHONNOUSERSITE'],'1')

    def test_existing_system_site_environment_is_rejected(self):
        with patch.object(setup,'cfg_values',return_value={'include-system-site-packages':'true'}),patch.object(setup.subprocess,'run') as runner:
            with self.assertRaises(setup.SetupError):setup.verify_environment(HERE.parent/'.venv')
            runner.assert_not_called()

    def test_asset_installer_must_exist_before_any_environment_mutation(self):
        with patch.object(setup,'platform_check'),patch.object(setup,'ASSET_INSTALLER',HERE/'missing-installer.py'),patch.object(setup.venv,'EnvBuilder') as creator:
            with self.assertRaises(setup.SetupError):setup.install()
            creator.assert_not_called()

    def test_scripts_are_cwd_independent_and_have_no_original_author_paths(self):
        for name in ('setup.ps1','Start.ps1','setup.py','requirements.txt'):
            text=(HERE/name).read_text()
            self.assertNotIn('C:/Users/jarve',text);self.assertNotIn('C:\\Users\\jarve',text)
        self.assertIn('$PSScriptRoot',(HERE/'Start.ps1').read_text())
        self.assertIn('$PSScriptRoot',(HERE/'setup.ps1').read_text())

if __name__=='__main__':unittest.main()
