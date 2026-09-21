import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import share_card
from PIL import Image

class LayoutTests(unittest.TestCase):
    def test_five_distinct_hd_layouts(self):
        cards=[]
        for layout in 'ABDEF':
            data=share_card.generate_share_card('swe','软件工程师',78,layout)
            self.assertEqual(Image.open(io.BytesIO(data)).size,(2160,2880))
            cards.append(data)
        self.assertEqual(len(set(cards)),5)

    def test_picker_only_approved_layouts(self):
        with patch.object(share_card.secrets,'choice',return_value='E') as choice:
            self.assertEqual(share_card.choose_share_layout(),'E')
            self.assertEqual(choice.call_args.args[0],tuple('ABDEF'))

    def test_invalid_layout(self):
        with self.assertRaises(ValueError):
            share_card.generate_share_card('swe','软件工程师',78,'C')

    def test_direction_without_mascot(self):
        data=share_card.generate_share_card('custom','很长的自定义职业方向用于测试文字适配边界',100,'E')
        self.assertEqual(Image.open(io.BytesIO(data)).size,(2160,2880))
