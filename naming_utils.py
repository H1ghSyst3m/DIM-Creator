import re


DIM_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,6}$")
DAZ_RESERVED_PREFIXES = frozenset({"IM", "DZ", "DAZ", "DAZ3D", "TAFI"})
DIM_ZIP_FILENAME_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z][A-Z0-9]{0,6})"
    r"(?P<sku>[0-9]{8})-(?P<part>[0-9]{2})_"
    r"(?P<name>[A-Za-z0-9]+)\.zip$"
)


def sanitize_dim_zip_product_name(product_name: str, fallback: str = "Package") -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "", str(product_name))
    fallback_sanitized = re.sub(r"[^A-Za-z0-9]+", "", str(fallback))
    return sanitized or fallback_sanitized or "Package"


def sanitize_support_filename_segment(value: str, fallback: str = "") -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    fallback_sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(fallback)).strip("_")
    return sanitized or fallback_sanitized


def validate_dim_prefix(prefix: str, fallback: str | None = None) -> str:
    if not isinstance(prefix, str):
        raise ValueError("DIM prefix must be text.")
    value = str(prefix).strip().upper()
    if not value and fallback is not None:
        if not isinstance(fallback, str):
            raise ValueError("DIM fallback prefix must be text.")
        value = str(fallback).strip().upper()
    if not DIM_PREFIX_PATTERN.fullmatch(value):
        raise ValueError(
            "DIM prefix must start with a letter and contain 1-7 uppercase "
            "letters or digits."
        )
    return value


def validate_dim_sku(sku: str | int) -> int:
    value = str(sku).strip()
    if not re.fullmatch(r"[0-9]{1,8}", value):
        raise ValueError("DIM SKU must contain 1-8 digits.")
    numeric = int(value)
    if not 1 <= numeric <= 99_999_999:
        raise ValueError("DIM SKU must be between 1 and 99999999.")
    return numeric


def validate_dim_part(product_part: str | int) -> int:
    if isinstance(product_part, bool):
        raise ValueError("DIM package part must be between 1 and 99.")
    value = str(product_part).strip()
    if not re.fullmatch(r"[0-9]{1,2}", value):
        raise ValueError("DIM package part must contain 1-2 digits.")
    numeric = int(value)
    if not 1 <= numeric <= 99:
        raise ValueError("DIM package part must be between 1 and 99.")
    return numeric


def format_dim_sku(sku: str | int) -> str:
    return f"{validate_dim_sku(sku):08d}"


def build_product_store_idx(
    prefix: str,
    sku: str | int,
    product_part: str | int,
) -> str | None:
    """Return the unencoded DAZ store ID, or defer custom IDs to DIM.

    DAZ documents that third-party source prefixes are encoded in this field,
    but does not publish the encoding. Omitting it for custom prefixes lets DIM
    derive the source-aware value from the validated package filename.
    """
    if validate_dim_prefix(prefix) not in DAZ_RESERVED_PREFIXES:
        return None
    return f"{validate_dim_sku(sku)}-{validate_dim_part(product_part)}"


def build_dim_zip_filename(
    prefix: str,
    sku: str | int,
    product_part: str | int,
    product_name: str,
    fallback_prefix: str = "LOCAL",
) -> str:
    prefix_clean = validate_dim_prefix(prefix, fallback=fallback_prefix)
    sku_formatted = format_dim_sku(sku)
    part_str = f"{validate_dim_part(product_part):02d}"
    name_segment = sanitize_dim_zip_product_name(product_name)
    filename = f"{prefix_clean}{sku_formatted}-{part_str}_{name_segment}.zip"
    if len(filename) > 255:
        raise ValueError("DIM package filename exceeds the Windows filename limit.")
    return filename


def validate_dim_zip_filename(filename: str) -> str:
    value = str(filename)
    match = DIM_ZIP_FILENAME_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("Invalid DIM package filename.")
    validate_dim_prefix(match.group("prefix"))
    validate_dim_sku(match.group("sku"))
    validate_dim_part(match.group("part"))
    return value


def build_support_cover_filename(
    store: str,
    sku: str | int,
    product_name: str,
) -> str:
    store_segment = sanitize_support_filename_segment(store)
    product_segment = sanitize_support_filename_segment(product_name, "Package")
    sku_segment = str(validate_dim_sku(sku))
    filename = f"{store_segment}_{sku_segment}_{product_segment}.jpg"
    if len(filename) > 255:
        raise ValueError("Support cover filename exceeds the Windows filename limit.")
    return filename
