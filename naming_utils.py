import re


def sanitize_dim_zip_product_name(product_name: str, fallback: str = "Package") -> str:
    sanitized = re.sub(r'[^A-Za-z0-9]+', '', str(product_name))
    return sanitized or fallback


def sanitize_support_filename_segment(value: str, fallback: str = "") -> str:
    sanitized = re.sub(r'[^A-Za-z0-9_-]+', '_', str(value)).strip('_')
    return sanitized or fallback


def format_dim_sku(sku: str) -> str:
    try:
        return f"{int(str(sku)):08d}"
    except ValueError:
        return str(sku).zfill(8)


def build_dim_zip_filename(
    prefix: str,
    sku: str,
    product_part: int,
    product_name: str,
    fallback_prefix: str = "IM",
) -> str:
    prefix_clean = re.sub(r'[^A-Za-z0-9]+', '', str(prefix)).upper() or fallback_prefix
    sku_formatted = format_dim_sku(sku)
    part_str = f"{int(product_part):02d}"
    name_segment = sanitize_dim_zip_product_name(product_name)
    return f"{prefix_clean}{sku_formatted}-{part_str}_{name_segment}.zip"


def build_support_cover_filename(store: str, sku: str, product_name: str) -> str:
    store_segment = sanitize_support_filename_segment(store)
    product_segment = sanitize_support_filename_segment(product_name, "Package")
    sku_segment = str(sku).strip()
    return f"{store_segment}_{sku_segment}_{product_segment}.jpg"
