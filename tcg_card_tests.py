"""Real-renderer checks for draw data, portrait routing, layout and bounded output."""
import io
import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="partner-tcg-cards-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

from PIL import Image, ImageDraw
from gacha import DrawItem, GachaRules, build_partners
from pokedex_db import PokedexDB
import tcg_cards
from tcg_cards import CARD_W, CARD_H, _paste_art, render_tcg_draw
from card_matchups import matchup_groups
from tcg_materials import render_print_surface
from tcg_symbols import render_energy_icon


class TcgCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = {p.key: p for p in build_partners(PokedexDB(), GachaRules.from_config({'include_alternate_forms': True}))}

    def item(self, key='25', rarity='R', shiny=False):
        p = self.pool[key]
        return DrawItem(p.key,p.dex,p.dex_raw,p.name,p.types,p.bst,rarity,shiny,True,0,1,p.row)

    def render(self, items, loader=None):
        before = deepcopy(items)
        raw = render_tcg_draw(items,nickname='测试训练家',remaining=2,daily_limit=3,dust=123,
                             pity_ssr=1,ssr_pity_after=30,image_loader=loader)
        self.assertEqual(items,before)
        image = Image.open(io.BytesIO(raw))
        self.assertEqual(image.width, CARD_W if len(items) == 1 else 1080)
        if len(items) == 1:
            self.assertEqual(image.height, CARD_H)
        self.assertLess(image.height,5000)
        image.verify()
        return raw

    def test_five_editions_render_independent_images_without_changing_draws(self):
        values = [self.item(rarity=kind) for kind in ('C','R','SR','SSR')] + [self.item(shiny=True)]
        images = [self.render([value]) for value in values]
        self.assertEqual(len(set(images)),5)

    def test_shiny_portrait_request_does_not_mutate_the_original_row(self):
        calls = []
        def loader(row):
            calls.append(row)
            return Image.new('RGBA',(200,280),(255,190,70,255))
        value = self.item(shiny=True)
        self.render([value],loader)
        self.assertTrue(calls[0]['_artwork_shiny'])
        self.assertIsNot(calls[0],value.row)
        self.assertNotIn('_artwork_shiny',value.row)

    def test_portraits_trim_transparency_and_scale_inside_the_existing_box(self):
        # Different aspect ratios expose both clipping and off-centre placement.
        box = (40, 50, 600, 420)
        for content in ((100, 75, 450, 225), (100, 75, 250, 425), (100, 75, 400, 425)):
            with self.subTest(content=content):
                source = Image.new('RGBA', (500, 500))
                source.paste((230, 40, 175, 255), content)
                before = source.tobytes()
                composites = []
                original = Image.Image.alpha_composite

                def record(base, image, dest=(0, 0), source=(0, 0)):
                    composites.append((image.copy(), dest))
                    return original(base, image, dest, source)

                with patch.object(Image.Image, 'alpha_composite', record):
                    _paste_art(Image.new('RGBA', (700, 550)), self.item(), lambda row: source, box, 'R')
                portrait, (x, y) = composites[-1]
                self.assertEqual(source.tobytes(), before)
                self.assertEqual(portrait.getchannel('A').getbbox(), (0, 0, *portrait.size))
                self.assertLessEqual(portrait.width, box[2])
                self.assertLessEqual(portrait.height, box[3])
                self.assertTrue(portrait.width == box[2] or portrait.height == box[3])
                self.assertEqual(x, box[0] + (box[2] - portrait.width) // 2)
                self.assertEqual(y, box[1] + (box[3] - portrait.height) // 2)
                aspect = (content[2] - content[0]) / (content[3] - content[1])
                self.assertAlmostEqual(portrait.width / portrait.height, aspect, delta=.01)

    def test_fully_transparent_art_shows_the_missing_art_placeholder(self):
        source = Image.new('RGBA', (475, 475), (200, 80, 40, 0))
        missing, transparent = (Image.new('RGBA', (900, 800), (245, 239, 225, 255)) for _ in range(2))
        _paste_art(missing, self.item(), lambda row: None, (87, 199, 726, 437), 'R')
        _paste_art(transparent, self.item(), lambda row: source, (87, 199, 726, 437), 'R')
        self.assertEqual(transparent.tobytes(), missing.tobytes())
        self.assertIsNone(source.getchannel('A').getbbox())

    def test_long_form_names_and_visible_text_stay_on_their_canvas(self):
        value = self.item('555:伽勒尔达摩狒狒（达摩模式）','SR')
        texts, bad = [], []
        original = ImageDraw.ImageDraw.text
        def record(draw,xy,text,*args,**kwargs):
            if not text:
                return original(draw,xy,text,*args,**kwargs)
            texts.append(str(text))
            box = draw.textbbox(xy,str(text),font=kwargs.get('font'),anchor=kwargs.get('anchor'),
                                stroke_width=kwargs.get('stroke_width',0))
            width,height=draw._image.size
            if box[0]<0 or box[1]<0 or box[2]>width or box[3]>height:
                bad.append((text,box,(width,height)))
            return original(draw,xy,text,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',record):
            self.render([value])
        self.assertFalse(bad,bad)
        self.assertIn(value.name,''.join(texts))

    def test_long_description_does_not_create_an_unbounded_image(self):
        value=self.item()
        value=replace(value,row={**value.row,'profile':'很长的图鉴说明。'*1200})
        raw=self.render([value])
        with Image.open(io.BytesIO(raw)) as image:
            self.assertLess(image.height,3000)

    def test_ten_draws_keep_every_result_and_shiny_art_request(self):
        values=[self.item(key,kind,shiny) for key,kind,shiny in
                [('1','C',False),('25','R',False),('448','SR',False),('384','SSR',False),('151','SSR',True)]]*2
        calls=[]
        def loader(row):
            calls.append(row)
            return None
        self.render(values,loader)
        self.assertEqual(len(calls),10)
        self.assertEqual(sum(bool(row.get('_artwork_shiny')) for row in calls),2)

    def test_cached_materials_and_icons_return_independent_images(self):
        for function,args in ((render_print_surface,('SHINY','超能力',120,180)),
                              (render_energy_icon,('电',32))):
            first=function(*args)
            second=function(*args)
            self.assertEqual(first.tobytes(),second.tobytes())
            first.paste((0,0,0,0),(0,0,first.width,first.height))
            self.assertEqual(function(*args).tobytes(),second.tobytes())

    def test_empty_results_are_rejected(self):
        with self.assertRaises(ValueError):
            self.render([])

    def test_pure_card_does_not_include_transient_draw_state_or_continuation(self):
        value = self.item('857', 'C')
        texts = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            first = self.render([value])
        second = render_tcg_draw([replace(value, is_new=False, dust=999)], nickname='另一位训练家',
            remaining=0, daily_limit=100, dust=99999, pity_ssr=29, ssr_pity_after=30)
        self.assertEqual(first, second)
        visible = ''.join(texts)
        for removed in ('全国图鉴', 'NEW', '今日宝可梦', '今日剩余次数', '星尘', '加入图鉴', '图鉴摘要（续）'):
            self.assertNotIn(removed, visible)

    def test_defensive_type_icons_include_quadruple_weakness_and_immunity(self):
        value = self.item('6', 'R')
        texts, icons = [], []
        original = ImageDraw.ImageDraw.text
        render_icon = tcg_cards.render_energy_icon
        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            return original(draw, xy, text, *args, **kwargs)
        def icon(name, size):
            icons.append((name, size))
            return render_icon(name, size)
        with patch.object(ImageDraw.ImageDraw, 'text', record), patch.object(tcg_cards, 'render_energy_icon', icon):
            self.render([value])
        visible = ''.join(texts)
        self.assertIn('岩石 ×4', visible)
        self.assertIn('地面 ×0', visible)
        self.assertIn(('岩石', 32), icons)
        self.assertIn(('地面', 32), icons)
        self.assertIn('弱点', visible)
        self.assertIn('抗性', visible)
        self.assertIn('免疫', visible)

    def test_many_resistances_keep_all_tokens_inside_the_physical_card(self):
        value = self.item('82', 'SSR')
        texts, bad = [], []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            bounds = draw.textbbox(xy, str(text), font=kwargs.get('font'), anchor=kwargs.get('anchor'),
                                  stroke_width=kwargs.get('stroke_width', 0))
            if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > draw._image.width or bounds[3] > draw._image.height:
                bad.append((text, bounds, draw._image.size))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            self.render([value])
        self.assertFalse(bad, bad)
        visible = ''.join(texts)
        for entries in matchup_groups(value.row, value.types).values():
            for name, multiplier in entries:
                self.assertIn(f'{name} ×{multiplier:g}', visible)

    def test_missing_practical_data_goes_to_text_without_profile_continuation(self):
        value = self.item('857', 'C')
        value = replace(value, row={**value.row,
            'abilities': '很长的特性名称、' * 45 + '特性末尾标记',
            'evolution': '非常长的进化链 → ' * 12 + '進化末尾标记',
            'profile': '图鉴摘要仅在卡面截取。' * 150 + '摘要末尾标记'})
        missing = []
        render_tcg_draw([value], nickname='训练家', remaining=2, daily_limit=3,
            dust=0, pity_ssr=1, ssr_pity_after=30, details_out=missing)
        self.assertIn('特性末尾标记', '\n'.join(missing))
        self.assertIn('進化末尾标记', '\n'.join(missing))
        self.assertNotIn('摘要末尾标记', '\n'.join(missing))
        self.assertTrue(missing[0].startswith('#0857 提布莉姆：'))

    def test_long_title_keeps_its_reserved_column_clear_of_hp_and_energy(self):
        value = self.item('555:伽勒尔达摩狒狒（达摩模式）', 'SR')
        measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        _size, title_lines = tcg_cards._name_layout(measure, value.name)
        bounds = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            if xy[0] == 54 and str(text) in title_lines:
                bounds.append(draw.textbbox(xy, str(text), font=kwargs.get('font'),
                    anchor=kwargs.get('anchor'), stroke_width=kwargs.get('stroke_width', 0)))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            self.render([value])
        self.assertEqual(len(bounds), len(title_lines))
        for left, top, right, bottom in bounds:
            self.assertGreaterEqual(left, 50)
            self.assertLess(right, 602)

    def test_form_name_shrinks_before_using_a_second_title_line(self):
        measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        size, lines = tcg_cards._name_layout(measure, '猫老大（阿罗拉的样子）')
        self.assertEqual(len(lines), 1)
        self.assertGreaterEqual(size, 36)
        self.assertLessEqual(measure.textlength(lines[0], font=tcg_cards._font(size)), 530)

    def test_tall_card_preserves_portrait_space_for_long_form_names(self):
        value = self.item('555:伽勒尔达摩狒狒（达摩模式）', 'SR')
        with patch.object(tcg_cards, '_paste_art', wraps=tcg_cards._paste_art) as paste:
            self.render([value])
        box = paste.call_args.args[3]
        self.assertGreaterEqual(box[3], 390)
        self.assertEqual((CARD_W, CARD_H), (900, 1500))

    def test_practical_body_text_remains_at_least_twenty_four_pixels(self):
        for kind, shiny in (('C', False), ('R', False), ('SR', False), ('SSR', False), ('SSR', True)):
            value = self.item('857', kind, shiny)
            small = []
            original = ImageDraw.ImageDraw.text
            def record(draw, xy, text, *args, **kwargs):
                size = getattr(kwargs.get('font'), 'size', 0)
                auxiliary = str(text) in {'攻击', '防御', '特攻', '特防', '速度', '总和'} or str(text).isdigit()
                if 744 < xy[1] < CARD_H - 84 and not auxiliary and size < 24:
                    small.append((text, size))
                return original(draw, xy, text, *args, **kwargs)
            with self.subTest(kind=kind, shiny=shiny), patch.object(ImageDraw.ImageDraw, 'text', record):
                self.render([value])
                self.assertFalse(small, small)


if __name__=='__main__':
    unittest.main(verbosity=2)
