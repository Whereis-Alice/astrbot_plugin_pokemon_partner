"""Battle long-image regressions: full content, readable bounds, and safe limits."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-battle-cards-"))

from PIL import Image, ImageDraw
import battle_cards
from battle_cards import MAX_HEIGHT, WIDTH, render_battle_cards
from battle_report import build_battle_report
from pokedex_db import PokedexDB


class BattleCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = PokedexDB()

    def snapshot(self):
        return build_battle_report(
            self.db,
            self.db.pokemon("皮卡丘"),
            self.db.pokemon("喷火龙"),
            50,
            seed=7,
            detailed=True,
        )

    def render(self, battle, loader=None):
        texts, bounds, canvases, by_canvas = [], [], [], {}
        original = ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            key = id(draw._image)
            if key not in by_canvas:
                by_canvas[key] = []
                canvases.append(
                    draw._image
                )  # prevent closed image IDs from being reused
            by_canvas[key].append(str(text))
            texts.append(str(text))
            bounds.append(
                (
                    draw._image.size,
                    draw.textbbox(
                        xy,
                        str(text),
                        font=kwargs.get("font"),
                        anchor=kwargs.get("anchor"),
                    ),
                )
            )
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            cards = render_battle_cards(battle, image_loader=loader)
        self.assertEqual(len(cards), 3)
        for raw in cards:
            with Image.open(io.BytesIO(raw)) as image:
                self.assertEqual(image.width, WIDTH)
                self.assertLessEqual(image.height, MAX_HEIGHT)
                self.assertEqual(image.format, "JPEG")
        for (width, height), (left, top, right, bottom) in bounds:
            self.assertGreaterEqual(left, 20)
            self.assertGreaterEqual(top, 18)
            self.assertLessEqual(right, width - 20)
            self.assertLessEqual(bottom, height - 20)
        sections = ["".join(by_canvas[id(canvas)]) for canvas in canvases]
        self.assertEqual(len(sections), 3)
        return cards, "".join(texts), sections

    def test_real_moves_effects_and_decisions_remain_readable(self):
        battle = self.snapshot()
        cards, text, sections = self.render(battle)
        self.assertIn("双方配招与效果", sections[1])
        self.assertIn("皮卡丘", sections[1])
        self.assertIn("喷火龙", sections[1])
        for setup in battle["result"]["setup"]:
            for move in setup["moves"]:
                self.assertIn(move["name"], sections[1])
                self.assertIn(str(move["effects"]), sections[1])
                if move["description"]:
                    self.assertIn(str(move["description"]), sections[1])
        self.assertIn("对战过程", sections[2])
        self.assertNotIn("逐回合复盘", text)
        self.assertIn("伏特攻击", text)
        self.assertIn("模拟效果：", text)
        self.assertIn("图鉴说明：", text)
        self.assertIn("选择【", text)
        self.assertIn("未模拟", text)

    def test_images_keep_all_rounds_without_detailed_flag(self):
        battle = self.snapshot()
        battle["result"]["turns"] = 25
        battle["result"]["turn_log"] = [
            [f"第{n}回合", f"ROUND_MARK_{n:02d} 命中并造成伤害，HP下降。"]
            for n in range(1, 26)
        ]
        for detailed in (True, False):
            with self.subTest(detailed=detailed):
                battle["detailed"] = detailed
                cards, text, sections = self.render(battle)
                for number in range(1, 26):
                    self.assertIn(f"ROUND_MARK_{number:02d}", sections[2])
                self.assertNotIn("省略中间", text)
                self.assertNotIn("已折叠", text)
                with Image.open(io.BytesIO(cards[2])) as process:
                    self.assertGreater(process.height, 1680)

    def test_long_names_descriptions_and_round_stay_on_their_single_images(self):
        battle = self.snapshot()
        name = "长名称宝可梦（特殊地区形态）" * 20
        first = battle["result"]["setup"][0]
        final = battle["result"]["final"].pop(first["name"])
        first["name"] = name
        battle["result"]["final"][name] = final
        first["moves"][0]["description"] = (
            "这段效果说明需要完整保留。" * 80 + "末尾机制标记"
        )
        battle["result"]["turn_log"] = [
            ["长回合记录：" + ("所有状态与能力变化均需保留。" * 180) + "回合末尾标记"]
        ]
        cards, text, sections = self.render(battle)
        self.assertIn(name, sections[1])
        self.assertIn(first["moves"][0]["description"], sections[1])
        self.assertIn(battle["result"]["turn_log"][0][0], sections[2])
        self.assertNotIn("（续）", text)

    def test_draw_mirror_trainers_and_failed_artwork_loader(self):
        battle = build_battle_report(
            self.db,
            self.db.pokemon("梦幻"),
            self.db.pokemon("梦幻"),
            50,
            seed=7,
            detailed=True,
        )
        battle["result"]["winner"] = "平局"
        battle["result"]["reason"] = "双方倒下"
        battle["trainer_names"] = ["小智", "小霞"]
        battle["owned_items"] = [{"shiny": False}, {"shiny": True}]
        cards, text, sections = self.render(battle, lambda row: b"not-an-image")
        self.assertIn("DRAW / 平局", text)
        self.assertIn("梦幻（甲）", text)
        self.assertIn("梦幻（乙）", text)
        self.assertIn("小智", text)
        self.assertIn("小霞", text)
        self.assertIn("闪光个体", text)
        self.assertIn("甲方", sections[1])
        self.assertIn("乙方", sections[1])
        self.assertIn("立绘暂未缓存", text)

    def test_mirror_long_forms_and_empty_turns_remain_complete(self):
        battle = build_battle_report(
            self.db,
            self.db.pokemon("梦幻"),
            self.db.pokemon("梦幻"),
            50,
            seed=7,
            detailed=False,
        )
        form = "镜像的特殊地区形态" * 18
        battle["row1"]["form_name"] = form
        battle["row2"]["form_name"] = form
        battle["trainer_names"] = ["甲方训练家" * 15, "乙方训练家" * 15]
        battle["result"]["turn_log"] = []
        battle["result"]["setup"][0]["moves"] = []
        cards, text, sections = self.render(battle)
        self.assertIn(form + "（甲）", sections[1])
        self.assertIn(form + "（乙）", sections[1])
        self.assertIn(battle["trainer_names"][0], sections[1])
        self.assertIn(battle["trainer_names"][1], sections[1])
        self.assertIn("无可用招式", sections[1])
        self.assertIn("挣扎", sections[1])
        self.assertIn("本场无行动回合", sections[2])

    def test_hundred_rounds_have_all_markers_on_one_image(self):
        battle = self.snapshot()
        battle["result"]["turns"] = 100
        battle["detailed"] = False
        battle["result"]["turn_log"] = [
            [
                f"第{n}回合：甲方先手",
                f"ROUND_MARK_{n:03d}",
                "甲方选择【十万伏特】：属性有效，预计可以造成伤害。",
                "甲方使用【十万伏特】，命中并造成 42 点伤害；对方 HP 100/200。",
                "乙方选择【火焰放射】：根据剩余 HP 与属性估算选择。",
                "乙方使用【火焰放射】，命中并造成 50 点伤害；对方 HP 80/200。",
                "回合结束结算：异常状态、能力等级与剩余 PP 均已记录。",
            ]
            for n in range(1, 101)
        ]
        cards, text, sections = self.render(battle)
        for number in range(1, 101):
            self.assertIn(f"ROUND_MARK_{number:03d}", sections[2])
        with Image.open(io.BytesIO(cards[2])) as process:
            self.assertGreater(process.height, 20000)
            self.assertLessEqual(process.height, MAX_HEIGHT)

    def test_excessive_snapshot_fails_before_long_canvas_allocation(self):
        battle = self.snapshot()
        before = deepcopy(battle)
        with (
            patch.object(battle_cards, "MAX_HEIGHT", 1500),
            patch.object(battle_cards, "_base", wraps=battle_cards._base) as allocate,
        ):
            with self.assertRaisesRegex(ValueError, "完整长图超过安全尺寸"):
                render_battle_cards(battle)
        self.assertEqual([call.args[0] for call in allocate.call_args_list], [1420])
        self.assertEqual(battle, before)

    def test_valid_artwork_bytes_and_unchanged_battle_snapshot(self):
        battle = self.snapshot()
        before = deepcopy(battle)
        stream = io.BytesIO()
        Image.new("RGBA", (80, 160), (240, 200, 30, 255)).save(stream, "PNG")
        self.render(battle, lambda row: stream.getvalue())
        self.assertEqual(battle, before)

    def test_transparent_artwork_shows_missing_art_placeholder(self):
        battle = self.snapshot()
        cards, text, sections = self.render(
            battle, lambda row: Image.new("RGBA", (80, 120), (0, 0, 0, 0))
        )
        self.assertEqual(sections[0].count("立绘暂未缓存"), 2)

    def test_adjacent_closing_punctuation_keeps_preceding_text(self):
        text = "梦幻（乙） 使用铁蹄光线，梦幻（甲） 受到 54 点伤害（威力140，属性×1，本系×1），HP 111/175。"
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        lines = battle_cards._wrap(draw, text, 26, 1026)
        self.assertEqual("".join(lines), text)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(line and line[0] not in "），。" for line in lines))
        punctuation = "），。" * 80
        lines = battle_cards._wrap(draw, punctuation, 26, 180)
        self.assertEqual("".join(lines), punctuation)
        self.assertTrue(all(draw.textlength(line, font=battle_cards._font(26)) <= 180 for line in lines))

    def test_same_dex_normal_and_shiny_request_distinct_art_without_mutation(self):
        battle = build_battle_report(
            self.db,
            self.db.pokemon("梦幻"),
            self.db.pokemon("梦幻"),
            50,
            seed=7,
            detailed=False,
        )
        battle["owned_items"] = [{"shiny": False}, {"shiny": True}]
        before = deepcopy(battle)
        requests = []

        def loader(row):
            requests.append(dict(row))
            row["loader_local"] = True
            return Image.new("RGBA", (80, 120), (90, 170, 235, 255))

        self.render(battle, loader)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["id"], requests[1]["id"])
        self.assertNotIn("_artwork_shiny", requests[0])
        self.assertIs(requests[1]["_artwork_shiny"], True)
        self.assertEqual(battle, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
