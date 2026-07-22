import unittest

from naming_utils import (
    DAZ_RESERVED_PREFIXES,
    build_dim_zip_filename,
    build_product_store_idx,
    build_support_cover_filename,
    format_dim_sku,
    sanitize_dim_zip_product_name,
    sanitize_support_filename_segment,
    validate_dim_part,
    validate_dim_prefix,
    validate_dim_sku,
    validate_dim_zip_filename,
)


class NamingUtilsTests(unittest.TestCase):
    def test_product_store_idx_is_only_emitted_for_daz_reserved_sources(self):
        for prefix in DAZ_RESERVED_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertEqual(build_product_store_idx(prefix, 70127, 1), "70127-1")
        self.assertIsNone(build_product_store_idx("RE", 70127, 1))
        self.assertIsNone(build_product_store_idx("LOCAL", 70127, 1))

    def test_dim_zip_product_segment_allows_only_letters_and_digits(self):
        self.assertEqual(
            sanitize_dim_zip_product_name("X Fashion - Series_3 for G8-8.1 Females"),
            "XFashionSeries3forG881Females",
        )

    def test_dim_zip_filename_pads_sku_and_part(self):
        self.assertEqual(
            build_dim_zip_filename("RE", "70127", 1, "Bull Boxers G8M G8.1M G9"),
            "RE00070127-01_BullBoxersG8MG81MG9.zip",
        )

    def test_blank_preview_prefix_uses_the_non_reserved_local_prefix(self):
        self.assertEqual(
            build_dim_zip_filename("", "1", 1, "Local Product"),
            "LOCAL00000001-01_LocalProduct.zip",
        )

    def test_all_builtin_store_prefixes_are_officially_valid(self):
        prefixes = (
            "IM", "RO", "RH", "RE", "CB", "CG", "DA", "SH", "SF",
            "F3D", "TS", "D3X", "PR", "FR", "LOCAL",
        )
        self.assertEqual(
            [validate_dim_prefix(prefix) for prefix in prefixes],
            list(prefixes),
        )

    def test_prefix_is_normalized_but_unsafe_values_are_rejected(self):
        self.assertEqual(validate_dim_prefix(" re "), "RE")
        for invalid in ("", "3DX", "1", "ABCDEFGH", "A-B", "../RO", "A B", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_dim_prefix(invalid)

    def test_sku_boundaries_are_enforced(self):
        self.assertEqual(validate_dim_sku("1"), 1)
        self.assertEqual(validate_dim_sku("99999999"), 99_999_999)
        self.assertEqual(format_dim_sku("1"), "00000001")
        for invalid in ("", "0", "00000000", "100000000", "-1", "1.5", "abc"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_dim_sku(invalid)

    def test_part_boundaries_are_enforced(self):
        self.assertEqual(validate_dim_part("1"), 1)
        self.assertEqual(validate_dim_part(99), 99)
        for invalid in (0, 100, -1, "", "1.0", True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_dim_part(invalid)

    def test_unsafe_session_values_cannot_become_a_zip_path(self):
        for prefix, sku, part in (
            ("../RO", "1", 1),
            ("RO", "../1", 1),
            ("RO", "1", "../1"),
        ):
            with self.subTest(prefix=prefix, sku=sku, part=part):
                with self.assertRaises(ValueError):
                    build_dim_zip_filename(prefix, sku, part, "Product")

    def test_generated_filename_passes_the_official_shape_validator(self):
        filename = build_dim_zip_filename("D3X", "42", 99, "Temple Ruins")
        self.assertEqual(
            validate_dim_zip_filename(filename),
            "D3X00000042-99_TempleRuins.zip",
        )
        for invalid in (
            "3DX00000042-01_Product.zip",
            "RO00000000-01_Product.zip",
            "RO00000042-00_Product.zip",
            "RO00000042-01_Product.exe",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_dim_zip_filename(invalid)

    def test_support_segment_replaces_dot_and_preserves_hyphen_and_underscore(self):
        self.assertEqual(
            sanitize_support_filename_segment("Bull_Boxers G8-8.1"),
            "Bull_Boxers_G8-8_1",
        )

    def test_renderotica_support_cover_filename_matches_dim_support_basename(self):
        self.assertEqual(
            build_support_cover_filename(
                "Renderotica",
                "70127",
                "Bull Boxers G8M G8.1M G9",
            ),
            "Renderotica_70127_Bull_Boxers_G8M_G8_1M_G9.jpg",
        )

    def test_daz_support_cover_filename_matches_observed_dim_support_basename(self):
        self.assertEqual(
            build_support_cover_filename(
                "DAZ 3D",
                "163838",
                "X Fashion - Series 3 for G8-8.1 Females",
            ),
            "DAZ_3D_163838_X_Fashion_-_Series_3_for_G8-8_1_Females.jpg",
        )


if __name__ == "__main__":
    unittest.main()
