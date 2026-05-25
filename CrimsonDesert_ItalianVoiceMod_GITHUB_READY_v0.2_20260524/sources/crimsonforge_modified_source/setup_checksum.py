"""Build script for the C PaChecksum extension.

Built with ``py_limited_api=True`` so a single ``.pyd`` (named
``_pa_checksum.cp311-abi3-win_amd64.pyd``) loads on every CPython
3.11+. Without this the resulting binary is locked to one specific
Python version (``cp314-win_amd64`` for the build host), Blender's
bundled interpreter is a different version, the import silently
fails and ``checksum_engine.py`` falls back to its 100x-slower
pure-Python implementation — i.e. the 247-second PAZ-checksum
bottleneck on the in-place repack flow.

The C code uses only Stable-ABI calls (``PyArg_ParseTuple``,
``Py_buffer`` / ``PyBuffer_Release``, ``PyLong_FromUnsignedLong``,
``PyModuleDef``, ``PyModule_Create``), all available since Python
3.7 — pinning the floor at 3.11 covers every shipping Blender and
the standalone Forge equally.
"""

from setuptools import setup, Extension

setup(
    name="pa_checksum",
    ext_modules=[
        Extension(
            "core._pa_checksum",
            sources=["core/_pa_checksum.c"],
            py_limited_api=True,
            define_macros=[("Py_LIMITED_API", "0x030B0000")],
        ),
    ],
)
