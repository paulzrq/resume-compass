"""Selection must refresh controls outside the career fragment."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from streamlit.testing.v1 import AppTest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

HARNESS = '''
import streamlit as st
from home_ui import render_career_picker
chosen, _ = render_career_picker(
 fields_by_id={'swe': {'id':'swe','name':'Software Engineer','category':'Tech'}},
 field_categories=['Tech'], fields_by_category={'Tech':['Software Engineer']},
 field_options={'Software Engineer':'swe'}, custom_field_id='custom')
st.toggle('In-depth assessment', key='depth')
st.button('Start assessment', key='start', disabled=not bool(chosen))
'''

class CareerSubmitTest(unittest.TestCase):
    def test_selection_refreshes_app_and_enables_submit_without_toggle(self):
        app = AppTest.from_string(HARNESS).run()
        self.assertTrue(app.button(key='start').disabled)
        app.button(key='home_cat_btn__Tech').click().run()
        with patch.object(st, 'rerun', wraps=st.rerun) as rerun:
            app.button(key='home_role_btn__swe').click().run()
        self.assertFalse(app.exception)
        rerun.assert_called_with(scope='app')
        self.assertFalse(app.button(key='start').disabled)
        self.assertFalse(app.toggle(key='depth').value)
        self.assertEqual(app.session_state['home_career_chosen'], 'swe')
