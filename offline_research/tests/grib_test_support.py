"""Portable ecCodes test-message construction.

Some native ecCodes builds expose definitions but omit the optional sample
directory.  The embedded 212-byte GRIB2 message is a regular 4x3 surface grid
created by ecCodes itself; tests still mutate and decode it through the native
library instead of skipping decoder coverage.
"""
from __future__ import annotations

import base64


_REGULAR_LL_SFC_GRIB2 = base64.b64decode(
    "R1JJQgAAAAIAAAAAAAAA1AAAABUBAAcAAgIBAQfpAQUAAAAABAAAAEgDAAAAAAwAAAAABgAA"
    "AAAAAAAAAAAAAAAAAAQAAAADAAAAAP////8CFg7ADk4cADAB94pADnviwAAPQkAAD0JAAAA"
    "AACQEAAAAAgAABABrAAAAAQAAAAlnAAAAAAL/AAAAAAAAHgAAADEFAAAADAADQ42TMwAAAAA"
    "AAAEAYljRmgAAAAAAAAABAAAAAAAMAQAAAAwAAgEAAAAGBv8AAAAIBwAAADc3Nzc="
)


def new_regular_ll_sfc_grib2(eccodes):
    """Return the native sample, or an equivalent embedded message."""
    try:
        return eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    except eccodes.CodesInternalError:
        return eccodes.codes_new_from_message(_REGULAR_LL_SFC_GRIB2)
