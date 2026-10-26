import os
import tempfile
import unittest

from app.db_manager import DBManager


class DeliveryRuleScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = DBManager(os.path.join(self.temp_dir.name, "rules.db"))
        cursor = self.manager.conn.cursor()
        cursor.executemany("INSERT INTO cookies (id, value, user_id) VALUES (?, 'test', 1)",
                           [('seller-a',), ('seller-b',)])
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('global', 'text', 'global', 1, 1)
            """
        )
        self.global_card = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('account', 'text', 'account', 1, 1)
            """
        )
        self.account_card = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO cards (name, type, text_content, enabled, user_id)
            VALUES ('product', 'text', 'product', 1, 1)
            """
        )
        self.product_card = cursor.lastrowid
        self.manager.conn.commit()

        self.manager.create_delivery_rule("会员", self.global_card, user_id=1)
        self.manager.create_delivery_rule(
            "会员", self.account_card, user_id=1, cookie_id="seller-a"
        )
        self.manager.create_delivery_rule(
            "", self.product_card, user_id=1,
            cookie_id="seller-a", item_id="item-1"
        )

    def tearDown(self):
        self.manager.close()
        self.temp_dir.cleanup()

    def test_exact_product_rule_outranks_keyword_rules(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-a", "item-1"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.product_card])

    def test_account_rule_outranks_global_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-a", "item-2"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.account_card])

    def test_other_account_uses_global_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "会员商品", "seller-b", "item-1"
        )

        self.assertEqual([rule["card_id"] for rule in rules], [self.global_card])

    def test_unmatched_text_does_not_use_generic_rule(self):
        rules = self.manager.get_delivery_rules_for_item(
            "普通商品", "seller-b", "item-1"
        )

        self.assertEqual(rules, [])

    def test_longest_matching_keyword_wins(self):
        self.manager.create_delivery_rule('年度会员', self.product_card, user_id=1)
        rules = self.manager.get_delivery_rules_for_item('年度会员商品', 'seller-b', 'item-2')
        self.assertEqual([r['card_id'] for r in rules], [self.product_card])

    def test_empty_search_and_sql_wildcards_do_not_match_everything(self):
        for text in ('', '%', '_'):
            self.assertEqual(self.manager.get_delivery_rules_for_item(text, 'seller-b', 'item-2'), [])

    def test_equal_specificity_remains_ambiguous(self):
        self.manager.create_delivery_rule('会员', self.product_card, user_id=1)
        self.assertEqual(len(self.manager.get_delivery_rules_for_item('会员', 'seller-b', 'item-2')), 2)

    def test_foreign_user_global_rule_is_not_selected(self):
        self.manager.create_delivery_rule('年度会员', self.product_card, user_id=2)
        rules = self.manager.get_delivery_rules_for_item('年度会员', 'seller-b', 'item-2')
        self.assertEqual([r['card_id'] for r in rules], [self.global_card])


if __name__ == "__main__":
    unittest.main()
