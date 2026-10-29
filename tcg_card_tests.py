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
from tcg_cards import render_tcg_draw
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
        self.assertEqual(image.width,1080)
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


if __name__=='__main__':
    unittest.main(verbosity=2)
