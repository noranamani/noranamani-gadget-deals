"""No-network relevance regressions; product names are untrusted titles."""
import unittest
from category_relevance import is_relevant


class RelevanceTests(unittest.TestCase):
    def test_rejects_confirmed_wrong_product_types(self):
        bad = [
            ("microsd", "SDカードリーダー 4in1 USB-C microSD対応"),
            ("usb-charger", "シガーソケット USB-C PD 45W カーチャージャー"),
            ("wireless-charger", "30W AC充電器 USB-Cx1＋USB-Ax1"),
            ("mobile-router", "【国内専用・追加チャージ専用】 ポケットWiFi"),
            ("mobile-router", "MR1専用 レザー ケース カバー ポケットWi-Fi"),
            ("car-charger", "USB-C から シガーソケット12V メスソケット 変換アダプター"),
        ]
        for category, title in bad:
            with self.subTest(category=category, title=title):
                self.assertFalse(is_relevant(category, title))

    def test_keeps_plausible_products_and_card_adapter_bundles(self):
        good = [
            ("microsd", "microSDカード 128GB 変換アダプタ付"),
            ("microsd", "マイクロSDカード 256GB 無料のUSB3.0カードリーダー"),
            ("usb-charger", "USB Type-C AC充電器 PD65W"),
            ("wireless-charger", "Qi2 ワイヤレス充電器 15W USB-C入力"),
            ("mobile-router", "Aterm MR05LN SIMフリー モバイルWi-Fiルーター"),
            ("car-charger", "シガーソケット USB-C PD カーチャージャー"),
            ("power-bank", "モバイルバッテリー PD 10000mAh"),
            ("microsd", "検証商品 1"),
        ]
        for category, title in good:
            with self.subTest(category=category, title=title):
                self.assertTrue(is_relevant(category, title))
    def test_unrecognized_categories_are_not_blocked(self):
        self.assertTrue(is_relevant("future-category", "検証商品 2"))


if __name__ == "__main__":
    unittest.main()
