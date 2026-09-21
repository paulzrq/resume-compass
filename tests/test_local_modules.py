import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from local_modules import load_current_module
import share_card

class LocalModuleTests(unittest.TestCase):
 def test_stale_share_module_is_refreshed(self):
  # Simulate the old module left in a long-lived deployment process.
  original=share_card.choose_share_layout
  del share_card.choose_share_layout
  share_card._resume_source_fingerprint='previous-deployment'
  try:
   with self.assertRaises(ImportError):
    exec('from share_card import choose_share_layout',{})
   current=load_current_module('share_card')
   self.assertIn(current.choose_share_layout(),'ABDEF')
   self.assertTrue(callable(current.render_share_preview))
  finally:
   if not hasattr(share_card,'choose_share_layout'):
    share_card.choose_share_layout=original
 def test_unchanged_module_preserves_cache(self):
  current=load_current_module('share_card')
  with patch('local_modules.importlib.reload') as reload:
   self.assertIs(load_current_module('share_card'),current)
   reload.assert_not_called()
if __name__=='__main__':unittest.main()
