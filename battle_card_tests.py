"""Battle card content / pagination regressions with no network dependency."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault('ASTRBOT_ROOT', tempfile.mkdtemp(prefix='partner-battle-cards-'))

from PIL import Image, ImageDraw
from battle_cards import MAX_HEIGHT, WIDTH, render_battle_cards
from battle_report import build_battle_report
from pokedex_db import PokedexDB


class BattleCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = PokedexDB()

    def snapshot(self):
        return build_battle_report(self.db, self.db.pokemon('皮卡丘'), self.db.pokemon('喷火龙'), 50, seed=7, detailed=True)

    def render(self, battle, loader=None):
        texts, bounds = [], []
        original = ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            bounds.append((draw._image.size, draw.textbbox(xy, str(text), font=kwargs.get('font'), anchor=kwargs.get('anchor'))))
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, 'text', record):
            cards = render_battle_cards(battle, image_loader=loader)
        for raw in cards:
            with Image.open(io.BytesIO(raw)) as image:
                self.assertEqual(image.width, WIDTH)
                self.assertLessEqual(image.height, MAX_HEIGHT)
                self.assertEqual(image.format, 'JPEG')
        for (width, height), (left, top, right, bottom) in bounds:
            self.assertGreaterEqual(left, 20)
            self.assertGreaterEqual(top, 18)
            self.assertLessEqual(right, width-20)
            self.assertLessEqual(bottom, height-20)
        return cards, ''.join(texts)

    def test_real_moves_effects_and_decisions_remain_readable(self):
        cards, text = self.render(self.snapshot())
        self.assertGreaterEqual(len(cards), 4)
        self.assertIn('伏特攻击', text)
        self.assertIn('模拟效果：', text)
        self.assertIn('图鉴说明：', text)
        self.assertIn('选择【', text)
        self.assertIn('未模拟', text)

    def test_details_keep_all_rounds_and_preview_discloses_omission(self):
        battle = self.snapshot()
        battle['result']['turns'] = 25
        battle['result']['turn_log'] = [[f'第{n}回合', f'ROUND_MARK_{n:02d} 命中并造成伤害，HP下降。'] for n in range(1, 26)]
        cards, text = self.render(battle)
        self.assertIn('ROUND_MARK_13', text)
        self.assertIn('ROUND_MARK_25', text)
        battle['detailed'] = False
        previews, preview = self.render(battle)
        self.assertNotIn('ROUND_MARK_13', preview)
        self.assertIn('ROUND_MARK_25', preview)
        self.assertIn('省略中间 17 回合', preview)
        self.assertLess(len(previews), len(cards))

    def test_long_names_descriptions_and_oversized_round_are_paginated(self):
        battle = self.snapshot()
        name = '长名称宝可梦（特殊地区形态）' * 20
        first = battle['result']['setup'][0]
        final = battle['result']['final'].pop(first['name'])
        first['name'] = name
        battle['result']['final'][name] = final
        first['moves'][0]['description'] = '这段效果说明需要完整保留。' * 80 + '末尾机制标记'
        battle['result']['turn_log'] = [['长回合记录：' + ('所有状态与能力变化均需保留。' * 180) + '回合末尾标记']]
        cards, text = self.render(battle)
        self.assertGreater(len(cards), 4)
        self.assertIn('末尾机制标记', text)
        self.assertIn('回合末尾标记', text)
        self.assertIn('（续）', text)

    def test_draw_mirror_trainers_and_failed_artwork_loader(self):
        battle = build_battle_report(self.db, self.db.pokemon('梦幻'), self.db.pokemon('梦幻'), 50, seed=7, detailed=True)
        battle['result']['winner'] = '平局'
        battle['result']['reason'] = '双方倒下'
        battle['trainer_names'] = ['小智', '小霞']
        battle['owned_items'] = [{'shiny': False}, {'shiny': True}]
        cards, text = self.render(battle, lambda row: b'not-an-image')
        self.assertIn('DRAW / 平局', text)
        self.assertIn('梦幻（甲）', text)
        self.assertIn('梦幻（乙）', text)
        self.assertIn('小智', text)
        self.assertIn('小霞', text)
        self.assertIn('闪光个体', text)
        self.assertIn('立绘暂未缓存', text)

    def test_valid_artwork_bytes_and_unchanged_battle_snapshot(self):
        battle = self.snapshot()
        before = deepcopy(battle)
        stream = io.BytesIO()
        Image.new('RGBA', (80, 160), (240, 200, 30, 255)).save(stream, 'PNG')
        self.render(battle, lambda row: stream.getvalue())
        self.assertEqual(battle, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
