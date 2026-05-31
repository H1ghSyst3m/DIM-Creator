import unittest

from naming_utils import (
    build_dim_zip_filename,
    build_support_cover_filename,
    sanitize_dim_zip_product_name,
    sanitize_support_filename_segment,
)


class NamingUtilsTests(unittest.TestCase):
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
