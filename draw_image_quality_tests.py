"""Native card detail survives multi-draw composition and JPEG colour encoding."""
import io
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('ASTRBOT_ROOT', tempfile.mkdtemp(prefix='partner-draw-quality-'))

from PIL import Image
from PIL.JpegImagePlugin import get_sampling
import rarity_cards
import tcg_cards
import yugioh_cards
from partner_cards import render_draw_card, _encode

OPTIONS = dict(nickname='训练家', remaining=0, daily_limit=10, dust=0,
               pity_ssr=0, ssr_pity_after=30)


class DrawImageQualityTests(unittest.TestCase):
    def test_all_styles_keep_single_pixel_colour_detail_in_multi_draws(self):
        # These colours have similar luminance. Chroma subsampling or resizing
        # blends them together, concealing the loss that a size-only test misses.
        for style, module, method, width, height, margin in (
            ('classic', rarity_cards, '_single_image', 1080, 1800, 24),
            ('tcg', tcg_cards, '_card', 900, 1500, 22),
            ('yugioh', yugioh_cards, '_card', 1080, 1680, 20),
        ):
            with self.subTest(style=style):
                card = Image.new('RGBA', (width, height), (240, 235, 225, 255))
                for x in range(96):
                    colour = (240, 0, 0, 255) if x % 2 == 0 else (0, 123, 0, 255)
                    card.paste(colour, (100+x, 100, 101+x, 124))
                def make_card(*args, **kwargs):
                    return card.copy() if method == '_single_image' else (card.copy(), ())
                with patch.object(module, method, side_effect=make_card), \
                        patch.object(Image.Image, 'resize', side_effect=AssertionError('Card pixels were rescaled')):
                    raw = render_draw_card([object()] * 4, style=style, **OPTIONS)
                with Image.open(io.BytesIO(raw)) as output:
                    self.assertGreaterEqual(output.width, width * 2)
                    self.assertGreaterEqual(output.height, height * 2)
                    self.assertLess(output.height, 4000)
                    self.assertEqual(get_sampling(output), 0)
                    output.load()
                    red = output.getpixel((margin+140, margin+112))
                    green = output.getpixel((margin+141, margin+112))
                    self.assertGreater(red[0] - green[0], 180)
                    self.assertGreater(green[1] - red[1], 90)

    def test_collection_images_also_preserve_complete_colour_sampling(self):
        image = Image.new('RGB', (100, 100), (120, 30, 200))
        with Image.open(io.BytesIO(_encode(image))) as encoded:
            self.assertEqual(get_sampling(encoded), 0)
            self.assertEqual(encoded.size, image.size)


if __name__ == '__main__':
    unittest.main()
